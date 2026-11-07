"""📒 북키 — 분개 추천·시산표·계정 Reconciliation·결산·PBC 에이전트.

작업 순서(BaseAgent.run 이 고정): 입력 검증 → 계산 → 이상항목 리포트 → 결과 엑셀 → 검토필요.
핵심 원칙: AI 는 '추천'까지. 신뢰도 낮음은 '확인필요'로 표시하고, 차대 불일치는 오류로 멈추며,
은행 잔액의 원인불명 차이는 숨기지 않고 별도 표시한다. 확정은 담당자의 검토 승인.
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from app.agents import bookkeeping_calc as bc
from app.agents import corptax_calc
from app.agents.base import BaseAgent, ValidationError
from app.core import clients, config, db
from app.core.rules import RuleMissing, Rules, load_rules

SHEET_JOURNAL, SHEET_TB, SHEET_RECON = "분개장", "시산표", "은행 Reconciliation"
SHEET_BS, SHEET_CHECK, SHEET_PBC, SHEET_BASIS = "BS 계정 검토", "결산 체크리스트", "PBC 리스트", "계산근거"

HEAD_FILL = PatternFill("solid", fgColor="DDEBF7")
WARN_FILL = PatternFill("solid", fgColor="FFF2CC")
ERR_FILL = PatternFill("solid", fgColor="F8CBAD")
MONEY = "#,##0"
MAX_ANOMALY_ROWS = 30  # 분개 '확인필요' 이상항목은 이 개수까지만 개별 표시

NOT_REFLECTED = [
    "카드 승인취소(환불) 건 — 금액이 0 이하이면 입력 오류로 처리",
    "부가세(매입세액) 분리 — 공급가액·세액 구분 없이 총액으로 분개 (배트 단계에서 연계 예정)",
    "외화 거래, 감가상각·선급비용 상각 분개 자동 생성(체크리스트로 안내만 함)",
    "결산 체크리스트·PBC 의 완료/준비 상태는 화면(작업 상세)에서 관리하며 이 엑셀은 생성 시점 스냅샷",
]


class BookkeepingAgent(BaseAgent):
    agent_id = "bookkeeping"
    name = "북키"
    icon = "📒"
    role = "Bookkeeping & Accounting"
    summary = "거래내역 분개 추천, 시산표, 계정 Reconciliation, 결산 체크리스트, 감사 PBC 리스트"
    task_name = "분개·시산표 작성"

    # ------------------------------------------------------------------
    # 1) 입력 검증
    # ------------------------------------------------------------------
    async def validate(self, ctx: dict) -> None:
        await super().validate(ctx)  # 오류 상태 시연(simulate_error)
        try:
            rules = load_rules(ctx["tax_year"], ("bookkeeping",))
            ctx["rules"] = rules
            ctx["mapping"] = bc.load_mapping(rules.base_dir)
            ctx["names"] = {k: rules.v(f"bookkeeping.accounts.{k}")
                            for k in ("bank", "card_payable", "unclassified_out", "unclassified_in")}
        except (RuleMissing, OSError) as e:
            raise ValidationError(f"{ctx['tax_year']}년 북키 규칙을 읽지 못했습니다: {e}")
        files = self._resolve_inputs(ctx)
        ctx["files"] = files
        for role, path in files.items():
            self.log(ctx, f"입력 파일({bc.ROLE_LABELS[role]}): {path.name}")

        issues: list[dict] = []
        chart_rows, m = bc.read_rows(files["chart"], bc.CHART_COLUMNS)
        chart, i1 = bc.validate_chart(chart_rows, m, list(ctx["names"].values()))
        issues += i1
        opening, bank, lag, cards = [], [], [], []
        if not i1:  # 계정과목표가 정상일 때만 전기말 잔액의 계정 코드를 대조할 수 있다
            o_rows, m = bc.read_rows(files["opening"], bc.OPENING_COLUMNS)
            opening, i2 = bc.validate_opening(o_rows, m, chart)
            issues += i2
        b_rows, m = bc.read_rows(files["bank"], bc.BANK_COLUMNS)
        bank, i3 = bc.validate_bank(b_rows, m)
        issues += i3
        if "lag" in files:
            l_rows, m = bc.read_rows(files["lag"], bc.LAG_COLUMNS)
            lag, i4 = bc.validate_bank(l_rows, m, source=bc.SRC_LAG, need_balance=False)
            issues += i4
        if "card" in files:
            c_rows, m = bc.read_rows(files["card"], bc.CARD_COLUMNS)
            cards, i5 = bc.validate_card(c_rows, m)
            issues += i5

        errors = [i for i in issues if i["severity"] == "error"]
        if errors:
            raise ValidationError(f"입력 오류 {len(errors)}건 — {errors[0]['message']}", issues)

        period, notes = bc.detect_period(bank + lag + cards)
        if period[0] != ctx["tax_year"]:
            raise ValidationError(f"거래 귀속월({period[0]}-{period[1]:02d})이 선택한 적용연도({ctx['tax_year']})와 다릅니다")
        ctx.update(chart=chart, opening=opening, bank=bank, lag=lag, cards=cards, period=period,
                   input_notes=notes + [i for i in issues if i["severity"] != "error"])
        self.progress(ctx, 5, f"입력 검증 완료 (은행 {len(bank)}건, 시차 {len(lag)}건, 카드 {len(cards)}건)")

    def _resolve_inputs(self, ctx: dict) -> dict[str, Path]:
        """역할별 입력 파일: 업로드(최신순, 파일 이름에 역할 키워드) → 샘플 폴더. 다른 고객사 이름이 든 파일은 제외."""
        others = [c for c in clients.load_clients() if c["id"] != ctx["client_id"]]
        found: dict[str, Path] = {}
        folder = config.UPLOAD_DIR / "bookkeeping"
        if folder.exists():
            ups = sorted((p for p in folder.iterdir() if p.suffix.lower() in (".csv", ".xlsx") and not p.name.startswith("~$")),
                         key=lambda p: p.stat().st_mtime, reverse=True)
            for p in ups:
                role = bc.detect_role(p.name)
                if role and role not in found and not any(c["id"] in p.name or c["name"] in p.name for c in others):
                    found[role] = p
        sample = config.SAMPLE_DIR / "bookkeeping" / ctx["client_id"]
        if sample.exists():
            for p in sorted(sample.iterdir()):
                role = bc.detect_role(p.name)
                if role and role not in found:
                    found[role] = p
        for role in bc.REQUIRED_ROLES:
            if role not in found:
                key = {"bank": "은행", "chart": "계정", "opening": "전기"}[role]
                raise ValidationError(f"입력 파일이 없습니다: {bc.ROLE_LABELS[role]} — 에이전트 상세 화면에서 "
                                      f"파일 이름에 '{key}' 가 들어가게 올려 주세요")
        return found

    # ------------------------------------------------------------------
    # 2) 계산: 분개 추천 → 분개장 → 원장 → 시산표 → Reconciliation → BS 검토 → 체크리스트·PBC
    # ------------------------------------------------------------------
    async def compute(self, ctx: dict) -> None:
        rules: Rules = ctx["rules"]
        n = ctx["names"]
        try:
            accounts = {a.name: a for a in ctx["chart"]}
            txs = ctx["bank"] + ctx["lag"] + ctx["cards"]
            total = len(txs)
            step = max(1, total // 20)
            entries: list[dict] = []
            for start in range(0, total, step):
                chunk = txs[start:start + step]
                entries += bc.make_entries(chunk, accounts, ctx["mapping"], n["bank"], n["card_payable"],
                                           n["unclassified_out"], n["unclassified_in"])
                done = start + len(chunk)
                self.progress(ctx, 5 + int(60 * done / total), f"거래 {total}건 중 {done}건 분개 추천 중")
                await asyncio.sleep(config.CALC_DELAY)
            ctx["entries"] = bc.finalize_entries(entries)
            ctx["lines"] = bc.journal_lines(ctx["entries"])

            self.progress(ctx, 70, "총계정원장·합계잔액시산표 집계 중")
            led = bc.build_ledger(ctx["chart"], ctx["opening"], ctx["lines"])
            tb = bc.trial_balance(led)
            ctx["ledger"], ctx["tb"] = led, tb
            if not tb["balanced"]:
                t = tb["totals"]
                raise ValidationError(
                    f"시산표 차대 불일치 — 합계 차변 {t['total_dr']:,}원 ≠ 대변 {t['total_cr']:,}원 "
                    f"(차이 {t['total_dr'] - t['total_cr']:+,}원)")

            self.progress(ctx, 80, "은행 Reconciliation 중")
            bank_code = accounts[n["bank"]].code
            ctx["bank_code"] = bank_code
            ctx["recon"] = bc.reconcile_bank(ctx["bank"], ctx["lag"], led, bank_code, int(rules.v("bookkeeping.recon.tolerance")))
            ctx["period_end"] = bc.period_end(ctx["period"])
            ctx["unpaid_cards"] = bc.unpaid_cards(ctx["cards"], ctx["period_end"])

            self.progress(ctx, 88, "BS 계정 검토 중")
            origins = {o.code: o.origin_date for o in ctx["opening"] if o.origin_date}
            ctx["bs_rows"] = bc.review_bs(led, origins, list(rules.v("bookkeeping.bs_review.watch_accounts")),
                                          int(rules.v("bookkeeping.bs_review.stale_days")), ctx["period_end"])

            self.progress(ctx, 92, "결산 체크리스트·PBC 생성 중")
            ctx["checklist"] = bc.build_checklist(rules.base_dir, ctx["period"][1], self._hints(ctx))
            ready = {"bank": "준비완료", "chart": "준비완료", "opening": "준비완료", "tb": "준비중", "gl": "준비중"}
            if "card" in ctx["files"]:
                ready["card"] = "준비완료"
            ctx["pbc"] = bc.build_pbc(rules.base_dir, ready)
        except RuleMissing as e:
            raise ValidationError(f"규칙 값 미확인: {e}")

    def _hints(self, ctx: dict) -> dict:
        led, tb, rc = ctx["ledger"], ctx["tb"], ctx["recon"]
        t = tb["totals"]
        low = sum(1 for e in ctx["entries"] if e["status"] == bc.STATUS_CHECK)
        flags = sum(len(r["flags"]) for r in ctx["bs_rows"] if any(f[0] == "warning" for f in r["flags"]))
        by_name = {rec["account"].name: rec for rec in led.values()}

        def bal(name):
            rec = by_name.get(name)
            return bc.signed_balance(rec) if rec else 0

        dep_posted = by_name.get("감가상각비", {}).get("dr", 0)
        fixed = sum(bc.signed_balance(r) for r in led.values() if r["account"].name in ("비품", "차량운반구", "건물", "기계장치"))
        hints = {
            "tb": f"합계 차변 {t['total_dr']:,}원 / 대변 {t['total_cr']:,}원 — {'일치' if tb['balanced'] else '불일치'}",
            "low_conf": f"확인필요 분개 {low}건 (전체 {len(ctx['entries'])}건)" if low else "확인필요 분개 없음",
            "bank_rec": ("원인불명 차이 없음" if rc["matched"] else f"원인불명 차이 {rc['unexplained']:+,}원 — 규명 필요"),
            "card_unpaid": f"미결제 카드 사용 {len(ctx['unpaid_cards'])}건 · {sum(c.amount for c in ctx['unpaid_cards']):,}원",
            "depreciation": (f"유형자산 잔액 {fixed:,}원 — 당기 감가상각비 분개 " + ("있음" if dep_posted else "없음(계상 필요 여부 확인)")) if fixed else "",
            "prepaid": f"선급비용 잔액 {bal('선급비용'):,}원 — 상각 필요 여부 확인" if bal("선급비용") else "",
            "accrued": f"미지급비용 잔액 {bal('미지급비용'):,}원 — 귀속월 미지급분 반영 확인" if bal("미지급비용") else "",
            "bs_review": f"주의 플래그 계정 {flags}개" if flags else "주의 플래그 없음",
        }
        return hints

    # ------------------------------------------------------------------
    # 3) 이상항목 리포트 + 체크 항목 저장
    # ------------------------------------------------------------------
    async def report_anomalies(self, ctx: dict) -> None:
        self.progress(ctx, 96, "이상항목 점검 중")
        found: list[dict] = list(ctx["input_notes"])
        rc = ctx["recon"]
        # 분개 추천 '확인필요' (AI 가 확정하지 않은 항목)
        low = [e for e in ctx["entries"] if e["status"] == bc.STATUS_CHECK]
        for e in low[:MAX_ANOMALY_ROWS]:
            found.append(bc.issue("warning", "분개 확인필요",
                                  f"{e['date']} {e['memo']} {e['amount']:,}원 → {e['suggested']} 추천 ({e['reason']})",
                                  f"{e['source']} 행 {e['ref_row']}"))
        if len(low) > MAX_ANOMALY_ROWS:
            found.append(bc.issue("info", "분개 확인필요", f"그 외 {len(low) - MAX_ANOMALY_ROWS}건은 분개장 시트의 '상태' 열 참고", "분개장"))
        # 은행 Reconciliation
        if not rc["matched"]:
            found.append(bc.issue("error", "원인불명 차이",
                                  f"조정 후 은행잔액 {rc['adjusted_bank']:,}원 vs 장부 보통예금 {rc['book_end']:,}원 → 차이 {rc['unexplained']:+,}원 "
                                  f"(기초 잔액 차이 {rc['open_diff']:+,}원, 은행 내역 잔액 불연속 {rc['gap_sum']:+,}원)", SHEET_RECON))
        for g in rc["gaps"]:
            found.append(bc.issue("warning", "은행 잔액 불연속",
                                  f"{g['date']} '{g['memo']}' 행: 예상 잔액 {g['expected']:,}원 ≠ 실제 {g['actual']:,}원 ({g['gap']:+,}원) — 내역에 없는 변동",
                                  f"은행 거래내역 행 {g['row']}"))
        if ctx["lag"]:
            found.append(bc.issue("info", "시차 항목", f"장부 반영·은행 미반영 {len(ctx['lag'])}건 (순액 {rc['lag_net']:+,}원) — 은행 잔액 조정에 반영", SHEET_RECON))
        if ctx["unpaid_cards"]:
            found.append(bc.issue("info", "카드 미결제", f"결제 전 카드 사용 {len(ctx['unpaid_cards'])}건 · "
                                  f"{sum(c.amount for c in ctx['unpaid_cards']):,}원 (미지급금)", SHEET_RECON))
        # BS 계정
        for r in ctx["bs_rows"]:
            for sev, cat, text in r["flags"]:
                found.append(bc.issue(sev, cat, f"{r['account'].name}: {text}", f"BS 계정 검토 · {r['account'].code}"))
        unverified = ctx["rules"].unverified()
        if unverified:
            found.append(bc.issue("info", "규칙 검증 전", f"북키 설정 {len(unverified)}건이 verified: false (장기 미정리 기준일 등 임시값)", "app/rules/2026/bookkeeping.yaml"))
        ctx["anomalies"] = found
        db.add_anomalies(ctx["task_id"], found)
        db.replace_work_items(ctx["task_id"], "checklist", ctx["checklist"])
        db.replace_work_items(ctx["task_id"], "pbc", ctx["pbc"])

    def rules_warnings(self, ctx: dict) -> list[str]:
        out = ["계정과목 추천은 키워드 규칙 기반이며 신뢰도 '낮음'(확인필요)은 담당자가 확정해야 합니다. 규칙은 사무실 보정 대상(TODO)."]
        rules = ctx.get("rules")
        if rules and rules.unverified():
            out.append(f"북키 설정 {len(rules.unverified())}건이 검증 전(verified: false)입니다.")
        return out

    async def write_output(self, ctx: dict) -> None:
        await super().write_output(ctx)
        # 코택이 이어받을 시산표를 값으로 따로 저장(엑셀은 수식이라 값을 읽을 수 없음)
        tb = [{"code": r["account"].code, "name": r["account"].name, "kind": r["account"].kind, "normal": r["account"].normal,
               **{k: r[k] for k in ("open_dr", "open_cr", "dr", "cr", "total_dr", "total_cr", "bal_dr", "bal_cr")}}
              for r in ctx["tb"]["rows"]]
        payload = {"client_id": ctx["client_id"], "client_name": ctx["client_name"], "tax_year": ctx["tax_year"],
                   "period": list(ctx["period"]), "task_id": ctx["task_id"], "rows": tb}
        corptax_calc.sidecar_path(ctx["output_path"]).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    async def request_review(self, ctx: dict) -> None:
        await super().request_review(ctx)
        rc = ctx["recon"]
        low = sum(1 for e in ctx["entries"] if e["status"] == bc.STATUS_CHECK)
        diff = "은행 대사 일치" if rc["matched"] else f"은행 원인불명 차이 {rc['unexplained']:+,}원"
        db.update_task(ctx["task_id"], result=f"분개 {len(ctx['entries'])}건(확인필요 {low}) · 시산표 차대 일치 · {diff}")

    # ------------------------------------------------------------------
    # 4) 결과 엑셀 (계산 셀은 수식)
    # ------------------------------------------------------------------
    def build_workbook(self, ctx: dict) -> Workbook:
        wb = Workbook()
        ws_j = wb.active
        ws_j.title = SHEET_JOURNAL
        ws_tb = wb.create_sheet(SHEET_TB)
        ws_rc = wb.create_sheet(SHEET_RECON)
        ws_bs = wb.create_sheet(SHEET_BS)
        ws_ck = wb.create_sheet(SHEET_CHECK)
        ws_pb = wb.create_sheet(SHEET_PBC)
        ws_ba = wb.create_sheet(SHEET_BASIS)
        tol_ref = self._write_basis(ws_ba, ctx)
        last_j = self._write_journal(ws_j, ctx)
        tb_row = self._write_tb(ws_tb, ctx, last_j)
        self._write_recon(ws_rc, ctx, tb_row, tol_ref)
        self._write_bs(ws_bs, ctx, tb_row)
        self._write_checklist(ws_ck, ctx)
        self._write_pbc(ws_pb, ctx)
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

    def _write_journal(self, ws, ctx: dict) -> int:
        self._head(ws, 1, ["전표번호", "일자", "계정코드", "계정과목", "차변", "대변", "적요", "거래처", "출처", "신뢰도", "상태", "추천 근거"])
        for ln in ctx["lines"]:
            ws.append([ln["entry_no"], ln["date"], ln["code"], ln["name"], ln["debit"] or None, ln["credit"] or None,
                       ln["memo"], ln["party"], ln["source"], ln["confidence"], ln["status"], ln["reason"]])
            r = ws.max_row
            ws.cell(row=r, column=2).number_format = "yyyy-mm-dd"
            for c in (5, 6):
                ws.cell(row=r, column=c).number_format = MONEY
            if ln["status"] == bc.STATUS_CHECK:
                for c in (10, 11):
                    ws.cell(row=r, column=c).fill = WARN_FILL
        last = ws.max_row
        total = last + 1
        ws.cell(row=total, column=1, value="합계").font = Font(bold=True)
        for col in (5, 6):
            letter = get_column_letter(col)
            cell = ws.cell(row=total, column=col, value=f"=SUM({letter}2:{letter}{last})")
            cell.font, cell.number_format = Font(bold=True), MONEY
        ws.cell(row=total, column=7, value=f'=IF(E{total}=F{total},"차대 일치","차대 불일치")').font = Font(bold=True)
        self._widths(ws, [16, 12, 10, 14, 14, 14, 28, 18, 16, 8, 10, 52])
        ws.freeze_panes = "A2"
        return last

    def _write_tb(self, ws, ctx: dict, last_j: int) -> dict:
        self._head(ws, 1, ["계정코드", "계정과목", "구분", "전기이월 차변", "전기이월 대변", "당기 차변", "당기 대변",
                           "합계 차변", "합계 대변", "잔액 차변", "잔액 대변"])
        rows = {}
        jc, jd, jcr = f"'{SHEET_JOURNAL}'!$C$2:$C${last_j}", f"'{SHEET_JOURNAL}'!$E$2:$E${last_j}", f"'{SHEET_JOURNAL}'!$F$2:$F${last_j}"
        for i, rec in enumerate(ctx["tb"]["rows"]):
            r, a = i + 2, rec["account"]
            rows[a.code] = r
            ws.append([a.code, a.name, a.kind, rec["open_dr"], rec["open_cr"],
                       f"=SUMIF({jc},A{r},{jd})", f"=SUMIF({jc},A{r},{jcr})",
                       f"=D{r}+F{r}", f"=E{r}+G{r}", f"=MAX(0,H{r}-I{r})", f"=MAX(0,I{r}-H{r})"])
        last = len(rows) + 1
        total = last + 1
        ws.cell(row=total, column=1, value="합계").font = Font(bold=True)
        for col in range(4, 12):
            letter = get_column_letter(col)
            ws.cell(row=total, column=col, value=f"=SUM({letter}2:{letter}{last})").font = Font(bold=True)
        ws.cell(row=total + 1, column=1, value="차대 일치 검증").font = Font(bold=True)
        ws.cell(row=total + 1, column=2,
                value=f'=IF(AND(D{total}=E{total},F{total}=G{total},H{total}=I{total},J{total}=K{total}),"일치","불일치")').font = Font(bold=True)
        for row in ws.iter_rows(min_row=2, max_row=total, min_col=4, max_col=11):
            for cell in row:
                cell.number_format = MONEY
        self._widths(ws, [10, 18, 8] + [15] * 8)
        ws.freeze_panes = "C2"
        return rows

    def _write_recon(self, ws, ctx: dict, tb_row: dict, tol_ref: str) -> None:
        rc = ctx["recon"]
        t = tb_row[ctx["bank_code"]]
        bold = Font(bold=True)
        ws.cell(row=1, column=1, value="은행 Reconciliation — 은행 잔액 vs 장부 보통예금").font = Font(bold=True, size=12)
        lag, gaps, cards = rc["lag_items"], rc["gaps"], ctx["unpaid_cards"]
        lag_first = 17
        lag_last = lag_first + len(lag) - 1
        rows = [
            (3, "은행 기말 잔액 (거래내역 마지막 잔액)", rc["bank_end"]),
            (4, "(+/−) 시차 항목 순액 — 장부 반영·은행 미반영", f"=SUM(E{lag_first}:E{lag_last})" if lag else 0),
            (5, "조정 후 은행 잔액", "=B3+B4"),
            (6, "장부 보통예금 잔액 (시산표)", f"='{SHEET_TB}'!J{t}-'{SHEET_TB}'!K{t}"),
            (7, "원인불명 차이", "=B5-B6"),
            (8, "판정", f'=IF(ABS(B7)<={tol_ref},"일치","차이 있음 — 원인 확인 필요")'),
            (10, "참고) 원인 분해", None),
            (11, "기초 잔액 차이 (은행 기초 − 장부 전기말)", rc["open_diff"]),
            (12, "은행 내역 잔액 불연속 합계", rc["gap_sum"]),
            (13, "합계 (= 원인불명 차이와 같아야 함)", "=B11+B12"),
        ]
        for r, label, val in rows:
            ws.cell(row=r, column=1, value=label).font = bold if r in (5, 7, 8, 10) else Font()
            if val is not None:
                c = ws.cell(row=r, column=2, value=val)
                c.number_format = MONEY
                if r in (7, 8):
                    c.font = bold
                    if not rc["matched"]:
                        c.fill = ERR_FILL
        ws.cell(row=15, column=1, value="시차(미결제) 항목 — 장부에는 반영했고 은행에는 아직 없는 거래 (+입금 / −출금)").font = bold
        self._head(ws, 16, ["구분", "일자", "적요", "거래처", "금액(+입금/−출금)"])
        for i, t_ in enumerate(lag):
            r = lag_first + i
            ws.cell(row=r, column=1, value=t_.kind or "시차")
            ws.cell(row=r, column=2, value=t_.date).number_format = "yyyy-mm-dd"
            ws.cell(row=r, column=3, value=t_.memo)
            ws.cell(row=r, column=4, value=t_.party)
            ws.cell(row=r, column=5, value=t_.inflow - t_.outflow).number_format = MONEY
        if not lag:
            ws.cell(row=lag_first, column=1, value="(시차 항목 없음)")
        nxt = max(lag_last, lag_first) + 2
        ws.cell(row=nxt, column=1, value="은행 거래내역 잔액 불연속 — 원인불명 후보 (거래내역에 없는 변동)").font = bold
        self._head(ws, nxt + 1, ["행", "일자", "적요", "예상 잔액", "실제 잔액", "차이"])
        g0 = nxt + 2
        for i, g in enumerate(gaps):
            r = g0 + i
            for c, v in enumerate([g["row"], g["date"], g["memo"], g["expected"], g["actual"]], 1):
                ws.cell(row=r, column=c, value=v)
            ws.cell(row=r, column=2).number_format = "yyyy-mm-dd"
            ws.cell(row=r, column=6, value=f"=E{r}-D{r}")
            for c in (4, 5, 6):
                ws.cell(row=r, column=c).number_format = MONEY
                ws.cell(row=r, column=c).fill = WARN_FILL
        if not gaps:
            ws.cell(row=g0, column=1, value="(불연속 없음)")
        nxt = g0 + max(len(gaps), 1) + 1
        ws.cell(row=nxt, column=1, value="법인카드 미결제(결제 예정) 내역 — 장부 미지급금, 은행 미출금").font = bold
        self._head(ws, nxt + 1, ["사용일", "가맹점", "적요", "금액", "결제예정일"])
        c0 = nxt + 2
        for i, c_ in enumerate(cards):
            r = c0 + i
            ws.cell(row=r, column=1, value=c_.date).number_format = "yyyy-mm-dd"
            ws.cell(row=r, column=2, value=c_.party)
            ws.cell(row=r, column=3, value=c_.memo)
            ws.cell(row=r, column=4, value=c_.amount).number_format = MONEY
            ws.cell(row=r, column=5, value=c_.settle_date).number_format = "yyyy-mm-dd"
        if cards:
            ws.cell(row=c0 + len(cards), column=3, value="합계").font = bold
            ws.cell(row=c0 + len(cards), column=4, value=f"=SUM(D{c0}:D{c0 + len(cards) - 1})").number_format = MONEY
        else:
            ws.cell(row=c0, column=1, value="(미결제 카드 없음)")
        self._widths(ws, [52, 18, 28, 18, 20, 14])

    def _write_bs(self, ws, ctx: dict, tb_row: dict) -> None:
        self._head(ws, 1, ["계정코드", "계정과목", "구분", "정상잔액", "전기말 잔액", "기말 잔액", "증감", "점검 결과", "비고"])
        sev_fill = {"warning": WARN_FILL, "error": ERR_FILL}
        for i, row in enumerate(ctx["bs_rows"]):
            r, a = i + 2, row["account"]
            t = tb_row[a.code]
            sign = (lambda x, y: f"='{SHEET_TB}'!{x}{t}-'{SHEET_TB}'!{y}{t}") if a.normal == "차변" else \
                   (lambda x, y: f"='{SHEET_TB}'!{y}{t}-'{SHEET_TB}'!{x}{t}")
            flags = row["flags"]
            worst = "error" if any(f[0] == "error" for f in flags) else "warning" if any(f[0] == "warning" for f in flags) else ""
            ws.append([a.code, a.name, a.kind, a.normal, sign("D", "E"), sign("H", "I"), f"=F{r}-E{r}",
                       " / ".join(f"[{c}] {txt}" for _, c, txt in flags) if flags else "이상 없음", ""])
            for c in (5, 6, 7):
                ws.cell(row=r, column=c).number_format = MONEY
            if worst:
                ws.cell(row=r, column=8).fill = sev_fill[worst]
            ws.cell(row=r, column=8).alignment = Alignment(wrap_text=True, vertical="top")
        self._widths(ws, [10, 16, 8, 10, 16, 16, 14, 90, 20])
        ws.freeze_panes = "C2"

    def _write_checklist(self, ws, ctx: dict) -> None:
        self._head(ws, 1, ["주기", "항목", "설명", "자동 점검 힌트", "완료"])
        for it in ctx["checklist"]:
            ws.append([it["group"], it["title"], it["detail"], it["hint"], "☐"])
            ws.cell(row=ws.max_row, column=5).alignment = Alignment(horizontal="center")
        ws.append([])
        ws.append(["※ 완료 체크는 화면(에이전트 상세 > 작업 상세 > 결산 체크리스트)에서 관리합니다. 이 시트는 생성 시점 스냅샷입니다."])
        self._widths(ws, [8, 44, 60, 52, 8])
        ws.freeze_panes = "A2"

    def _write_pbc(self, ws, ctx: dict) -> None:
        self._head(ws, 1, ["번호", "분류", "요청자료", "담당", "준비상태", "비고"])
        for n, it in enumerate(ctx["pbc"], 1):
            owner = it["detail"].split("담당: ")[-1]
            ws.append([f"PBC-{n:02d}", it["group"], it["title"], owner, it["status"], ""])
        dv = DataValidation(type="list", formula1='"미준비,준비중,준비완료,제출완료"', allow_blank=False)
        ws.add_data_validation(dv)
        dv.add(f"E2:E{len(ctx['pbc']) + 1}")
        self._widths(ws, [10, 10, 56, 12, 12, 30])
        ws.freeze_panes = "A2"

    def _write_basis(self, ws, ctx: dict) -> str:
        rules: Rules = ctx["rules"]
        meta = [("에이전트", f"{self.icon} {self.name}"), ("고객사", ctx["client_name"]),
                ("귀속월", f"{ctx['period'][0]}-{ctx['period'][1]:02d}"), ("적용연도(기준연도)", ctx["tax_year"]),
                ("입력 파일", ", ".join(f"{bc.ROLE_LABELS[r]}={p.name}" for r, p in ctx["files"].items())),
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
        ws.append(["적용 설정 (rules/bookkeeping.yaml)"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        self._head(ws, ws.max_row + 1, ["항목", "값", "근거", "적용연도", "검증", "확인일"])
        tol_ref = ""
        for path, it in rules.items():
            val = it["value"]
            ws.append([path, ", ".join(val) if isinstance(val, list) else val, it.get("source", ""), it.get("year"),
                       "검증됨" if it.get("verified") else "검증 전", it.get("verified_on") or ""])
            if path == "bookkeeping.recon.tolerance":
                tol_ref = f"'{SHEET_BASIS}'!$B${ws.max_row}"
            if not it.get("verified"):
                ws.cell(row=ws.max_row, column=5).fill = WARN_FILL
        ws.append([])
        ws.append(["분개 추천 키워드 규칙 (rules/account_mapping.yaml) — 위에서부터 먼저 맞는 규칙 적용"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        self._head(ws, ws.max_row + 1, ["규칙", "키워드", "방향", "추천 계정", "신뢰도"])
        for rule in ctx["mapping"]:
            ws.append([rule["id"], ", ".join(rule["keywords"]), rule["direction"], rule["account"],
                       "높음" if rule["confidence"] == "high" else "낮음(확인필요)"])
        ws.append([])
        ws.append(["이번 단계에서 반영하지 않은 항목"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        for text in NOT_REFLECTED:
            ws.append(["-", text])
        self._widths(ws, [40, 50, 50, 14, 14, 12])
        return tol_ref
