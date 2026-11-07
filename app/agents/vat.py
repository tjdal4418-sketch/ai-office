"""🧾 배트 — 세금계산서 대사·불공제 후보·부가세 신고서 요약 에이전트.

작업 순서(BaseAgent.run 이 고정): 입력 검증 → 계산 → 이상항목 리포트 → 결과 엑셀 → 검토필요.
핵심 원칙: 불공제 여부와 가산세 부과 여부는 AI 가 확정하지 않는다. '후보'와 '위험'을 찾아 보여주고,
후보는 담당자가 '판단' 칸을 바꾸기 전까지 불공제로 보는(보수적) 신고서 요약을 만들며 반대 가정의 금액도 같이 보여준다.
"""
import asyncio
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from app.agents import vat_calc as vc
from app.agents.bookkeeping_calc import read_rows
from app.agents.base import BaseAgent, ValidationError
from app.core import clients, config, db
from app.core.rules import RuleMissing, Rules, load_rules

SHEET_REC, SHEET_DIFF, SHEET_ND = "대사결과", "불일치 상세", "불공제 후보"
SHEET_SUM, SHEET_BASIS = "신고서 요약", "계산근거"

HEAD_FILL = PatternFill("solid", fgColor="DDEBF7")
WARN_FILL = PatternFill("solid", fgColor="FFF2CC")
ERR_FILL = PatternFill("solid", fgColor="F8CBAD")
MONEY = "#,##0"
MAX_ANOMALY_ROWS = 40
REC_FIRST = 9   # 대사결과 시트: 목록 첫 데이터 행

NOT_REFLECTED = [
    "신용카드·현금영수증 매출, 영세율, 면세 공급가액은 입력이 없어 과세표준에 포함하지 않음",
    "공통매입세액 안분, 의제매입세액, 대손세액, 신고·납부 관련 가산세는 계산하지 않음",
    "신고기한의 주말·공휴일 순연 미반영, 법인 사업자(예정신고 대상) 기준으로 안내",
    "같은 거래가 세금계산서와 카드매입에 중복되어도 자동으로 걸러내지 않음",
    "불공제 후보는 키워드 단서일 뿐입니다. 판단 칸(확인필요/불공제/공제)은 담당자가 정합니다",
]


def _today() -> date:
    return date.today()


class VatAgent(BaseAgent):
    agent_id = "vat"
    name = "배트"
    icon = "🧾"
    role = "VAT Reconciliation"
    summary = "세금계산서 대사, 불공제 매입세액 후보 플래그, 부가세 신고서 요약, 발행 관리"
    task_name = "부가세 대사"

    # ------------------------------------------------------------------
    # 1) 입력 검증
    # ------------------------------------------------------------------
    async def validate(self, ctx: dict) -> None:
        await super().validate(ctx)  # 오류 상태 시연(simulate_error)
        try:
            rules = load_rules(ctx["tax_year"], ("vat",))
            ctx["rules"] = rules
            rate = rules.d("vat.standard_rate")
            ctx["tolerance"] = int(rules.v("vat.matching.amount_tolerance"))
        except (RuleMissing, OSError) as e:
            raise ValidationError(f"{ctx['tax_year']}년 부가세 규칙을 읽지 못했습니다: {e}")
        raw = ctx["params"].get("today")
        ctx["today"] = date.fromisoformat(raw) if raw else _today()
        files = self._resolve_inputs(ctx)
        ctx["files"] = files
        for role, path in files.items():
            self.log(ctx, f"입력 파일({vc.ROLE_LABELS[role]}): {path.name}")

        issues: list[dict] = []
        rows, m = read_rows(files["sales"], vc.INVOICE_COLUMNS)
        sales, i1 = vc.validate_invoices(rows, m, vc.SALES, rate)
        rows, m = read_rows(files["purchase"], vc.INVOICE_COLUMNS)
        purchases, i2 = vc.validate_invoices(rows, m, vc.PURCHASE, rate)
        rows, m = read_rows(files["ledger"], vc.LEDGER_COLUMNS)
        ledger, i3 = vc.validate_ledger(rows, m)
        cards, i4 = [], []
        if "card" in files:
            rows, m = read_rows(files["card"], vc.CARD_COLUMNS)
            cards, i4 = vc.validate_cards(rows, m)
        issues = i1 + i2 + i3 + i4

        errors = [i for i in issues if i["severity"] == "error"]
        if errors:
            raise ValidationError(f"입력 오류 {len(errors)}건 — {errors[0]['message']}", issues)

        invoice_dates = [i.write_date for i in sales + purchases]
        period = vc.detect_period(invoice_dates)
        if period[0] != ctx["tax_year"]:
            raise ValidationError(f"세금계산서 작성일의 연도({period[0]})가 선택한 적용연도({ctx['tax_year']})와 다릅니다")
        start, end = vc.period_bounds(period)
        notes = []
        for label, items, field in (("매출 세금계산서", sales, "write_date"), ("매입 세금계산서", purchases, "write_date"),
                                    ("장부", ledger, "date"), ("카드매입", cards, "date")):
            for it in items:
                if not (start <= getattr(it, field) <= end):
                    notes.append(vc.issue("warning", "기간 외 거래", f"{getattr(it, field)} 건이 과세기간({start}~{end}) 밖입니다 — 대사에는 포함됨",
                                          f"{label} 행 {it.row}"))
        ctx.update(sales=sales, purchases=purchases, ledger=ledger, cards=cards, period=period,
                   input_notes=notes + [i for i in issues if i["severity"] != "error"])
        self.progress(ctx, 5, f"입력 검증 완료 (매출 {len(sales)}건, 매입 {len(purchases)}건, 장부 {len(ledger)}건, 카드 {len(cards)}건)")

    def _resolve_inputs(self, ctx: dict) -> dict[str, Path]:
        """역할별 입력 파일: 업로드(최신순, 파일 이름에 역할 키워드) → 샘플 폴더. 다른 고객사 이름이 든 파일은 제외."""
        others = [c for c in clients.load_clients() if c["id"] != ctx["client_id"]]
        found: dict[str, Path] = {}
        folder = config.UPLOAD_DIR / "vat"
        if folder.exists():
            ups = sorted((p for p in folder.iterdir() if p.suffix.lower() in (".csv", ".xlsx") and not p.name.startswith("~$")),
                         key=lambda p: p.stat().st_mtime, reverse=True)
            for p in ups:
                role = vc.detect_role(p.name)
                if role and role not in found and not any(c["id"] in p.name or c["name"] in p.name for c in others):
                    found[role] = p
        sample = config.SAMPLE_DIR / "vat" / ctx["client_id"]
        if sample.exists():
            for p in sorted(sample.iterdir()):
                role = vc.detect_role(p.name)
                if role and role not in found:
                    found[role] = p
        for role in vc.REQUIRED_ROLES:
            if role not in found:
                key = {"sales": "매출", "purchase": "매입", "ledger": "장부"}[role]
                raise ValidationError(f"입력 파일이 없습니다: {vc.ROLE_LABELS[role]} — 에이전트 상세 화면에서 "
                                      f"파일 이름에 '{key}' 가 들어가게 올려 주세요")
        return found

    # ------------------------------------------------------------------
    # 2) 계산
    # ------------------------------------------------------------------
    async def compute(self, ctx: dict) -> None:
        rules: Rules = ctx["rules"]
        try:
            total_inv = len(ctx["sales"]) + len(ctx["purchases"])
            self.progress(ctx, 15, f"매출 세금계산서 {len(ctx['sales'])}건 ↔ 장부 대사 중")
            sales_res = vc.reconcile(ctx["sales"], ctx["ledger"], vc.SALES, ctx["tolerance"])
            await asyncio.sleep(config.CALC_DELAY)
            self.progress(ctx, 30, f"매입 세금계산서 {len(ctx['purchases'])}건 ↔ 장부 대사 중")
            purchase_res = vc.reconcile(ctx["purchases"], ctx["ledger"], vc.PURCHASE, ctx["tolerance"])
            await asyncio.sleep(config.CALC_DELAY)
            ctx["sales_results"], ctx["purchase_results"] = sales_res, purchase_res
            ctx["results"] = sales_res + purchase_res

            self.progress(ctx, 50, "불공제 매입세액 후보 점검 중")
            ctx["candidates"] = vc.find_candidates(purchase_res, ctx["cards"], rules)
            await asyncio.sleep(config.CALC_DELAY)

            self.progress(ctx, 65, "부가세 신고서 요약 집계 중")
            ctx["summary"] = vc.summarize(ctx["results"], ctx["candidates"], ctx["cards"])
            ctx["card_ineligible"] = [c for c in ctx["cards"] if not vc.card_eligible(c)]
            await asyncio.sleep(config.CALC_DELAY)

            self.progress(ctx, 80, "세금계산서 발행 관리·가산세 위험 점검 중")
            ctx["issuance"] = vc.issuance_review(sales_res, rules, ctx["today"])
            ctx["deadline"] = vc.period_deadline(rules.base_dir, ctx["period"], ctx["today"])
            ctx["upcoming"] = self._upcoming(ctx)
            ctx["counts"] = {s: sum(1 for r in ctx["results"] if r["status"] == s) for s in vc.STATUSES}
            ctx["total_invoices"] = total_inv
        except RuleMissing as e:
            raise ValidationError(f"규칙 값 미확인: {e}")
        except (OSError, KeyError, ValueError) as e:
            raise ValidationError(f"신고기한 규칙을 읽지 못했습니다: {e}")

    @staticmethod
    def _upcoming(ctx: dict) -> list[dict]:
        from app.core import deadlines
        return [u for u in deadlines.upcoming(ctx["today"], limit=50, home_only=False) if u["id"] in ("vat_prelim", "vat_final")][:3]

    # ------------------------------------------------------------------
    # 3) 이상항목 리포트
    # ------------------------------------------------------------------
    async def report_anomalies(self, ctx: dict) -> None:
        self.progress(ctx, 96, "이상항목 점검 중")
        found: list[dict] = list(ctx["input_notes"])
        # 매출의 세금계산서누락(미발행 의심)은 아래 '발행 관리' 항목으로 따로 알리므로 여기서는 제외
        mism = [r for r in ctx["results"] if r["status"] != vc.MATCH and not (r["kind"] == vc.SALES and r["status"] == vc.NO_INVOICE)]
        for r in mism[:MAX_ANOMALY_ROWS]:
            base = r["invoice"] or r["ledger"]
            name = base.name or vc.fmt_biz(base.biz)
            ref = f"{r['kind']} {'세금계산서' if r['invoice'] else '장부'} 행 {base.row}"
            label = r["status"] if r["status"] != vc.NO_INVOICE else (
                "세금계산서누락(미발행 의심)" if r["kind"] == vc.SALES else "세금계산서누락(미수취)")
            amount = (r["invoice"] or r["ledger"]).supply
            found.append(vc.issue("warning", label, f"{vc.rec_date(r)} {name} 공급가액 {amount:,}원 — {r['note']}", ref))
        if len(mism) > MAX_ANOMALY_ROWS:
            found.append(vc.issue("info", "불일치", f"그 외 {len(mism) - MAX_ANOMALY_ROWS}건은 불일치 상세 시트 참고", SHEET_DIFF))
        for c in ctx["candidates"][:MAX_ANOMALY_ROWS]:
            found.append(vc.issue("warning", "불공제 후보",
                                  f"{c['date']} {c['name']} {c['item']} 세액 {c['tax']:,}원 — {', '.join(c['types'])} "
                                  f"(키워드 {', '.join(c['keywords'])}) · 담당자 판단 필요", f"{c['source']} · {c['ref']}"))
        for x in ctx["issuance"]:
            found.append(vc.issue("warning", x["type"], f"{x['date']} {x['name']} 공급가액 {x['supply']:,}원 — {x['note']}", x["ref"]))
        if ctx["card_ineligible"]:
            tot = sum(c.total for c in ctx["card_ineligible"])
            found.append(vc.issue("info", "카드매입 공제 불가", f"가맹점 사업자번호 또는 세액이 없는 카드 사용 {len(ctx['card_ineligible'])}건 ({tot:,}원)은 공제 대상에서 제외", "카드매입 내역"))
        d = ctx["deadline"]
        if d:
            tag = "검증 전" if not d["verified"] else "검증됨"
            if d["d_day"] < 0:
                found.append(vc.issue("warning", "신고기한", f"{vc.period_label(ctx['period'])} 신고기한 {d['date']} 이(가) {-d['d_day']}일 지났습니다 — 신고·납부 완료 여부 확인 ({tag})", "app/rules/2026/deadlines.yaml"))
            else:
                found.append(vc.issue("info", "신고기한", f"{vc.period_label(ctx['period'])} 신고기한 {d['date']} (D-{d['d_day']}) ({tag})", "app/rules/2026/deadlines.yaml"))
        unverified = ctx["rules"].unverified()
        found.append(vc.issue("warning", "법령 수치 검증 전", f"부가세 규칙 {len(unverified)}건이 verified: false — 법령 MCP로 검증 전까지 참고용", "app/rules/2026/vat.yaml"))
        ctx["anomalies"] = found
        db.add_anomalies(ctx["task_id"], found)

    def rules_warnings(self, ctx: dict) -> list[str]:
        out = ["불공제 후보·가산세 위험은 키워드·규칙 기반 안내이며 최종 판단은 담당자가 합니다."]
        rules = ctx.get("rules")
        if rules and rules.unverified():
            out.append(f"부가세 규칙 {len(rules.unverified())}건이 검증 전(verified: false)입니다 — 법령 MCP로 확인 필요")
        return out

    async def request_review(self, ctx: dict) -> None:
        await super().request_review(ctx)
        c, s = ctx["counts"], ctx["summary"]
        kind = "납부" if s["payable"] >= 0 else "환급"
        db.update_task(ctx["task_id"], result=(
            f"대사 {len(ctx['results'])}건 (일치 {c[vc.MATCH]} · 금액차이 {c[vc.DIFF]} · 장부누락 {c[vc.NO_LEDGER]} · 세금계산서누락 {c[vc.NO_INVOICE]})"
            f" · 불공제 후보 {len(ctx['candidates'])}건 · {kind}세액 {abs(s['payable']):,}원(후보 불공제 가정)"))

    # ------------------------------------------------------------------
    # 4) 결과 엑셀 (계산 셀은 수식)
    # ------------------------------------------------------------------
    def build_workbook(self, ctx: dict) -> Workbook:
        wb = Workbook()
        ws_rec = wb.active
        ws_rec.title = SHEET_REC
        ws_diff = wb.create_sheet(SHEET_DIFF)
        ws_nd = wb.create_sheet(SHEET_ND)
        ws_sum = wb.create_sheet(SHEET_SUM)
        ws_basis = wb.create_sheet(SHEET_BASIS)
        ref = self._write_basis(ws_basis, ctx)
        rec_last = self._write_rec(ws_rec, ctx)
        self._write_diff(ws_diff, ctx, ref)
        nd_last = self._write_nd(ws_nd, ctx)
        self._write_summary(ws_sum, ctx, rec_last, nd_last)
        return wb

    @staticmethod
    def _head(ws, row: int, labels: list[str]) -> None:
        for c, label in enumerate(labels, 1):
            cell = ws.cell(row=row, column=c, value=label)
            cell.font, cell.fill = Font(bold=True), HEAD_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    @staticmethod
    def _widths(ws, widths: list[int]) -> None:
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    def _write_rec(self, ws, ctx: dict) -> int:
        n = len(ctx["results"])
        last = max(REC_FIRST, REC_FIRST + n - 1)
        ws.cell(row=1, column=1, value=f"세금계산서 vs 장부 대사 — {vc.period_label(ctx['period'])}").font = Font(bold=True, size=12)
        ws.cell(row=2, column=1, value="기준: 사업자번호 + 공급가액 + 작성일 (세액이 다르거나 공급가액만 다르면 금액차이)")
        self._head(ws, 3, ["구분", *vc.STATUSES, "합계"])
        for r, kind in ((4, vc.SALES), (5, vc.PURCHASE)):
            ws.cell(row=r, column=1, value=kind).font = Font(bold=True)
            for c in range(2, 6):
                col = get_column_letter(c)
                ws.cell(row=r, column=c, value=f"=COUNTIFS($A${REC_FIRST}:$A${last},$A{r},$B${REC_FIRST}:$B${last},{col}$3)")
            ws.cell(row=r, column=6, value=f"=SUM(B{r}:E{r})")
        ws.cell(row=6, column=1, value="합계").font = Font(bold=True)
        for c in range(2, 7):
            col = get_column_letter(c)
            ws.cell(row=6, column=c, value=f"=SUM({col}4:{col}5)").font = Font(bold=True)
        self._head(ws, REC_FIRST - 1, ["구분", "상태", "사업자번호", "거래처", "작성일(세금계산서)", "공급가액(세금계산서)", "세액(세금계산서)",
                                       "작성일(장부)", "공급가액(장부)", "세액(장부)", "공급가액 차이", "세액 차이", "비고"])
        for k, r in enumerate(ctx["results"]):
            row = REC_FIRST + k
            i, l = r["invoice"], r["ledger"]
            base = i or l
            ws.cell(row=row, column=1, value=r["kind"])
            ws.cell(row=row, column=2, value=r["status"])
            ws.cell(row=row, column=3, value=vc.fmt_biz(base.biz))
            ws.cell(row=row, column=4, value=base.name)
            if i:
                ws.cell(row=row, column=5, value=i.write_date).number_format = "yyyy-mm-dd"
                ws.cell(row=row, column=6, value=i.supply)
                ws.cell(row=row, column=7, value=i.tax)
            if l:
                ws.cell(row=row, column=8, value=l.date).number_format = "yyyy-mm-dd"
                ws.cell(row=row, column=9, value=l.supply)
                ws.cell(row=row, column=10, value=l.tax)
            if i and l:
                ws.cell(row=row, column=11, value=f"=F{row}-I{row}")
                ws.cell(row=row, column=12, value=f"=G{row}-J{row}")
            ws.cell(row=row, column=13, value=r["note"])
            for c in (6, 7, 9, 10, 11, 12):
                ws.cell(row=row, column=c).number_format = MONEY
            if r["status"] != vc.MATCH:
                ws.cell(row=row, column=2).fill = WARN_FILL
        if not ctx["results"]:
            ws.cell(row=REC_FIRST, column=1, value="(대사할 건 없음)")
        self._widths(ws, [8, 14, 15, 18, 16, 16, 14, 14, 16, 14, 14, 12, 70])
        ws.freeze_panes = f"A{REC_FIRST}"
        return last

    def _write_diff(self, ws, ctx: dict, ref: dict) -> None:
        bold = Font(bold=True)
        ws.cell(row=1, column=1, value="A. 불일치 항목 (금액차이 · 장부누락 · 세금계산서누락)").font = Font(bold=True, size=12)
        self._head(ws, 2, ["구분", "상태", "사업자번호", "거래처", "작성일", "공급가액(세금계산서)", "공급가액(장부)", "차이", "확인 안내"])
        # 매출의 세금계산서누락(미발행 의심)은 아래 '발행 관리' 항목으로 따로 알리므로 여기서는 제외
        mism = [r for r in ctx["results"] if r["status"] != vc.MATCH and not (r["kind"] == vc.SALES and r["status"] == vc.NO_INVOICE)]
        for k, r in enumerate(mism):
            row = 3 + k
            i, l = r["invoice"], r["ledger"]
            base = i or l
            vals = [r["kind"], r["status"], vc.fmt_biz(base.biz), base.name, vc.rec_date(r), i.supply if i else None,
                    l.supply if l else None, f"=F{row}-G{row}" if i and l else None, r["note"]]
            for c, v in enumerate(vals, 1):
                if v is not None:
                    ws.cell(row=row, column=c, value=v)
            ws.cell(row=row, column=5).number_format = "yyyy-mm-dd"
            for c in (6, 7, 8):
                ws.cell(row=row, column=c).number_format = MONEY
            ws.cell(row=row, column=2).fill = WARN_FILL
        if not mism:
            ws.cell(row=3, column=1, value="(불일치 없음)")
        start = 3 + max(len(mism), 1) + 2
        ws.cell(row=start, column=1, value="B. 세금계산서 발행 관리 — 미발행·지연발행 의심 매출과 가산세 위험 (위험액은 참고용, 부과 여부는 담당자·세무서 판단)").font = Font(bold=True, size=12)
        self._head(ws, start + 1, ["구분", "작성일", "거래처", "사업자번호", "공급가액", "발급기한", "발급일", "기한 초과일수", "가산세율", "가산세 위험액", "비고"])
        first = start + 2
        for k, x in enumerate(ctx["issuance"]):
            row = first + k
            rate_ref = ref["penalty.late_issue_rate"] if x["type"] == "지연발행 의심" else ref["penalty.unissued_rate"]
            vals = [x["type"], x["date"], x["name"], x["biz"], x["supply"], x["due"], x["issue_date"], x["days"], f"={rate_ref}",
                    f"=IF(H{row}>0,ROUND(E{row}*I{row},0),0)", x["note"]]
            for c, v in enumerate(vals, 1):
                if v is not None:
                    ws.cell(row=row, column=c, value=v)
            for c in (2, 6, 7):
                ws.cell(row=row, column=c).number_format = "yyyy-mm-dd"
            ws.cell(row=row, column=5).number_format = MONEY
            ws.cell(row=row, column=9).number_format = "0.0%"
            ws.cell(row=row, column=10).number_format = MONEY
            ws.cell(row=row, column=1).fill = WARN_FILL
        n = len(ctx["issuance"])
        if n:
            tot = first + n
            ws.cell(row=tot, column=1, value="가산세 위험액 합계").font = bold
            ws.cell(row=tot, column=10, value=f"=SUM(J{first}:J{tot - 1})").number_format = MONEY
            ws.cell(row=tot, column=10).font = bold
        else:
            ws.cell(row=first, column=1, value="(미발행·지연발행 의심 없음)")
        ws.cell(row=first + max(n, 1) + 2, column=1,
                value="※ 기한 초과일수가 0 이하(아직 기한 전)이면 가산세 위험액은 0으로 표시됩니다. 카드·현금영수증 매출, 면세 등 세금계산서 발행 대상이 아닌 건은 제외하세요.")
        self._widths(ws, [14, 12, 20, 15, 16, 14, 14, 14, 10, 16, 70])

    def _write_nd(self, ws, ctx: dict) -> int:
        self._head(ws, 1, ["출처", "일자", "거래처", "사업자번호", "품목/적요", "계정과목", "공급가액", "세액", "후보 유형", "걸린 키워드", "판단", "참조"])
        for k, c in enumerate(ctx["candidates"]):
            row = 2 + k
            vals = [c["source"], c["date"], c["name"], c["biz"], c["item"], c["account"], c["supply"], c["tax"],
                    ", ".join(c["types"]), ", ".join(c["keywords"]), "확인필요", c["ref"]]
            for col, v in enumerate(vals, 1):
                ws.cell(row=row, column=col, value=v)
            ws.cell(row=row, column=2).number_format = "yyyy-mm-dd"
            for col in (7, 8):
                ws.cell(row=row, column=col).number_format = MONEY
            ws.cell(row=row, column=11).fill = WARN_FILL
        n = len(ctx["candidates"])
        last = max(2, 1 + n)
        if n == 0:
            ws.cell(row=2, column=1, value="(불공제 후보 없음)")
        else:
            dv = DataValidation(type="list", formula1='"확인필요,불공제,공제"', allow_blank=False)
            ws.add_data_validation(dv)
            dv.add(f"K2:K{last}")
        tot = last + 1
        ws.cell(row=tot, column=1, value="합계").font = Font(bold=True)
        for col in (7, 8):
            letter = get_column_letter(col)
            c = ws.cell(row=tot, column=col, value=f"=SUM({letter}2:{letter}{last})")
            c.number_format, c.font = MONEY, Font(bold=True)
        ws.cell(row=tot + 2, column=1, value="※ '판단' 칸을 '공제'로 바꾸면 신고서 요약에서 해당 세액이 공제 매입세액으로 옮겨집니다. '확인필요'·'불공제'는 불공제로 계산합니다.")
        self._widths(ws, [10, 12, 18, 15, 28, 14, 14, 12, 26, 22, 10, 26])
        ws.freeze_panes = "A2"
        return last

    def _write_summary(self, ws, ctx: dict, rec_last: int, nd_last: int) -> None:
        s, bold = ctx["summary"], Font(bold=True)
        R = f"'{SHEET_REC}'!"
        A, F, G, I = (f"{R}${c}${REC_FIRST}:${c}${rec_last}" for c in "AFGI")
        N = f"'{SHEET_ND}'!"
        nA, nH, nK = (f"{N}${c}$2:${c}${nd_last}" for c in "AHK")
        d = ctx["deadline"]
        ws.cell(row=1, column=1, value=f"부가가치세 신고서 요약 — {ctx['client_name']} · {vc.period_label(ctx['period'])}").font = Font(bold=True, size=12)
        if d:
            ws.cell(row=2, column=1, value=f"신고기한 ({d['name']})")
            ws.cell(row=2, column=2, value=d["date"]).number_format = "yyyy-mm-dd"
            tag = "검증 전" if not d["verified"] else "검증됨"
            ws.cell(row=2, column=3, value=(f"D-{d['d_day']}" if d["d_day"] >= 0 else f"D+{-d['d_day']} (기한 경과)") + f" · 기한 규칙 {tag}")
            if d["d_day"] < 0:
                ws.cell(row=2, column=3).fill = ERR_FILL
        ws.cell(row=3, column=1, value="기준일(작업일)")
        ws.cell(row=3, column=2, value=ctx["today"]).number_format = "yyyy-mm-dd"
        up = ctx["upcoming"]
        ws.cell(row=4, column=1, value="다가오는 부가세 기한")
        ws.cell(row=4, column=2, value=", ".join(f"{u['name']} {u['date']}(D-{u['d_day']})" for u in up) or "-")
        rows = [
            (6, "[매출]", None, None, True),
            (7, "과세표준 (매출 세금계산서 공급가액)", f'=SUMIFS({F},{A},"{vc.SALES}")', "카드·현금영수증 매출, 영세율·면세는 미포함", False),
            (8, "매출세액", f'=SUMIFS({G},{A},"{vc.SALES}")', "", False),
            (9, "(참고) 장부 매출 공급가액", f'=SUMIFS({I},{A},"{vc.SALES}")', "", False),
            (10, "장부 대비 과세표준 차이", "=B7-B9", "0이 아니면 대사결과·불일치 상세 확인", False),
            (12, "[매입 — 세금계산서]", None, None, True),
            (13, "매입 공급가액", f'=SUMIFS({F},{A},"{vc.PURCHASE}")', "", False),
            (14, "매입세액 합계", f'=SUMIFS({G},{A},"{vc.PURCHASE}")', "", False),
            (15, "불공제 후보 세액 (세금계산서)", f'=SUMIFS({nH},{nA},"세금계산서",{nK},"<>공제")', "판단이 '공제'가 아닌 후보 — 불공제 후보 시트", False),
            (16, "공제 매입세액 (세금계산서)", "=B14-B15", "", False),
            (18, "[매입 — 카드]", None, None, True),
            (19, "카드매입 세액 (공제 요건 충족분)", s["card_tax"], "가맹점 사업자번호·세액이 있는 건만", False),
            (20, "불공제 후보 세액 (카드)", f'=SUMIFS({nH},{nA},"카드",{nK},"<>공제")', "", False),
            (21, "카드매입 공제분", "=B19-B20", "", False),
            (23, "[납부(환급)세액]", None, None, True),
            (24, "납부(환급)세액 = 매출세액 − 공제 매입세액 − 카드매입 공제분", "=B8-B16-B21", "후보를 불공제로 본 값(보수적)", False),
            (25, "구분", '=IF(B24>=0,"납부세액","환급세액")', "", False),
            (26, "(참고) 후보를 모두 공제로 판정할 경우", "=B24-B15-B20", "판단 칸을 바꾸면 위 값들이 함께 바뀝니다", False),
        ]
        for r, label, val, note, header in rows:
            c = ws.cell(row=r, column=1, value=label)
            if header:
                c.font, c.fill = bold, HEAD_FILL
                ws.cell(row=r, column=2).fill = HEAD_FILL
                ws.cell(row=r, column=3).fill = HEAD_FILL
                continue
            v = ws.cell(row=r, column=2, value=val)
            v.number_format = MONEY
            if note:
                ws.cell(row=r, column=3, value=note)
            if r in (24, 25):
                c.font, v.font = bold, bold
        ws.cell(row=28, column=1, value="신고 전 확인").font = bold
        for k, t in enumerate(NOT_REFLECTED):
            ws.cell(row=29 + k, column=1, value=f"- {t}")
        self._widths(ws, [62, 20, 60])

    def _write_basis(self, ws, ctx: dict) -> dict:
        rules: Rules = ctx["rules"]
        meta = [("에이전트", f"{self.icon} {self.name}"), ("고객사", ctx["client_name"]),
                ("과세기간", vc.period_label(ctx["period"])), ("적용연도(기준연도)", ctx["tax_year"]),
                ("입력 파일", ", ".join(f"{vc.ROLE_LABELS[r]}={p.name}" for r, p in ctx["files"].items())),
                ("생성시각", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                ("상태", "AI 작성본 — 담당자의 검토 승인 전")]
        for k, v in meta:
            ws.append([k, v])
            ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        for w in self.rules_warnings(ctx):
            ws.append(["경고", w])
            for c in (1, 2):
                ws.cell(row=ws.max_row, column=c).fill = WARN_FILL
        ws.append([])
        ws.append(["적용 규칙 (rules/vat.yaml) — 이 값을 고치면 가산세 위험액 등이 다시 계산됩니다"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        self._head(ws, ws.max_row + 1, ["항목(rules 경로)", "값", "근거 법령/고시", "적용연도", "검증", "확인일"])
        ref = {}
        for path, it in rules.items():
            val = it["value"]
            ws.append([path, ", ".join(val) if isinstance(val, list) else val, it.get("source", ""), it.get("year"),
                       "검증됨" if it.get("verified") else "검증 전", it.get("verified_on") or ""])
            ref[path.removeprefix("vat.")] = f"'{SHEET_BASIS}'!$B${ws.max_row}"
            if not it.get("verified"):
                ws.cell(row=ws.max_row, column=5).fill = WARN_FILL
        ws.append([])
        ws.append(["이번 단계에서 반영하지 않은 항목"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        for t in NOT_REFLECTED:
            ws.append(["-", t])
        self._widths(ws, [42, 60, 56, 10, 10, 12])
        return ref
