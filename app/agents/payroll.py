"""💰 페이 — 급여·4대보험·원천세 에이전트.

작업 순서(BaseAgent.run 이 고정): 입력 검증 → 계산 → 이상항목 리포트 → 결과 엑셀 → 검토필요.
계산 로직은 payroll_calc.py(순수 함수), 법령 수치는 rules/<연도>/ 에서만 읽는다.
연말정산·퇴직소득은 이번 단계에서 구현하지 않고 coming_soon 메뉴만 둔다.
"""
import asyncio
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.agents import payroll_calc as pc
from app.agents.base import BaseAgent, ValidationError
from app.core import config, db
from app.core.rules import RuleMissing, Rules, load_rules

SHEET_PAYSLIP, SHEET_INSURANCE, SHEET_WITHHOLD = "급여명세", "4대보험", "원천세 집계"
SHEET_ANOMALY, SHEET_BASIS = "이상항목", "계산근거"

HEAD_FILL = PatternFill("solid", fgColor="DDEBF7")
WARN_FILL = PatternFill("solid", fgColor="FFF2CC")
MONEY = "#,##0"

# 이번 단계에서 반영하지 않은 항목 — '계산근거' 시트에 항상 표시
NOT_REFLECTED = [
    "입사·퇴사월 일할계산, 4대보험 취득·상실월 처리",
    "건강보험 보수월액 상·하한, 국민연금 외 기관별 소득월액 정정",
    "상여금의 간이세액 별도 계산(현재는 월 과세급여에 합산해 조회)",
    "8세 이상 자녀 구분(입력은 '20세 이하 자녀수'만 받음), 부양가족 요건 검증",
    "산재보험료(사업주 전액 부담) 및 임금채권부담금",
    "기타소득 필요경비 60%는 소득세법 제21조①7·8의2·9·15·19호 소득에만 해당 — 입력된 기타소득 전부에 적용하므로 소득 종류는 담당자가 확인",
    "연말정산·퇴직소득 — 추후 확장 메뉴(미구현)",
]


class PayrollAgent(BaseAgent):
    agent_id = "payroll"
    name = "페이"
    icon = "💰"
    role = "Payroll & Withholding Tax"
    summary = "급여 계산, 4대보험, 근로·사업·기타소득 원천세, 원천징수이행상황신고서 집계"
    task_name = "급여 계산"
    coming_soon = ["연말정산", "퇴직소득"]

    # ------------------------------------------------------------------
    # 1) 입력 검증
    # ------------------------------------------------------------------
    async def validate(self, ctx: dict) -> None:
        await super().validate(ctx)  # 오류 상태 시연(simulate_error) 처리
        try:
            ctx["rules"] = load_rules(ctx["tax_year"])
        except Exception as e:
            raise ValidationError(f"{ctx['tax_year']}년 규칙 파일을 읽지 못했습니다: {e}")
        path, wb = self._open_input(ctx)
        ctx["input_name"] = path.name
        self.log(ctx, f"입력 파일: {path.name}")

        info = pc.read_info(wb["정보"]) if "정보" in wb.sheetnames else {}
        ctx["period"] = self._period(ctx, info)
        if "급여대장" not in wb.sheetnames:
            raise ValidationError("'급여대장' 시트가 없습니다")

        rows, missing = pc.read_table(wb["급여대장"], pc.PAYROLL_COLUMNS)
        employees, issues = pc.validate_payroll(rows, missing)

        payees: list = []
        if "소득자지급내역" in wb.sheetnames:
            prow, pmissing = pc.read_table(wb["소득자지급내역"], pc.PAYEE_COLUMNS)
            payees, pissues = pc.validate_payees(prow, pmissing)
            issues += pissues
        ctx["prev_gross"] = None
        if "전월급여" in wb.sheetnames:
            prev_rows, _ = pc.read_table(wb["전월급여"], pc.PREV_COLUMNS)
            ctx["prev_gross"] = {str(r["emp_no"]).strip(): int(pc._to_number(r["gross"]) or 0)
                                 for _, r in prev_rows if r.get("emp_no") is not None}

        errors = [i for i in issues if i["severity"] == "error"]
        if errors:
            first = errors[0]["message"]
            raise ValidationError(f"입력 오류 {len(errors)}건 — {first}", issues)
        ctx["employees"], ctx["payees"] = employees, payees
        ctx["input_notes"] = [i for i in issues if i["severity"] != "error"] + ctx.pop("period_notes", [])
        self.progress(ctx, 5, f"입력 검증 완료 (급여 {len(employees)}명, 소득자 {len(payees)}건)")

    def _open_input(self, ctx: dict):
        """업로드 파일(최신순) → 샘플 순으로, 선택한 고객사와 맞는 첫 파일을 연다."""
        folder = config.UPLOAD_DIR / "payroll"
        files = []
        if folder.exists():
            files = sorted((p for p in folder.glob("*.xlsx") if not p.name.startswith("~$")),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        sample = config.SAMPLE_DIR / "payroll" / f"{ctx['client_id']}.xlsx"
        if sample.exists():
            files.append(sample)
        for path in files:
            try:
                wb = load_workbook(path, data_only=True)
            except Exception:
                continue  # 깨진 파일은 건너뜀
            info = pc.read_info(wb["정보"]) if "정보" in wb.sheetnames else {}
            client = info.get("고객사")
            if client and client != ctx["client_name"]:
                continue  # 다른 고객사 파일
            return path, wb
        raise ValidationError("입력 파일이 없습니다 — 에이전트 상세 화면에서 급여대장(.xlsx)을 업로드하세요")

    def _period(self, ctx: dict, info: dict) -> tuple[int, int]:
        raw = info.get("귀속월", "")
        notes = []
        try:
            y, m = (int(x) for x in raw.split("-"))
            assert 1 <= m <= 12
        except Exception:
            y, m = ctx["tax_year"], date.today().month
            notes.append(pc.issue("info", "귀속월", f"'정보' 시트에 귀속월이 없어 {y}-{m:02d} 로 가정함", "정보"))
        if y != ctx["tax_year"]:
            raise ValidationError(f"귀속월({y}-{m:02d})이 선택한 적용연도({ctx['tax_year']})와 다릅니다")
        ctx["period_notes"] = notes
        return y, m

    # ------------------------------------------------------------------
    # 2) 계산
    # ------------------------------------------------------------------
    async def compute(self, ctx: dict) -> None:
        rules: Rules = ctx["rules"]
        try:
            table = pc.TaxTable.from_csv(rules.base_dir / rules.v("withholding.simple_table.file"))
            ctx["table"] = table
            employees = ctx["employees"]
            total = len(employees)
            results = []
            for i, emp in enumerate(employees, 1):
                results.append(pc.calc_employee(emp, rules, table, ctx["period"]))
                self.progress(ctx, 5 + int(80 * i / total), f"급여대장 {total}명 중 {i}명 계산 중")
                await asyncio.sleep(config.CALC_DELAY)
            ctx["results"] = results

            self.progress(ctx, 88, "사업·기타소득 원천징수 계산 중")
            ctx["payee_results"] = [pc.calc_payee(p, rules) for p in ctx["payees"]]
            self.progress(ctx, 92, "원천징수이행상황신고서 집계 중")
            ctx["summary"] = pc.summarize_withholding(results, ctx["payee_results"])
        except RuleMissing as e:
            raise ValidationError(f"규칙 값 미확인: {e}")
        except FileNotFoundError as e:
            raise ValidationError(f"간이세액표 파일을 찾지 못했습니다: {e}")

    # ------------------------------------------------------------------
    # 3) 이상항목 리포트
    # ------------------------------------------------------------------
    async def report_anomalies(self, ctx: dict) -> None:
        self.progress(ctx, 96, "이상항목 점검 중")
        rules: Rules = ctx["rules"]
        try:
            found = pc.find_anomalies(ctx["results"], ctx["payee_results"], ctx["prev_gross"], rules,
                                      ctx["period"], ctx["table"])
        except RuleMissing as e:
            raise ValidationError(f"규칙 값 미확인: {e}")
        if ctx["prev_gross"] is None:
            found.append(pc.issue("info", "전월 자료 없음", "'전월급여' 시트가 없어 전월 대비 변동 점검을 건너뜀", "전월급여"))
        unverified = rules.unverified()
        found.append(pc.issue("warning", "법령 수치 검증 전",
                              f"적용한 규칙 중 {len(unverified)}건이 verified: false 입니다. 법령 MCP로 검증 전까지 계산 결과는 참고용",
                              f"app/rules/{rules.year}/"))
        ctx["anomalies"] = ctx["input_notes"] + found
        db.add_anomalies(ctx["task_id"], ctx["anomalies"])

    def rules_warnings(self, ctx: dict) -> list[str]:
        rules: Rules = ctx["rules"]
        lo, hi = ctx["table"].coverage
        return [
            f"간이세액표: 홈택스 2026.03.01 시행분({lo:,}원 이상 ~ {hi:,}원 미만). 월급여 1천만원 초과(계산식)·가족 11명 초과는 표에 없어 직접 확인",
            f"법령 수치 {len(rules.unverified())}건이 검증 전(verified: false)입니다 — 법령 MCP로 확인 필요",
        ]

    async def request_review(self, ctx: dict) -> None:
        await super().request_review(ctx)
        net = sum(r["net_pay"] for r in ctx["results"])
        warn = sum(1 for a in ctx["anomalies"] if a["severity"] == "warning")
        db.update_task(ctx["task_id"], result=f"{len(ctx['results'])}명 · 실수령 합계 {net:,}원 · 이상항목 {len(ctx['anomalies'])}건(주의 {warn})")

    # ------------------------------------------------------------------
    # 4) 결과 엑셀 (계산 셀은 수식으로 유지)
    # ------------------------------------------------------------------
    def build_workbook(self, ctx: dict) -> Workbook:
        wb = Workbook()
        ws_pay = wb.active
        ws_pay.title = SHEET_PAYSLIP
        ws_ins = wb.create_sheet(SHEET_INSURANCE)
        ws_wh = wb.create_sheet(SHEET_WITHHOLD)
        ws_an = wb.create_sheet(SHEET_ANOMALY)
        ws_basis = wb.create_sheet(SHEET_BASIS)

        ref = self._write_basis(ws_basis, ctx)  # 규칙 셀 주소를 먼저 확정
        n = len(ctx["results"])
        self._write_payslip(ws_pay, ctx, ref)
        self._write_insurance(ws_ins, ctx, ref)
        self._write_withholding(ws_wh, ctx, ref, n)
        self._write_anomalies(ws_an, ctx)
        return wb

    # 엑셀 수식 주의: 0.009 같은 소수 요율을 곱하면 3149.9999999999995 처럼 부동소수 오차가 생겨
    # TRUNC 이 한 단위 낮게 절사될 수 있다 → 모든 절사 수식은 TRUNC(ROUND(x,6)) 로 감싼다(파이썬은 Decimal 로 정확 계산).
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

    def _write_basis(self, ws, ctx: dict) -> dict:
        """계산근거 시트. 규칙 값은 B열에 두고 다른 시트 수식이 이 셀을 참조한다 → 요율을 고치면 전체가 재계산."""
        rules: Rules = ctx["rules"]
        meta = [("에이전트", f"{self.icon} {self.name}"), ("고객사", ctx["client_name"]),
                ("귀속월", f"{ctx['period'][0]}-{ctx['period'][1]:02d}"), ("적용연도(기준연도)", ctx["tax_year"]),
                ("입력 파일", ctx.get("input_name", "")), ("생성시각", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                ("상태", "AI 작성본 — 담당자의 검토 승인 전")]
        for k, v in meta:
            ws.append([k, v])
            ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        for w in self.rules_warnings(ctx):
            ws.append(["경고", w])
            for c in (1, 2):
                ws.cell(row=ws.max_row, column=c).fill = WARN_FILL
        ws.append([])
        ws.append(["적용 요율·기준 (이 값을 고치면 다른 시트가 다시 계산됩니다)"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        self._head(ws, ws.max_row + 1, ["항목(rules 경로)", "값", "근거 법령/고시", "적용연도", "검증", "확인일"])
        ref = {}
        for path, it in rules.items():
            ws.append([path, it["value"] if it["value"] is not None else "확인 필요", it.get("source", ""),
                       it.get("year"), "검증됨" if it.get("verified") else "검증 전", it.get("verified_on") or ""])
            ref[path] = f"'{SHEET_BASIS}'!$B${ws.max_row}"
            if not it.get("verified"):
                ws.cell(row=ws.max_row, column=5).fill = WARN_FILL
        lo, hi = ctx["table"].coverage
        ws.append([])
        ws.append(["간이세액표", f"홈택스 2026.03.01 시행분 {lo:,}원 이상 ~ {hi:,}원 미만 (1천만원 초과는 계산식 — 표에 없음)"])
        ws.append([])
        ws.append(["이번 단계에서 반영하지 않은 항목"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        for text in NOT_REFLECTED:
            ws.append(["-", text])
        self._widths(ws, [44, 28, 70, 10, 10, 12])
        return ref

    def _write_payslip(self, ws, ctx: dict, ref: dict) -> None:
        heads = ["사번", "성명", "주민번호(마스킹)", "입사일", "기본급", "식대", "자가운전보조금", "상여", "총지급액",
                 "비과세", "과세급여", "국민연금", "건강보험", "장기요양", "고용보험", "근로소득세", "지방소득세",
                 "공제합계", "실수령액", "공제대상가족(본인포함)", "20세이하 자녀수", "간이세액표 조회세액", "자녀 세액공제", "비고"]
        self._head(ws, 1, heads)
        meal, car = ref["payroll.nontaxable.meal_monthly_limit"], ref["payroll.nontaxable.own_car_monthly_limit"]
        lrate, lunit = ref["withholding.local_income_tax.rate"], ref["withholding.local_income_tax.truncate_unit"]
        c1, c2, cx = (ref["withholding.child_credit.one_child"], ref["withholding.child_credit.two_children"],
                      ref["withholding.child_credit.extra_per_child"])
        notes = self._notes_by_emp(ctx)
        for i, res in enumerate(ctx["results"]):
            r, e = i + 2, res["emp"]
            ws.append([e.emp_no, e.name, e.rrn, e.hire_date, e.base, e.meal, e.car, e.bonus,
                       f"=SUM(E{r}:H{r})",
                       f"=MIN(F{r},{meal})+MIN(G{r},{car})",
                       f"=I{r}-J{r}",
                       f"='{SHEET_INSURANCE}'!E{r}", f"='{SHEET_INSURANCE}'!F{r}",
                       f"='{SHEET_INSURANCE}'!G{r}", f"='{SHEET_INSURANCE}'!H{r}",
                       f"=MAX(0,V{r}-W{r})" if res["tax_in_range"] else 0,
                       f"=TRUNC(ROUND(P{r}*{lrate}/{lunit},6))*{lunit}",
                       f"=SUM(L{r}:Q{r})", f"=I{r}-R{r}",
                       res["family"], e.children,
                       res["table_tax"] if res["tax_in_range"] else "구간 없음",
                       f"=IF(U{r}=0,0,IF(U{r}=1,{c1},{c2}+(U{r}-2)*{cx}))",
                       notes.get(e.emp_no, "")])
            ws.cell(row=r, column=4).number_format = "yyyy-mm-dd"
        last = len(ctx["results"]) + 1
        total = last + 1
        ws.cell(row=total, column=1, value="합계").font = Font(bold=True)
        for col in range(5, 20):
            letter = get_column_letter(col)
            ws.cell(row=total, column=col, value=f"=SUM({letter}2:{letter}{last})").font = Font(bold=True)
        for row in ws.iter_rows(min_row=2, max_row=total, min_col=5, max_col=23):
            for cell in row:
                cell.number_format = MONEY
        self._widths(ws, [9, 10, 18, 12] + [13] * 15 + [14, 12, 16, 14, 50])
        ws.freeze_panes = "C2"

    def _write_insurance(self, ws, ctx: dict, ref: dict) -> None:
        self._head(ws, 1, ["사번", "성명", "보수월액(과세급여)", "국민연금 기준소득월액",
                           "국민연금(근로자)", "건강보험(근로자)", "장기요양(근로자)", "고용보험(근로자)", "근로자 부담 합계",
                           "국민연금(사업주)", "건강보험(사업주)", "장기요양(사업주)", "고용보험(사업주)", "사업주 부담 합계", "총 합계"])
        R = ref
        floor_path, cap_path = pc.pension_limit_paths(ctx["rules"], ctx["period"])  # 귀속월이 속한 적용기간의 상·하한 셀
        floor, cap = R[floor_path], R[cap_path]
        bu = R["insurance.national_pension.base_truncate_unit"]
        pu, hu = R["insurance.national_pension.truncate_unit"], R["insurance.health_insurance.truncate_unit"]
        lu, eu = R["insurance.long_term_care.truncate_unit"], R["insurance.employment_insurance.truncate_unit"]
        ltc_emp, ltc_er = R["insurance.long_term_care.employee_rate"], R["insurance.long_term_care.employer_rate"]
        for i, res in enumerate(ctx["results"]):
            r, e = i + 2, res["emp"]
            ws.append([e.emp_no, e.name, f"='{SHEET_PAYSLIP}'!K{r}",
                       f"=TRUNC(ROUND(MIN(MAX(C{r},{floor}),{cap})/{bu},6))*{bu}",
                       f"=TRUNC(ROUND(D{r}*{R['insurance.national_pension.employee_rate']}/{pu},6))*{pu}",
                       f"=TRUNC(ROUND(C{r}*{R['insurance.health_insurance.employee_rate']}/{hu},6))*{hu}",
                       f"=TRUNC(ROUND(C{r}*{ltc_emp}/{lu},6))*{lu}",
                       f"=TRUNC(ROUND(C{r}*{R['insurance.employment_insurance.employee_rate']}/{eu},6))*{eu}",
                       f"=SUM(E{r}:H{r})",
                       f"=TRUNC(ROUND(D{r}*{R['insurance.national_pension.employer_rate']}/{pu},6))*{pu}",
                       f"=TRUNC(ROUND(C{r}*{R['insurance.health_insurance.employer_rate']}/{hu},6))*{hu}",
                       f"=TRUNC(ROUND(C{r}*{ltc_er}/{lu},6))*{lu}",
                       f"=TRUNC(ROUND(C{r}*{R['insurance.employment_insurance.employer_rate']}/{eu},6))*{eu}",
                       f"=SUM(J{r}:M{r})", f"=I{r}+N{r}"])
        last = len(ctx["results"]) + 1
        total = last + 1
        ws.cell(row=total, column=1, value="합계").font = Font(bold=True)
        for col in range(3, 16):
            letter = get_column_letter(col)
            ws.cell(row=total, column=col, value=f"=SUM({letter}2:{letter}{last})").font = Font(bold=True)
        for row in ws.iter_rows(min_row=2, max_row=total, min_col=3, max_col=15):
            for cell in row:
                cell.number_format = MONEY
        self._widths(ws, [9, 10] + [15] * 13)
        ws.freeze_panes = "C2"

    def _write_withholding(self, ws, ctx: dict, ref: dict, n: int) -> None:
        R = ref
        ws.append(["원천징수이행상황신고서 형식 집계 (소득종류별)"])
        ws.cell(row=1, column=1).font = Font(bold=True, size=12)
        ws.append(["※ 소득 코드는 작성요령 확인 전이라 비워 둠 (TODO). 사업·기타소득 '인원'은 지급 건수 기준. 근로소득 총지급액은 과세급여(비과세 제외)."])
        self._head(ws, 3, ["소득종류", "코드", "인원", "총지급액", "소득세", "지방소득세", "징수세액 합계"])
        d_first = 11
        m = len(ctx["payee_results"])
        d_last = max(d_first, d_first - 1 + m)
        A, F, I, J = (f"${c}${d_first}:${c}${d_last}" for c in "AFIJ")
        codes = [R.get(k) for k in ("withholding.report_codes.wage_simple", "withholding.report_codes.business",
                                     "withholding.report_codes.other")]
        last = n + 1
        rows = [
            ["근로소득 (간이세액)", f"={codes[0]}", f"=COUNTA('{SHEET_PAYSLIP}'!A2:A{last})",
             f"=SUM('{SHEET_PAYSLIP}'!K2:K{last})", f"=SUM('{SHEET_PAYSLIP}'!P2:P{last})",
             f"=SUM('{SHEET_PAYSLIP}'!Q2:Q{last})", "=E4+F4"],
        ]
        for idx, kind in ((1, pc.KIND_BUSINESS), (2, pc.KIND_OTHER)):
            r = 4 + idx
            rows.append([kind, f"={codes[idx]}", f'=COUNTIF({A},"{kind}")', f'=SUMIF({A},"{kind}",{F})',
                         f'=SUMIF({A},"{kind}",{I})', f'=SUMIF({A},"{kind}",{J})', f"=E{r}+F{r}"])
        for row in rows:
            ws.append(row)
        ws.append(["합계", "", "=SUM(C4:C6)", "=SUM(D4:D6)", "=SUM(E4:E6)", "=SUM(F4:F6)", "=SUM(G4:G6)"])
        for c in range(1, 8):
            ws.cell(row=7, column=c).font = Font(bold=True)
        for row in ws.iter_rows(min_row=4, max_row=7, min_col=3, max_col=7):
            for cell in row:
                cell.number_format = MONEY

        ws.cell(row=9, column=1, value="사업·기타소득 지급 내역별 원천징수").font = Font(bold=True)
        self._head(ws, 10, ["구분", "성명", "주민번호(마스킹)", "지급일", "소득종류", "지급액", "필요경비", "소득금액",
                            "소득세", "지방소득세", "비고"])
        brate, bunit = R["withholding.business_income.rate"], R["withholding.business_income.truncate_unit"]
        erate, orate = R["withholding.other_income.required_expense_rate"], R["withholding.other_income.rate"]
        omin, ounit = R["withholding.other_income.minimum_taxable_income"], R["withholding.other_income.truncate_unit"]
        lrate, lunit = R["withholding.local_income_tax.rate"], R["withholding.local_income_tax.truncate_unit"]
        for i, res in enumerate(ctx["payee_results"]):
            r, p = d_first + i, res["payee"]
            ws.append([p.kind, p.name, p.rrn, p.pay_date, p.income_type, p.amount,
                       f'=IF(A{r}="{pc.KIND_OTHER}",TRUNC(ROUND(F{r}*{erate},6)),0)', f"=F{r}-G{r}",
                       f'=IF(A{r}="{pc.KIND_BUSINESS}",TRUNC(ROUND(F{r}*{brate}/{bunit},6))*{bunit},'
                       f'IF(H{r}<={omin},0,TRUNC(ROUND(H{r}*{orate}/{ounit},6))*{ounit}))',
                       f"=TRUNC(ROUND(I{r}*{lrate}/{lunit},6))*{lunit}",
                       "과세최저한 이하 — 원천징수 없음" if res["below_minimum"] else ""])
            ws.cell(row=r, column=4).number_format = "yyyy-mm-dd"
            for c in (6, 7, 8, 9, 10):
                ws.cell(row=r, column=c).number_format = MONEY
        if m == 0:
            ws.cell(row=d_first, column=1, value="(지급 내역 없음)")
        self._widths(ws, [22, 12, 18, 14, 14, 14, 14, 14, 14, 14, 30])

    def _write_anomalies(self, ws, ctx: dict) -> None:
        self._head(ws, 1, ["심각도", "분류", "대상", "내용"])
        label = {"error": "오류", "warning": "주의", "info": "참고"}
        order = {"error": 0, "warning": 1, "info": 2}
        for a in sorted(ctx["anomalies"], key=lambda a: order.get(a["severity"], 3)):
            ws.append([label.get(a["severity"], a["severity"]), a.get("category", ""), a.get("row_ref", ""),
                       a["message"].split("] ", 1)[-1]])
            if a["severity"] == "warning":
                ws.cell(row=ws.max_row, column=1).fill = WARN_FILL
        if not ctx["anomalies"]:
            ws.append(["-", "-", "-", "이상항목 없음"])
        self._widths(ws, [8, 20, 34, 100])
        ws.freeze_panes = "A2"

    @staticmethod
    def _notes_by_emp(ctx: dict) -> dict:
        """급여명세 '비고'에 개인별 이상항목 분류를 요약."""
        notes: dict[str, list[str]] = {}
        for a in ctx["anomalies"]:
            ref = a.get("row_ref", "")
            if "(사번 " in ref:
                emp_no = ref.split("(사번 ", 1)[1].split(" ", 1)[0].rstrip(")")
                notes.setdefault(emp_no, []).append(a.get("category", ""))
        return {k: ", ".join(dict.fromkeys(v)) for k, v in notes.items()}
