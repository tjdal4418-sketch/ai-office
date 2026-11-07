"""페이 에이전트 통합 테스트 — 샘플 데이터로 끝까지 실행하고 결과 엑셀(시트·수식·수치)을 확인한다."""
import asyncio
import shutil

import pytest
from openpyxl import load_workbook

from app.agents import REGISTRY
from app.agents.payroll import PayrollAgent
from app.core import config, db, state

SHEETS = ["급여명세", "4대보험", "원천세 집계", "이상항목", "계산근거"]
GANA = ("ganasangsa", "(주)가나상사")
DARA = ("daratech", "(주)다라테크")


def run_payroll(client=GANA):
    tid = db.create_task("payroll", client[0], 2026, {}, client_name=client[1], title="급여 계산")["id"]
    asyncio.run(REGISTRY["payroll"].run(tid))
    return tid, db.get_task(tid)


def categories(tid):
    return [a["message"].split("]")[0].lstrip("[") for a in db.list_anomalies(tid)]


def test_가나상사_끝까지_실행되면_검토필요():
    tid, task = run_payroll()
    assert task["status"] == "review"
    assert state.get_state("payroll").status == "review"
    assert "16명" in task["result"]
    wb = load_workbook(task["output_path"])
    assert wb.sheetnames == SHEETS


def test_의도적으로_섞은_이상항목이_잡힌다():
    tid, _ = run_payroll()
    cats = categories(tid)
    assert cats.count("전월 대비 변동") == 2
    assert "최저임금 미달 의심" in cats
    assert cats.count("국민연금 상한 해당") == 2
    assert "간이세액표 구간 없음" not in cats            # 홈택스 표(770천원~1천만원)에 샘플 급여가 모두 들어간다
    assert "입사월" in cats and "법령 수치 검증 전" in cats


def test_엑셀_계산셀은_수식으로_살아있다():
    _, task = run_payroll()
    wb = load_workbook(task["output_path"])  # data_only=False
    pay = wb["급여명세"]
    assert pay["I2"].value == "=SUM(E2:H2)"
    assert pay["K2"].value == "=I2-J2"
    assert pay["S2"].value == "=I2-R2"
    assert str(pay["L2"].value).startswith("='4대보험'!")
    assert pay["E18"].value == "=SUM(E2:E17)"  # 합계 행 (16명)
    assert str(wb["4대보험"]["E2"].value).startswith("=TRUNC(")
    assert str(wb["원천세 집계"]["D4"].value).startswith("=SUM(")
    # 마스킹 형식 외 주민번호는 결과에 없다
    assert all("******" in str(r[2].value) for r in pay.iter_rows(min_row=2, max_row=17))


def test_계산근거_시트에_적용요율_기준연도_경고():
    _, task = run_payroll()
    basis = load_workbook(task["output_path"])["계산근거"]
    text = " ".join(str(c.value) for row in basis.iter_rows() for c in row if c.value is not None)
    assert "적용연도" in text and "2026" in text
    assert "홈택스 2026.03.01 시행분" in text and "전체표 아님" not in text   # 간이세액표 출처 안내(샘플 경고 없음)
    assert "insurance.national_pension.employee_rate" in text  # 적용 요율 목록
    assert "검증 전" in text and "일할계산" in text      # 미검증·미반영 안내


def test_원천세_집계에_소득종류별_행():
    _, task = run_payroll(DARA)
    ws = load_workbook(task["output_path"])["원천세 집계"]
    assert [ws.cell(row=r, column=1).value for r in (4, 5, 6, 7)] == ["근로소득 (간이세액)", "사업소득", "기타소득", "합계"]
    assert ws["C5"].value.startswith("=COUNTIF(") and ws["D6"].value.startswith("=SUMIF(")


def test_진행률_문구는_N명_중_n명_계산중(monkeypatch):
    seen = []
    orig = PayrollAgent.progress
    monkeypatch.setattr(PayrollAgent, "progress", lambda self, ctx, pct, text: (seen.append((pct, text)), orig(self, ctx, pct, text))[1])
    run_payroll()
    texts = [t for _, t in seen]
    assert "급여대장 16명 중 1명 계산 중" in texts and "급여대장 16명 중 16명 계산 중" in texts
    pcts = [p for p, _ in seen]
    assert pcts == sorted(pcts)  # 진행률은 거꾸로 가지 않는다


def test_오류_샘플은_검증단계에서_중단되고_리포트(tmp_path):
    folder = config.UPLOAD_DIR / "payroll"
    folder.mkdir(parents=True)
    shutil.copy(config.SAMPLE_DIR / "payroll" / "errors" / "daratech_오류시연.xlsx", folder / "daratech_오류시연.xlsx")
    tid, task = run_payroll(DARA)
    assert task["status"] == "error"
    assert state.get_state("payroll").status == "error"
    assert task["error_reason"].startswith("입력 오류")
    cats = categories(tid)
    assert {"중복 사번", "필수값 누락", "숫자 형식", "주민번호"} <= set(cats)
    assert not task["output_path"]  # 오류면 결과 파일을 만들지 않는다


def test_다른_고객사_업로드_파일은_건너뛴다():
    folder = config.UPLOAD_DIR / "payroll"
    folder.mkdir(parents=True)
    shutil.copy(config.SAMPLE_DIR / "payroll" / "ganasangsa.xlsx", folder / "아무이름.xlsx")
    _, task = run_payroll(DARA)  # 다라테크 실행 → 가나상사 파일은 무시, 다라테크 샘플 사용
    assert task["status"] == "review"
    basis = load_workbook(task["output_path"])["계산근거"]
    assert basis["B5"].value == "daratech.xlsx"


def test_업로드가_있으면_샘플보다_우선():
    folder = config.UPLOAD_DIR / "payroll"
    folder.mkdir(parents=True)
    shutil.copy(config.SAMPLE_DIR / "payroll" / "ganasangsa.xlsx", folder / "가나_업로드.xlsx")
    _, task = run_payroll()
    assert load_workbook(task["output_path"])["계산근거"]["B5"].value == "가나_업로드.xlsx"


def test_적용연도와_귀속월이_다르면_오류():
    tid = db.create_task("payroll", "ganasangsa", 2025, {}, client_name=GANA[1], title="급여 계산")["id"]
    asyncio.run(REGISTRY["payroll"].run(tid))
    task = db.get_task(tid)
    assert task["status"] == "error"  # 2025 규칙 폴더가 없거나 귀속월 불일치


def test_연말정산_퇴직소득은_추후확장_메뉴만():
    assert REGISTRY["payroll"].meta()["coming_soon"] == ["연말정산", "퇴직소득"]
    assert not hasattr(REGISTRY["payroll"], "year_end_settlement")


def test_수식_결과가_파이썬_계산과_일치(tmp_path):
    """엑셀 수식을 실제로 계산(formulas 라이브러리)해 파이썬 계산값과 셀 단위로 비교."""
    formulas = pytest.importorskip("formulas")
    _, task = run_payroll()
    path = shutil.copy(task["output_path"], tmp_path / "calc.xlsx")
    sol = formulas.ExcelModel().loads(str(path)).finish().calculate()

    def cell(sheet, addr):
        return float(sol[f"'[calc.xlsx]{sheet}'!{addr}"].value[0, 0])

    from app.agents import payroll_calc as pc
    from app.core.rules import load_rules
    rules = load_rules(2026)
    table = pc.TaxTable.from_csv(rules.base_dir / "simple_tax_table.csv")
    wb = load_workbook(task["output_path"], data_only=False)
    y, m = (int(x) for x in str(wb["계산근거"]["B3"].value).split("-"))  # 귀속월 (계산근거 시트 3행)
    period = (y, m)
    mismatches = []
    for r in range(2, 18):
        e = wb["급여명세"]
        emp = pc.Employee(r, e[f"A{r}"].value, e[f"B{r}"].value, e[f"C{r}"].value, e[f"D{r}"].value.date(),
                          e[f"E{r}"].value, e[f"F{r}"].value, e[f"G{r}"].value, e[f"H{r}"].value,
                          e[f"T{r}"].value - 1, e[f"U{r}"].value)
        py = pc.calc_employee(emp, rules, table, period)
        for col, key in (("I", "gross"), ("J", "nontaxable"), ("K", "taxable"), ("L", "pension_emp"),
                         ("M", "health_emp"), ("N", "ltc_emp"), ("O", "emp_ins_emp"), ("P", "income_tax"),
                         ("Q", "local_tax"), ("R", "deductions"), ("S", "net_pay")):
            if cell("급여명세", f"{col}{r}") != py[key]:
                mismatches.append((emp.emp_no, key, cell("급여명세", f"{col}{r}"), py[key]))
        for col, key in (("J", "pension_er"), ("K", "health_er"), ("L", "ltc_er"), ("M", "emp_ins_er")):
            if cell("4대보험", f"{col}{r}") != py[key]:
                mismatches.append((emp.emp_no, key, cell("4대보험", f"{col}{r}"), py[key]))
    assert mismatches == []
