"""페이(급여) 계산 로직 — 순수 함수 모음.

엑셀 읽기·검증·계산만 담당하고 상태 변경(set_status)은 하지 않는다(에이전트가 호출).
금액은 Decimal 로 계산해 float 오차 없이 절사한다. 법령 수치는 전부 Rules 에서 읽는다.
"""
import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal

from app.core.rules import RuleMissing, Rules

# ---------- 엑셀 헤더 ----------
PAYROLL_COLUMNS = {
    "사번": "emp_no", "성명": "name", "주민번호": "rrn", "주민등록번호": "rrn", "입사일": "hire_date",
    "기본급": "base", "식대": "meal", "자가운전보조금": "car", "상여": "bonus",
    "부양가족수": "dependents", "20세이하자녀수": "children",
}
PAYEE_COLUMNS = {"구분": "kind", "성명": "name", "주민번호": "rrn", "주민등록번호": "rrn",
                 "지급일": "pay_date", "지급액": "amount", "소득종류": "income_type"}
PREV_COLUMNS = {"사번": "emp_no", "총지급액": "gross"}

REQUIRED_PAYROLL = ("emp_no", "name", "rrn", "hire_date", "base", "dependents")
KIND_BUSINESS, KIND_OTHER = "사업소득", "기타소득"

FIELD_LABELS = {"emp_no": "사번", "name": "성명", "rrn": "주민번호", "hire_date": "입사일", "base": "기본급",
                "dependents": "부양가족수", "kind": "구분", "pay_date": "지급일", "amount": "지급액"}

MASKED_RRN = re.compile(r"^\d{6}-\d\*{6}$")
FULL_RRN = re.compile(r"^\d{6}-?\d{7}$")


def trunc(amount, unit: int = 1) -> int:
    """unit 단위 미만 절사 (예: unit=10 → 10원 미만 절사). 음수는 0 으로."""
    q = (Decimal(amount) / Decimal(unit)).to_integral_value(rounding=ROUND_FLOOR)
    return max(0, int(q) * int(unit))


def issue(severity: str, category: str, message: str, ref: str = "") -> dict:
    """이상항목 한 건. DB 에는 severity/message/row_ref 만 저장된다(category 는 메시지 앞에 붙임)."""
    return {"severity": severity, "category": category, "message": f"[{category}] {message}", "row_ref": ref}


# ======================================================================
# 1) 엑셀 읽기
# ======================================================================
def _norm_header(h) -> str:
    return re.sub(r"\(.*?\)|\s", "", str(h or ""))


def read_table(ws, columns: dict) -> tuple[list[tuple[int, dict]], list[str]]:
    """1행을 머리글로 보고 [(엑셀 행번호, {필드: 값})], 누락된 머리글 목록을 반환."""
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], list(dict.fromkeys(columns.values()))
    index = {}
    for i, h in enumerate(rows[0]):
        field = columns.get(_norm_header(h))
        if field and field not in index:
            index[field] = i
    missing = [f for f in dict.fromkeys(columns.values()) if f not in index]
    out = []
    for n, row in enumerate(rows[1:], start=2):
        if all(c is None or str(c).strip() == "" for c in row):
            continue
        out.append((n, {f: (row[i] if i < len(row) else None) for f, i in index.items()}))
    return out, missing


def read_info(ws) -> dict:
    """'정보' 시트: 항목/값 두 열 (귀속월, 고객사)."""
    info = {}
    for row in ws.iter_rows(values_only=True):
        if row and row[0] is not None and len(row) > 1 and row[1] is not None:
            info[str(row[0]).strip()] = str(row[1]).strip()
    return info


# ======================================================================
# 2) 입력 검증 — 필수값 누락 / 숫자 형식 / 중복 사번
# ======================================================================
@dataclass
class Employee:
    row: int
    emp_no: str
    name: str
    rrn: str
    hire_date: date
    base: int
    meal: int
    car: int
    bonus: int
    dependents: int
    children: int


def _to_number(value):
    """숫자(또는 '1,234' 문자열)를 Decimal 로. 변환 불가면 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.replace(",", "").strip())
        except Exception:
            return None
    return None


def _to_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def check_rrn(value, ref: str, issues: list) -> str:
    """주민번호: 마스킹 형식(900101-1******)만 허용. 마스킹 안 된 번호는 개인정보라 오류."""
    s = str(value).strip()
    if FULL_RRN.match(s):
        issues.append(issue("error", "주민번호", "마스킹되지 않은 주민번호입니다(개인정보). 900101-1****** 형식으로 가린 뒤 업로드하세요", ref))
        return s[:6] + "-" + s.replace("-", "")[6] + "******"  # 화면·결과에는 원본을 남기지 않는다
    if not MASKED_RRN.match(s):
        issues.append(issue("error", "주민번호", f"형식 오류 '{s}' (예: 900101-1******)", ref))
    return s


def validate_payroll(rows: list[tuple[int, dict]], missing_cols: list[str],
                     sheet: str = "급여대장") -> tuple[list[Employee], list[dict]]:
    """급여대장 행을 검증해 (Employee 목록, 이슈 목록)을 반환. 이슈 중 error 가 있으면 계산하지 않는다."""
    issues: list[dict] = []
    for col in missing_cols:
        if col in REQUIRED_PAYROLL:
            issues.append(issue("error", "필수값 누락", f"'{sheet}' 시트에 필수 열이 없습니다: {FIELD_LABELS[col]}", sheet))
    if any(i["severity"] == "error" for i in issues):
        return [], issues

    employees: list[Employee] = []
    seen: dict[str, int] = {}
    for n, r in rows:
        ref = f"{sheet} 행 {n}" + (f" (사번 {r['emp_no']})" if not _blank(r.get("emp_no")) else "")
        before = len(issues)
        for f in REQUIRED_PAYROLL:
            if _blank(r.get(f)):
                issues.append(issue("error", "필수값 누락", f"{FIELD_LABELS[f]} 누락", ref))
        emp_no = str(r.get("emp_no") or "").strip()
        if emp_no:
            if emp_no in seen:
                issues.append(issue("error", "중복 사번", f"사번 {emp_no} 이(가) 행 {seen[emp_no]} 과(와) 중복", ref))
            else:
                seen[emp_no] = n
        rrn = check_rrn(r["rrn"], ref, issues) if not _blank(r.get("rrn")) else ""
        hire = None
        if not _blank(r.get("hire_date")):
            hire = _to_date(r["hire_date"])
            if hire is None:
                issues.append(issue("error", "형식 오류", f"입사일 형식 오류 '{r['hire_date']}' (예: 2024-03-01)", ref))
        nums = {}
        for f, label in (("base", "기본급"), ("meal", "식대"), ("car", "자가운전보조금"), ("bonus", "상여"),
                         ("dependents", "부양가족수"), ("children", "20세이하 자녀수")):
            v = r.get(f)
            if _blank(v):
                nums[f] = 0
                continue
            num = _to_number(v)
            if num is None:
                issues.append(issue("error", "숫자 형식", f"{label} 숫자 형식 오류 '{v}'", ref))
            elif num < 0:
                issues.append(issue("error", "숫자 형식", f"{label} 음수 불가 ({num})", ref))
            elif num != num.to_integral_value():
                issues.append(issue("error", "숫자 형식", f"{label} 정수여야 합니다 ({num})", ref))
            else:
                nums[f] = int(num)
        if "dependents" in nums and "children" in nums and nums["children"] > nums["dependents"]:
            issues.append(issue("error", "논리 오류", "20세 이하 자녀수가 부양가족수보다 많습니다", ref))
        if len(issues) == before and emp_no and hire:
            employees.append(Employee(n, emp_no, str(r["name"]).strip(), rrn, hire, nums["base"], nums["meal"],
                                      nums["car"], nums["bonus"], nums["dependents"], nums["children"]))
    if not rows:
        issues.append(issue("error", "필수값 누락", f"'{sheet}' 시트에 데이터가 없습니다", sheet))
    return employees, issues


@dataclass
class Payee:
    row: int
    kind: str
    name: str
    rrn: str
    pay_date: date
    amount: int
    income_type: str


def validate_payees(rows: list[tuple[int, dict]], missing_cols: list[str],
                    sheet: str = "소득자지급내역") -> tuple[list[Payee], list[dict]]:
    issues: list[dict] = []
    for col in missing_cols:
        if col in ("kind", "name", "rrn", "pay_date", "amount"):
            issues.append(issue("error", "필수값 누락", f"'{sheet}' 시트에 필수 열이 없습니다: {FIELD_LABELS[col]}", sheet))
    if issues:
        return [], issues
    payees: list[Payee] = []
    for n, r in rows:
        ref = f"{sheet} 행 {n}"
        before = len(issues)
        kind = str(r.get("kind") or "").strip()
        if kind not in (KIND_BUSINESS, KIND_OTHER):
            issues.append(issue("error", "형식 오류", f"구분은 '사업소득' 또는 '기타소득'이어야 합니다 ('{kind}')", ref))
        for f, label in (("name", "성명"), ("rrn", "주민번호"), ("pay_date", "지급일"), ("amount", "지급액")):
            if _blank(r.get(f)):
                issues.append(issue("error", "필수값 누락", f"{label} 누락", ref))
        rrn = check_rrn(r["rrn"], ref, issues) if not _blank(r.get("rrn")) else ""
        pay_date = _to_date(r.get("pay_date")) if not _blank(r.get("pay_date")) else None
        if not _blank(r.get("pay_date")) and pay_date is None:
            issues.append(issue("error", "형식 오류", f"지급일 형식 오류 '{r['pay_date']}'", ref))
        amount = None
        if not _blank(r.get("amount")):
            num = _to_number(r["amount"])
            if num is None or num < 0 or num != num.to_integral_value():
                issues.append(issue("error", "숫자 형식", f"지급액은 0 이상의 정수여야 합니다 ('{r['amount']}')", ref))
            else:
                amount = int(num)
        if len(issues) == before:
            payees.append(Payee(n, kind, str(r["name"]).strip(), rrn, pay_date, amount,
                                str(r.get("income_type") or "").strip()))
    return payees, issues


# ======================================================================
# 3) 간이세액표 조회
# ======================================================================
@dataclass
class TaxTable:
    rows: list[tuple[int, int, list[int]]]  # (from, to, [f1..f11])
    path: str = ""

    @classmethod
    def from_csv(cls, path) -> "TaxTable":
        rows = []
        with open(path, encoding="utf-8-sig", newline="") as f:
            lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
        reader = csv.DictReader(lines)
        for r in reader:
            taxes = [int(r[f"f{i}"]) for i in range(1, 12)]
            rows.append((int(r["salary_from"]), int(r["salary_to"]), taxes))
        return cls(rows, str(path))

    @property
    def coverage(self) -> tuple[int, int]:
        return self.rows[0][0], self.rows[-1][1]

    def lookup(self, salary: int, family: int):
        """(세액, 구간 있음 여부). 구간이 없으면 (0, False)."""
        family = max(family, 1)
        for lo, hi, taxes in self.rows:
            if lo <= salary < hi:
                if family <= 11:
                    return taxes[family - 1], True
                # 간이세액표 안내문 4: 11명 초과 = (11명 세액) − (10명 세액 − 11명 세액) × 11명 초과 가족 수, 음수면 0
                return max(0, taxes[10] - (taxes[9] - taxes[10]) * (family - 11)), True
        return 0, False


def child_credit(children: int, rules: Rules) -> int:
    """자녀 세액공제(월): 1명/2명/3명째부터 추가. 값은 rules 에서."""
    if children <= 0:
        return 0
    if children == 1:
        return int(rules.v("withholding.child_credit.one_child"))
    return int(rules.v("withholding.child_credit.two_children")) + \
        (children - 2) * int(rules.v("withholding.child_credit.extra_per_child"))


# ======================================================================
# 4) 개인별 계산
# ======================================================================
def pension_limit_paths(rules: Rules, period: tuple[int, int]) -> tuple[str, str]:
    """귀속월이 속한 적용기간의 국민연금 기준소득월액 (하한 경로, 상한 경로).

    상·하한은 7월~다음 해 6월 기간별로 고시되므로 rules 의 limits.<기간>.applies("YYYY-MM~YYYY-MM") 로 기간을 찾는다.
    맞는 기간이 없으면 RuleMissing(→ 작업 오류) — 엉뚱한 기간 값으로 조용히 계산하지 않는다."""
    ym = f"{period[0]}-{period[1]:02d}"
    groups = rules.data.get("insurance", {}).get("national_pension", {}).get("limits", {})
    for name, group in groups.items():
        applies = str(group.get("applies", {}).get("value") or "")
        if "~" not in applies:
            continue
        start, end = applies.split("~")
        if start <= ym <= end:
            base = f"insurance.national_pension.limits.{name}"
            return f"{base}.floor", f"{base}.cap"
    raise RuleMissing(f"국민연금 기준소득월액 상·하한 적용기간에 {ym} 이 없습니다 (insurance.yaml > limits)")


def pension_limits(rules: Rules, period: tuple[int, int]) -> tuple[int, int]:
    floor_path, cap_path = pension_limit_paths(rules, period)
    return int(rules.v(floor_path)), int(rules.v(cap_path))


def calc_insurance(taxable: int, rules: Rules, period: tuple[int, int]) -> dict:
    """4대보험 근로자·사업주 부담분. taxable = 과세 급여(보수월액). period = 귀속월 (국민연금 상·하한 적용기간 선택)."""
    t = Decimal(taxable)
    floor, cap = pension_limits(rules, period)
    pension_base = trunc(min(max(t, floor), cap), int(rules.v("insurance.national_pension.base_truncate_unit")))
    pu = int(rules.v("insurance.national_pension.truncate_unit"))
    hu = int(rules.v("insurance.health_insurance.truncate_unit"))
    lu = int(rules.v("insurance.long_term_care.truncate_unit"))
    eu = int(rules.v("insurance.employment_insurance.truncate_unit"))

    health_emp = trunc(t * rules.d("insurance.health_insurance.employee_rate"), hu)
    health_er = trunc(t * rules.d("insurance.health_insurance.employer_rate"), hu)
    out = {
        "pension_base": pension_base,
        "pension_emp": trunc(pension_base * rules.d("insurance.national_pension.employee_rate"), pu),
        "pension_er": trunc(pension_base * rules.d("insurance.national_pension.employer_rate"), pu),
        "health_emp": health_emp,
        "health_er": health_er,
        # 장기요양: 건강보험료 × 비율이 아니라 보수 × 0.9448% (근로자·사업주 각 0.4724%) — 노인장기요양보험법 시행령 제4조
        "ltc_emp": trunc(t * rules.d("insurance.long_term_care.employee_rate"), lu),
        "ltc_er": trunc(t * rules.d("insurance.long_term_care.employer_rate"), lu),
        "emp_ins_emp": trunc(t * rules.d("insurance.employment_insurance.employee_rate"), eu),
        "emp_ins_er": trunc(t * rules.d("insurance.employment_insurance.employer_rate"), eu),
    }
    out["employee_total"] = out["pension_emp"] + out["health_emp"] + out["ltc_emp"] + out["emp_ins_emp"]
    out["employer_total"] = out["pension_er"] + out["health_er"] + out["ltc_er"] + out["emp_ins_er"]
    return out


def calc_employee(emp: Employee, rules: Rules, table: TaxTable, period: tuple[int, int]) -> dict:
    """한 명의 급여명세. 비과세 → 4대보험 → 간이세액표 → 지방소득세 → 실수령액."""
    meal_nt = min(emp.meal, int(rules.v("payroll.nontaxable.meal_monthly_limit")))
    car_nt = min(emp.car, int(rules.v("payroll.nontaxable.own_car_monthly_limit")))
    gross = emp.base + emp.meal + emp.car + emp.bonus
    nontaxable = meal_nt + car_nt
    taxable = gross - nontaxable
    ins = calc_insurance(taxable, rules, period)

    family = 1 + emp.dependents  # 공제대상가족 수 = 본인 + 부양가족
    table_tax, in_range = table.lookup(taxable, family)
    credit = child_credit(emp.children, rules)
    income_tax = max(0, table_tax - credit) if in_range else 0
    local_tax = trunc(Decimal(income_tax) * rules.d("withholding.local_income_tax.rate"),
                      int(rules.v("withholding.local_income_tax.truncate_unit")))
    deductions = ins["employee_total"] + income_tax + local_tax
    return {
        "emp": emp, "gross": gross, "meal_nontaxable": meal_nt, "car_nontaxable": car_nt,
        "nontaxable": nontaxable, "taxable": taxable, **ins,
        "family": family, "table_tax": table_tax, "child_credit": credit, "tax_in_range": in_range,
        "income_tax": income_tax, "local_tax": local_tax,
        "deductions": deductions, "net_pay": gross - deductions,
    }


def calc_payee(p: Payee, rules: Rules) -> dict:
    """사업소득(3.3%)·기타소득(필요경비 공제 후 세율) 원천징수."""
    local_rate = rules.d("withholding.local_income_tax.rate")
    local_unit = int(rules.v("withholding.local_income_tax.truncate_unit"))
    amount = Decimal(p.amount)
    expense = 0
    below_minimum = False
    if p.kind == KIND_BUSINESS:
        income = p.amount
        tax = trunc(amount * rules.d("withholding.business_income.rate"),
                    int(rules.v("withholding.business_income.truncate_unit")))
    else:
        expense = trunc(amount * rules.d("withholding.other_income.required_expense_rate"))
        income = p.amount - expense
        below_minimum = income <= int(rules.v("withholding.other_income.minimum_taxable_income"))
        tax = 0 if below_minimum else trunc(Decimal(income) * rules.d("withholding.other_income.rate"),
                                            int(rules.v("withholding.other_income.truncate_unit")))
    local = trunc(Decimal(tax) * local_rate, local_unit)
    return {"payee": p, "expense": expense, "income": income, "income_tax": tax,
            "local_tax": local, "below_minimum": below_minimum}


# ======================================================================
# 5) 집계 (원천징수이행상황신고서 형식)
# ======================================================================
def summarize_withholding(wage_results: list[dict], payee_results: list[dict]) -> list[dict]:
    wage = {"label": "근로소득 (간이세액)", "headcount": len(wage_results),
            "paid": sum(r["taxable"] for r in wage_results),
            "tax": sum(r["income_tax"] for r in wage_results),
            "local": sum(r["local_tax"] for r in wage_results)}
    out = [wage]
    for kind in (KIND_BUSINESS, KIND_OTHER):
        sel = [r for r in payee_results if r["payee"].kind == kind]
        out.append({"label": kind, "headcount": len(sel),  # 지급 건수 기준
                    "paid": sum(r["payee"].amount for r in sel),
                    "tax": sum(r["income_tax"] for r in sel), "local": sum(r["local_tax"] for r in sel)})
    return out


# ======================================================================
# 6) 이상항목 플래그
# ======================================================================
def find_anomalies(results: list[dict], payee_results: list[dict], prev_gross: dict | None,
                   rules: Rules, period: tuple[int, int], table: TaxTable) -> list[dict]:
    """전월 대비 30% 이상 변동 / 최저임금 미달 의심 / 국민연금 상한 해당자 + 보조 점검."""
    out: list[dict] = []
    ratio = rules.d("payroll.anomaly.salary_change_ratio")
    min_hourly = int(rules.v("payroll.minimum_wage.hourly"))
    hours = Decimal(str(rules.v("payroll.minimum_wage.monthly_hours")))
    floor, cap = pension_limits(rules, period)
    lo, hi = table.coverage

    for r in results:
        e: Employee = r["emp"]
        ref = f"급여대장 행 {e.row} (사번 {e.emp_no} {e.name})"
        # 1) 전월 대비 변동
        if prev_gross is not None:
            joined_now = (e.hire_date.year, e.hire_date.month) == period
            prev = prev_gross.get(e.emp_no)
            if prev is None:
                if not joined_now:
                    out.append(issue("info", "전월 자료 없음", "전월 급여대장에 없는 사번입니다(신규·재입사 여부 확인)", ref))
            elif prev > 0 and abs(Decimal(r["gross"] - prev)) / Decimal(prev) >= ratio:
                pct = (Decimal(r["gross"] - prev) / Decimal(prev) * 100).quantize(Decimal("0.1"))
                out.append(issue("warning", "전월 대비 변동",
                                 f"총지급액 {prev:,}원 → {r['gross']:,}원 ({pct:+}%)", ref))
        # 2) 최저임금 미달 의심 (기본급 기준 단순 환산 — 산입범위는 담당자가 확인)
        hourly = Decimal(e.base) / hours
        if hourly < min_hourly:
            out.append(issue("warning", "최저임금 미달 의심",
                             f"기본급 {e.base:,}원 ÷ {hours}시간 = 시급 {int(hourly):,}원 < 최저임금 {min_hourly:,}원 "
                             "(산입범위·근로시간 확인 필요)", ref))
        # 3) 국민연금 상한/하한
        if r["taxable"] >= cap:
            out.append(issue("warning", "국민연금 상한 해당",
                             f"과세급여 {r['taxable']:,}원 ≥ 기준소득월액 상한 {cap:,}원 — 상한액으로 보험료 산정", ref))
        elif r["taxable"] < floor:
            out.append(issue("info", "국민연금 하한 적용", f"과세급여 {r['taxable']:,}원 < 하한 {floor:,}원 — 하한액으로 산정", ref))
        # 4) 보조 점검
        if not r["tax_in_range"]:
            out.append(issue("warning", "간이세액표 구간 없음",
                             f"과세급여 {r['taxable']:,}원은 간이세액표 범위({lo:,}원 이상 ~ {hi:,}원 미만) 밖 — 소득세 0원으로 둠. "
                             "월급여 1천만원 초과는 표가 아니라 계산식이라 직접 계산·확인 필요", ref))
        if (e.hire_date.year, e.hire_date.month) == period:
            out.append(issue("info", "입사월", "입사월 급여입니다 — 일할계산·4대보험 취득월 처리는 이번 단계에서 반영하지 않음 (TODO)", ref))
        if e.meal > r["meal_nontaxable"] or e.car > r["car_nontaxable"]:
            out.append(issue("info", "비과세 한도 초과", "식대/자가운전보조금이 비과세 한도를 넘어 초과분을 과세 처리함", ref))
    for r in payee_results:
        p: Payee = r["payee"]
        if r["below_minimum"]:
            out.append(issue("info", "과세최저한", f"소득금액 {r['income']:,}원이 과세최저한 이하라 원천징수 없음",
                             f"소득자지급내역 행 {p.row} ({p.name})"))
    return out
