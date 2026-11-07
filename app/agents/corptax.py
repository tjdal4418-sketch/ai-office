"""🏛️ 코택 — 법인세 세무조정 점검 에이전트.

북키가 만든 합계잔액시산표(결과물)를 이어받아 세무조정 체크리스트·기업업무추진비 한도·가지급금 인정이자·대손충당금 한도를 점검한다.
핵심 원칙: 한도 초과·인정이자는 '검토 후보와 추정치'이며 세무조정의 확정은 담당자가 한다.
작업 순서(BaseAgent.run 이 고정): 입력 검증 → 계산 → 이상항목 리포트 → 결과 엑셀 → 검토필요.
"""
import asyncio
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from app.agents import corptax_calc as cc
from app.agents.base import BaseAgent, ValidationError
from app.agents.payroll_calc import issue
from app.core import clients, config, db
from app.core.rules import RuleMissing, Rules, load_rules

SHEET_REVIEW, SHEET_LIMIT, SHEET_AUDIT = "세무조정 검토표", "한도 계산", "요청자료 목록"
SHEET_TB, SHEET_BASIS = "시산표(입력)", "계산근거"

HEAD_FILL = PatternFill("solid", fgColor="DDEBF7")
WARN_FILL = PatternFill("solid", fgColor="FFF2CC")
ERR_FILL = PatternFill("solid", fgColor="F8CBAD")
MONEY = "#,##0"

NOT_REFLECTED = [
    "특정 수입금액·문화기업업무추진비 가산, 증빙 미수취 손금불산입 등 기업업무추진비 세부 규정은 계산하지 않음",
    "가지급금 인정이자: 업무무관 판단, 수령 이자·차입금 이자율 선택(가중평균 vs 당좌대출)은 미반영 — 추정치",
    "대손충당금: 대손실적률은 입력이 없어 고정 설정률만 사용, 감가상각 상각범위액·기부금 한도·퇴직급여충당금 한도는 계산하지 않음(수기 검토)",
    "시산표가 월 단위(북키 결과)이면 사업연도 누계가 아니므로 한도 판정은 누계 시산표로 다시 확인",
    "최종 세무조정(손금불산입·익금산입, 소득처분)은 담당자가 확정합니다",
]


class CorpTaxAgent(BaseAgent):
    agent_id = "corptax"
    name = "코택"
    icon = "🏛️"
    role = "Corporate Tax & Other Tax"
    summary = "법인세 세무조정 체크리스트, 기업업무추진비 한도 계산, 세무조사 대응 자료 목록"
    task_name = "세무조정 점검"

    # ------------------------------------------------------------------
    # 1) 입력 검증
    # ------------------------------------------------------------------
    async def validate(self, ctx: dict) -> None:
        await super().validate(ctx)  # 오류 상태 시연(simulate_error)
        try:
            rules = load_rules(ctx["tax_year"], ("corptax",))
            ctx["rules"] = rules
            ctx["items_def"] = cc.load_items(rules.base_dir, "tax_adjustment_items.yaml")
            ctx["audit_def"] = cc.load_items(rules.base_dir, "audit_request_template.yaml")
        except (OSError, RuleMissing) as e:
            raise ValidationError(f"{ctx['tax_year']}년 법인세 규칙을 읽지 못했습니다: {e}")
        p = ctx["params"]
        ctx["is_sme"] = bool(p.get("is_sme", True))
        try:
            ctx["months"] = int(p.get("months", 12))
            raw_rev = p.get("revenue")
            ctx["revenue_input"] = None if raw_rev in (None, "") else int(raw_rev)
        except (TypeError, ValueError):
            raise ValidationError("사업연도 개월수·수입금액은 숫자여야 합니다")
        if not 1 <= ctx["months"] <= 12:
            raise ValidationError(f"사업연도 개월수는 1~12 이어야 합니다 ({ctx['months']})")
        if ctx["revenue_input"] is not None and ctx["revenue_input"] < 0:
            raise ValidationError("수입금액은 0 이상이어야 합니다")

        tb, notes, label = self._resolve_tb(ctx)
        ctx["tb"], ctx["tb_label"] = tb, label
        self.log(ctx, f"입력: {label}")
        ctx["input_notes"] = notes
        self.progress(ctx, 5, f"입력 검증 완료 (시산표 계정 {len(tb)}개)")

    def _resolve_tb(self, ctx: dict) -> tuple[list[cc.TbRow], list[dict], str]:
        """시산표 입력 우선순위: ① 업로드한 시산표 파일 ② 북키 작업 결과(같은 고객사·적용연도) ③ 샘플 파일."""
        others = [c for c in clients.load_clients() if c["id"] != ctx["client_id"]]
        folder = config.UPLOAD_DIR / "corptax"
        if folder.exists():
            ups = sorted((p for p in folder.iterdir() if p.suffix.lower() in (".csv", ".xlsx") and not p.name.startswith("~$")),
                         key=lambda p: p.stat().st_mtime, reverse=True)
            for p in ups:
                if any(k in p.name.lower() for k in cc.TB_KEYWORDS) and not any(c["id"] in p.name or c["name"] in p.name for c in others):
                    tb, issues = cc.read_tb_file(p)
                    errors = [i for i in issues if i["severity"] == "error"]
                    if errors:
                        raise ValidationError(f"입력 오류 {len(errors)}건 — {errors[0]['message']}", issues)
                    return tb, [], f"업로드한 시산표 {p.name}"
        up = cc.find_upstream(ctx["client_id"], ctx["tax_year"])
        if up:
            task, data = up
            ctx["upstream_task_id"] = task["id"]
            draft = task["status"] == "review"
            notes = [issue("info", "북키 연동", f"북키 작업 #{task['id']}의 시산표를 이어받았습니다 — "
                           + ("아직 담당자 검토 승인 전 초안입니다" if draft else "승인 완료된 결과입니다"), f"북키 작업 #{task['id']}")]
            y, m = data["period"]
            notes.append(issue("warning", "월 단위 시산표", f"북키 시산표는 {y}-{m:02d} 한 달 분입니다 — 기업업무추진비 등 한도 판정은 사업연도 누계 시산표로 다시 확인하세요",
                               f"북키 작업 #{task['id']}"))
            return cc.tb_from_bookkeeping(data), notes, f"북키 작업 #{task['id']} 시산표 ({'검토 승인 전 초안' if draft else '승인 완료'}, {y}-{m:02d})"
        sample = config.SAMPLE_DIR / "corptax" / ctx["client_id"] / "trial_balance.csv"
        if sample.exists():
            tb, issues = cc.read_tb_file(sample)
            errors = [i for i in issues if i["severity"] == "error"]
            if errors:
                raise ValidationError(f"입력 오류 {len(errors)}건 — {errors[0]['message']}", issues)
            notes = [issue("info", "샘플 시산표", "북키 결과가 없어 샘플 시산표(사업연도 누계)를 사용했습니다 — 북키를 먼저 실행하면 그 결과를 이어받습니다", sample.name)]
            return tb, notes, f"샘플 시산표 {sample.name}"
        raise ValidationError("시산표가 없습니다 — 북키를 먼저 실행하거나, 파일 이름에 '시산표'가 들어가게 올려 주세요")

    # ------------------------------------------------------------------
    # 2) 계산
    # ------------------------------------------------------------------
    async def compute(self, ctx: dict) -> None:
        rules: Rules = ctx["rules"]
        try:
            self.progress(ctx, 20, "계정별 잔액 집계 중")
            await asyncio.sleep(config.CALC_DELAY)
            tb_revenue = cc.revenue_from_tb(ctx["tb"])
            if ctx["revenue_input"] is not None:
                revenue, rev_source = ctx["revenue_input"], "사용자 입력"
            else:
                revenue, rev_source = tb_revenue, "시산표 매출 계정 합계"
            ctx["revenue"], ctx["revenue_source"] = revenue, rev_source
            self.progress(ctx, 45, "기업업무추진비 한도 계산 중")
            await asyncio.sleep(config.CALC_DELAY)
            self.progress(ctx, 70, "세무조정 항목 검토 필요 여부 판정 중")
            ctx["review"] = cc.build_review(ctx["tb"], rules, ctx["items_def"], ctx["is_sme"], ctx["months"], revenue)
            await asyncio.sleep(config.CALC_DELAY)
            ctx["counts"] = {s: sum(1 for i in ctx["review"]["items"] if i["status"] == s)
                             for s in (cc.STATUS_ADJUST, cc.STATUS_REVIEW, cc.STATUS_NONE)}
        except RuleMissing as e:
            raise ValidationError(f"규칙 값 미확인: {e}")

    # ------------------------------------------------------------------
    # 3) 이상항목 리포트 + 체크 항목 저장
    # ------------------------------------------------------------------
    async def report_anomalies(self, ctx: dict) -> None:
        self.progress(ctx, 96, "이상항목 점검 중")
        found: list[dict] = list(ctx["input_notes"])
        rv = ctx["review"]
        if ctx["revenue_input"] is None and ctx["revenue"] == 0:
            found.append(issue("warning", "수입금액 없음", "시산표에 매출 계정이 없어 수입금액을 0원으로 계산했습니다 — 수입금액을 입력하면 기업업무추진비 한도가 늘어납니다", "한도 계산"))
        for it in rv["items"]:
            if it["status"] == cc.STATUS_ADJUST:
                found.append(issue("warning", "조정 필요", f"{it['title']}: {it['summary']}", f"세무조정 검토표 · {it['key']}"))
            elif it["status"] == cc.STATUS_REVIEW:
                found.append(issue("info", "검토 필요", f"{it['title']}: 잔액 {it['amount']:,}원 — {it['summary']}", f"세무조정 검토표 · {it['key']}"))
        unverified = ctx["rules"].unverified()
        found.append(issue("warning", "법령 수치 검증 전", f"법인세 규칙 {len(unverified)}건이 verified: false — 법령 MCP로 검증 전까지 참고용", "app/rules/2026/corptax.yaml"))
        ctx["anomalies"] = found
        db.add_anomalies(ctx["task_id"], found)

        checklist = [{"key": it["key"], "group": it["status"], "title": it["title"], "detail": it["point"],
                      "hint": f"[{it['status']}] {it['summary']}", "status": "open"} for it in rv["items"]]
        ready = {"tb": "준비중"}
        audit = [{"key": a["key"], "group": a["category"], "title": a["title"],
                  "detail": f"요청 {n:02d} · 담당: {a.get('owner', '')}", "status": ready.get(a.get("auto_ready") or "", "미준비")}
                 for n, a in enumerate(ctx["audit_def"], 1)]
        ctx["checklist"], ctx["audit"] = checklist, audit
        db.replace_work_items(ctx["task_id"], "checklist", checklist)
        db.replace_work_items(ctx["task_id"], "pbc", audit)

    def rules_warnings(self, ctx: dict) -> list[str]:
        out = ["한도 초과·인정이자는 검토 후보와 추정치이며, 세무조정의 확정은 담당자가 합니다."]
        rules = ctx.get("rules")
        if rules and rules.unverified():
            out.append(f"법인세 규칙 {len(rules.unverified())}건이 검증 전(verified: false)입니다 — 법령 MCP로 확인 필요")
        return out

    async def request_review(self, ctx: dict) -> None:
        await super().request_review(ctx)
        c, ent = ctx["counts"], ctx["review"]["ent"]
        ent_item = next(i for i in ctx["review"]["items"] if i["key"] == "entertainment")
        db.update_task(ctx["task_id"], result=(
            f"세무조정 항목 {len(ctx['review']['items'])}개 중 조정 필요 {c[cc.STATUS_ADJUST]} · 검토 필요 {c[cc.STATUS_REVIEW]} · 해당 없음 {c[cc.STATUS_NONE]}"
            f" · 기업업무추진비 한도 {ent['limit']:,}원 (계상 {ent_item['amount']:,}원)"))

    # ------------------------------------------------------------------
    # 4) 결과 엑셀 — 세무조정 검토표 (계산 셀은 수식)
    # ------------------------------------------------------------------
    def build_workbook(self, ctx: dict) -> Workbook:
        wb = Workbook()
        ws_rv = wb.active
        ws_rv.title = SHEET_REVIEW
        ws_lim = wb.create_sheet(SHEET_LIMIT)
        ws_au = wb.create_sheet(SHEET_AUDIT)
        ws_tb = wb.create_sheet(SHEET_TB)
        ws_ba = wb.create_sheet(SHEET_BASIS)
        ref = self._write_basis(ws_ba, ctx)
        tb_last = self._write_tb(ws_tb, ctx)
        self._write_limit(ws_lim, ctx, ref, tb_last)
        self._write_review(ws_rv, ctx, tb_last)
        self._write_audit(ws_au, ctx)
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

    @staticmethod
    def _sumif(names: list[str], tb_last: int) -> str:
        """시산표(입력) 시트에서 계정과목 이름으로 정상방향 잔액을 합산하는 수식 조각."""
        if not names:
            return "0"
        parts = [f"SUMIF('{SHEET_TB}'!$B$2:$B${tb_last},\"{n}\",'{SHEET_TB}'!$G$2:$G${tb_last})" for n in names]
        return "+".join(parts)

    def _write_tb(self, ws, ctx: dict) -> int:
        self._head(ws, 1, ["계정코드", "계정과목", "구분", "정상잔액", "차변잔액", "대변잔액", "정상방향 잔액"])
        for i, t in enumerate(ctx["tb"]):
            r = i + 2
            ws.append([t.code, t.name, t.kind, t.normal, t.debit, t.credit, f'=IF(D{r}="차변",E{r}-F{r},F{r}-E{r})'])
            for c in (5, 6):
                ws.cell(row=r, column=c).number_format = "#,##0;-#,##0;"  # 0 은 비워 보이게
            ws.cell(row=r, column=7).number_format = MONEY
        last = len(ctx["tb"]) + 1
        ws.cell(row=last + 1, column=1, value="합계").font = Font(bold=True)
        for col in (5, 6):
            letter = get_column_letter(col)
            c = ws.cell(row=last + 1, column=col, value=f"=SUM({letter}2:{letter}{last})")
            c.number_format, c.font = MONEY, Font(bold=True)
        ws.cell(row=last + 2, column=1, value="차대 일치 검증").font = Font(bold=True)
        ws.cell(row=last + 2, column=2, value=f'=IF(E{last + 1}=F{last + 1},"일치","불일치")').font = Font(bold=True)
        ws.cell(row=last + 4, column=1, value=f"출처: {ctx['tb_label']}")
        self._widths(ws, [10, 18, 8, 10, 16, 16, 16])
        ws.freeze_panes = "C2"
        return last

    def _write_limit(self, ws, ctx: dict, ref: dict, tb_last: int) -> None:
        R = lambda k: ref[f"corptax.{k}"]  # noqa: E731
        bold = Font(bold=True)
        S = lambda names: self._sumif(names, tb_last)  # noqa: E731

        def put(row, label, val, note="", fmt=MONEY, strong=False):
            c = ws.cell(row=row, column=1, value=label)
            v = ws.cell(row=row, column=2, value=val)
            v.number_format = fmt
            if note:
                ws.cell(row=row, column=3, value=note)
            if strong:
                c.font, v.font = bold, bold

        ws.cell(row=1, column=1, value="기업업무추진비 한도 계산 (중소기업 여부는 B3 을 바꾸면 다시 계산)").font = Font(bold=True, size=12)
        put(3, "중소기업 여부", "중소기업" if ctx["is_sme"] else "일반기업", "목록에서 선택", "@")
        dv = DataValidation(type="list", formula1='"중소기업,일반기업"', allow_blank=False)
        ws.add_data_validation(dv)
        dv.add("B3")
        put(4, "사업연도 개월수", ctx["months"], "기본한도를 개월 수로 환산", "0")
        put(5, "기본한도(연)", f'=IF(B3="중소기업",{R("entertainment.basic_limit_sme")},{R("entertainment.basic_limit_general")})')
        put(6, "기본한도(개월 환산)", "=TRUNC(ROUND(B5*B4/12,6))")
        put(7, "수입금액", ctx["revenue"], f"출처: {ctx['revenue_source']}")
        t2, t3 = R("entertainment.tier2_over"), R("entertainment.tier3_over")
        r1, r2, r3 = (R(f"entertainment.tier{i}_rate") for i in (1, 2, 3))
        put(8, "수입금액 기준 한도", f"=TRUNC(ROUND(MIN(B7,{t2})*{r1}+MAX(0,MIN(B7,{t3})-{t2})*{r2}+MAX(0,B7-{t3})*{r3},6))", "구간별 적용률 합산")
        put(9, "기업업무추진비 한도 합계", "=B6+B8", strong=True)
        put(10, "기업업무추진비 계상액 (시산표)", "=" + S(["기업업무추진비", "접대비"]), "계정과목이 '접대비'로 남은 고객사도 합산")
        put(11, "한도초과 예상액 (손금불산입 검토)", "=MAX(0,B10-B9)", "확정 아님 — 담당자 판단", strong=True)
        put(12, "한도 여유", "=MAX(0,B9-B10)")
        ws.cell(row=14, column=1, value="가지급금 인정이자 추정").font = Font(bold=True, size=12)
        put(15, "가지급금 잔액", "=" + S(["가지급금"]))
        put(16, "적용 이자율(연)", f"={R('imputed_interest.rate')}", "", "0.0%")
        put(17, "개월수", "=B4", "", "0")
        put(18, "인정이자 추정액", "=TRUNC(ROUND(B15*B16*B17/12,6))", "수령 이자 차감 전·업무무관 판단 전 추정치", strong=True)
        ws.cell(row=20, column=1, value="대손충당금 한도 추정").font = Font(bold=True, size=12)
        put(21, "채권잔액 (외상매출금+미수금)", "=" + S(["외상매출금", "미수금"]))
        put(22, "설정률", f"={R('bad_debt.fixed_rate')}", "대손실적률은 입력이 없어 미반영", "0.0%")
        put(23, "한도", "=TRUNC(ROUND(B21*B22,6))")
        put(24, "대손충당금 잔액", "=" + S(["대손충당금"]))
        put(25, "한도초과 예상액", "=MAX(0,B24-B23)", "", strong=True)
        ws.cell(row=27, column=1, value="업무용승용차 안내").font = Font(bold=True, size=12)
        put(28, "1대당 연간 감가상각비 한도", f"={R('vehicle.depreciation_limit')}")
        put(29, "운행기록부 작성 필요 비용 기준(1대당 연)", f"={R('vehicle.log_threshold')}")
        put(30, "차량유지비 잔액", "=" + S(["차량유지비"]))
        put(31, "차량운반구 잔액", "=" + S(["차량운반구"]))
        ws.cell(row=33, column=1, value=f"※ 시산표 출처: {ctx['tb_label']}. 월 단위 시산표라면 누계 시산표로 다시 확인하세요.")
        self._widths(ws, [46, 20, 60])

    def _write_review(self, ws, ctx: dict, tb_last: int) -> None:
        ws.cell(row=1, column=1, value=f"세무조정 검토표 — {ctx['client_name']} · {ctx['tax_year']}년 (AI 작성본 — 담당자의 검토 승인 전)").font = Font(bold=True, size=12)
        ws.cell(row=2, column=1, value=f"시산표 출처: {ctx['tb_label']} · {'중소기업' if ctx['is_sme'] else '일반기업'} · {ctx['months']}개월 · 수입금액 {ctx['revenue']:,}원({ctx['revenue_source']})")
        self._head(ws, 3, ["번호", "항목", "주 계정과목", "잔액", "검토 필요 여부", "계산·확인 요약", "예상 조정액", "근거 법령", "확인 포인트", "참고 계정 잔액", "검토 완료"])
        adj_ref = {"entertainment": f"='{SHEET_LIMIT}'!B11", "imputed": f"='{SHEET_LIMIT}'!B18", "bad_debt": f"='{SHEET_LIMIT}'!B25"}
        for n, it in enumerate(ctx["review"]["items"], 1):
            r = 3 + n
            adj = adj_ref.get(it.get("calc", "")) if it.get("calc") else None
            ws.append([n, it["title"], ", ".join(it["primary"]), "=" + self._sumif(it["primary"], tb_last), it["status"], it["summary"],
                       adj, it["basis"], it["point"], ", ".join(f"{k} {v:,}" for k, v in it["related_balances"]), "☐"])
            ws.cell(row=r, column=4).number_format = MONEY
            ws.cell(row=r, column=7).number_format = MONEY
            fill = ERR_FILL if it["status"] == cc.STATUS_ADJUST else WARN_FILL if it["status"] == cc.STATUS_REVIEW else None
            if fill:
                ws.cell(row=r, column=5).fill = fill
            for c in (6, 9, 10):
                ws.cell(row=r, column=c).alignment = Alignment(wrap_text=True, vertical="top")
            ws.cell(row=r, column=11).alignment = Alignment(horizontal="center")
        ws.append([])
        ws.append(["※ 검토 필요 여부는 생성 시점 판정이고, 예상 조정액은 '한도 계산' 시트와 수식으로 연결돼 있습니다. 완료 체크는 화면(작업 상세)에서도 할 수 있어요."])
        self._widths(ws, [6, 30, 22, 16, 14, 60, 16, 34, 60, 30, 9])
        ws.freeze_panes = "C4"

    def _write_audit(self, ws, ctx: dict) -> None:
        self._head(ws, 1, ["번호", "분류", "요청자료", "담당", "준비상태", "비고"])
        for n, it in enumerate(ctx["audit"], 1):
            ws.append([f"A-{n:02d}", it["group"], it["title"], it["detail"].split("담당: ")[-1], it["status"], ""])
        dv = DataValidation(type="list", formula1='"미준비,준비중,준비완료,제출완료"', allow_blank=False)
        ws.add_data_validation(dv)
        dv.add(f"E2:E{len(ctx['audit']) + 1}")
        ws.append([])
        ws.append(["※ 세무조사·외부감사 요청자료 템플릿입니다. 준비 상태는 화면(작업 상세)에서 관리하며 이 시트는 생성 시점 스냅샷입니다."])
        self._widths(ws, [8, 10, 56, 12, 12, 30])
        ws.freeze_panes = "A2"

    def _write_basis(self, ws, ctx: dict) -> dict:
        rules: Rules = ctx["rules"]
        meta = [("에이전트", f"{self.icon} {self.name}"), ("고객사", ctx["client_name"]), ("적용연도(기준연도)", ctx["tax_year"]),
                ("기업 구분", "중소기업" if ctx["is_sme"] else "일반기업"), ("사업연도 개월수", ctx["months"]),
                ("시산표 출처", ctx["tb_label"]), ("생성시각", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                ("상태", "AI 작성본 — 담당자의 검토 승인 전")]
        for k, v in meta:
            ws.append([k, v])
            ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        for w in self.rules_warnings(ctx):
            ws.append(["경고", w])
            for c in (1, 2):
                ws.cell(row=ws.max_row, column=c).fill = WARN_FILL
        ws.append([])
        ws.append(["적용 규칙 (rules/corptax.yaml) — 이 값을 고치면 한도 계산 시트가 다시 계산됩니다"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        self._head(ws, ws.max_row + 1, ["항목(rules 경로)", "값", "근거 법령/고시", "적용연도", "검증", "확인일"])
        ref = {}
        for path, it in rules.items():
            ws.append([path, it["value"], it.get("source", ""), it.get("year"), "검증됨" if it.get("verified") else "검증 전", it.get("verified_on") or ""])
            ref[path] = f"'{SHEET_BASIS}'!$B${ws.max_row}"
            if not it.get("verified"):
                ws.cell(row=ws.max_row, column=5).fill = WARN_FILL
        ws.append([])
        ws.append(["이번 단계에서 반영하지 않은 항목"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        for t in NOT_REFLECTED:
            ws.append(["-", t])
        self._widths(ws, [44, 30, 70, 10, 10, 12])
        return ref
