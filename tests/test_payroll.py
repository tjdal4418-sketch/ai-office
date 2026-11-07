"""페이(급여) 계산 테스트 — 전부 손으로 계산 가능한 예시.

⚠ 여기서 쓰는 요율·한도는 '테스트 전용 가정값'이며 법령 값이 아니다(실제 값은 app/rules/2026/ 에 있고 검증 전).
   계산 로직이 규칙대로 움직이는지만 검증하려고 계산하기 쉬운 숫자(5%, 3% …)를 쓴다.
"""
from datetime import date

import pytest

from app.agents import payroll_calc as pc
from app.core.rules import RuleMissing, Rules


def L(value):
    return {"value": value, "source": "테스트 가정값", "year": 2026, "verified": False, "verified_on": None}


TEST_RULES = {
    "insurance": {
        # 상·하한은 적용기간(7월~다음 해 6월)별 — h1 은 2026.1~6월, h2 는 2026.7~2027.6월
        "national_pension": {"employee_rate": L(0.05), "employer_rate": L(0.05),
                             "limits": {"h1_2026": {"applies": L("2026-01~2026-06"), "floor": L(300000), "cap": L(5000000)},
                                        "h2_2026": {"applies": L("2026-07~2027-06"), "floor": L(400000), "cap": L(6000000)}},
                             "base_truncate_unit": L(1000), "truncate_unit": L(10)},
        "health_insurance": {"employee_rate": L(0.03), "employer_rate": L(0.03), "truncate_unit": L(10)},
        # 장기요양: 보수 × 요율 (근로자 0.4% / 사업주 0.5% — 서로 다르게 둬서 각각 계산하는지 확인)
        "long_term_care": {"employee_rate": L(0.004), "employer_rate": L(0.005), "truncate_unit": L(10)},
        "employment_insurance": {"employee_rate": L(0.01), "employer_rate": L(0.02), "truncate_unit": L(10)},
    },
    "payroll": {
        "nontaxable": {"meal_monthly_limit": L(200000), "own_car_monthly_limit": L(200000)},
        "minimum_wage": {"hourly": L(10000), "monthly_hours": L(200)},
        "anomaly": {"salary_change_ratio": L(0.30)},
    },
    "withholding": {
        "local_income_tax": {"rate": L(0.1), "truncate_unit": L(10)},
        "child_credit": {"one_child": L(10000), "two_children": L(25000), "extra_per_child": L(20000)},
        "business_income": {"rate": L(0.03), "truncate_unit": L(10)},   # 원천징수 소득세는 10원 미만 버림 (국고금 관리법 제47조①)
        "other_income": {"required_expense_rate": L(0.6), "rate": L(0.2), "minimum_taxable_income": L(50000),
                         "truncate_unit": L(10)},
    },
}
RULES = Rules(TEST_RULES, year=2026)
PERIOD = (2026, 10)  # 귀속월 → h2_2026 구간(하한 40만 / 상한 600만)을 쓴다

# 테스트용 간이세액표: f_k = 기준액 - 10,000 × (k-1)
TABLE = pc.TaxTable([
    (2_000_000, 2_700_000, [30000 - 5000 * k if 30000 - 5000 * k > 0 else 0 for k in range(11)]),
    (2_700_000, 2_900_000, [max(0, 60000 - 10000 * k) for k in range(11)]),
])


def emp(base=2_800_000, meal=0, car=0, bonus=0, dependents=0, children=0, hire=date(2020, 1, 2), no="E001", row=2):
    return pc.Employee(row, no, "테스트", "900101-1******", hire, base, meal, car, bonus, dependents, children)


# ---------------------------------------------------------------- 손계산 케이스
def test_월급300만원_부양가족1명():
    """기본급 280만 + 식대 20만(전액 비과세), 부양가족 1명 → 공제대상가족 2명.
    과세급여 2,800,000 / 국민연금 5% 140,000 / 건보 3% 84,000 / 장기요양 보수×0.4% 11,200 / 고용 1% 28,000
    소득세(f2) 50,000 / 지방소득세 5,000 → 공제 318,200 / 실수령 2,681,800"""
    r = pc.calc_employee(emp(meal=200_000, dependents=1), RULES, TABLE, PERIOD)
    assert (r["gross"], r["nontaxable"], r["taxable"]) == (3_000_000, 200_000, 2_800_000)
    assert (r["pension_emp"], r["health_emp"], r["ltc_emp"], r["emp_ins_emp"]) == (140_000, 84_000, 11_200, 28_000)
    assert (r["income_tax"], r["local_tax"]) == (50_000, 5_000)
    assert r["deductions"] == 318_200
    assert r["net_pay"] == 2_681_800
    # 사업주 부담분: 연금 140,000 / 건보 84,000 / 장기요양 보수×0.5% 14,000 / 고용 2% 56,000
    assert (r["pension_er"], r["health_er"], r["ltc_er"], r["emp_ins_er"]) == (140_000, 84_000, 14_000, 56_000)
    assert r["employer_total"] == 294_000


def test_비과세_한도_초과분은_과세():
    """식대 30만(한도 20만) + 자가운전 25만(한도 20만) → 비과세 40만, 초과 15만은 과세.
    총지급 3,050,000 / 과세 2,650,000 / 연금 132,500 / 건보 79,500 / 장기요양 10,600 / 고용 26,500
    소득세(f1) 30,000 / 지방 3,000 → 실수령 2,767,900"""
    r = pc.calc_employee(emp(base=2_500_000, meal=300_000, car=250_000), RULES, TABLE, PERIOD)
    assert (r["gross"], r["nontaxable"], r["taxable"]) == (3_050_000, 400_000, 2_650_000)
    assert r["employee_total"] == 249_100
    assert (r["income_tax"], r["local_tax"]) == (30_000, 3_000)
    assert r["net_pay"] == 2_767_900


def test_국민연금_기준소득월액_상한():
    """과세급여 800만 > 상한 600만 → 연금은 600만 기준 300,000. 건보·고용은 실제 보수 기준.
    (샘플표 구간 밖이라 소득세 0 + tax_in_range False)"""
    r = pc.calc_employee(emp(base=8_000_000), RULES, TABLE, PERIOD)
    assert r["pension_base"] == 6_000_000
    assert r["pension_emp"] == 300_000
    # 장기요양은 실제 보수 기준: 8,000,000 × 0.4% = 32,000
    assert (r["health_emp"], r["ltc_emp"], r["emp_ins_emp"]) == (240_000, 32_000, 80_000)
    assert r["tax_in_range"] is False and r["income_tax"] == 0


def test_국민연금_하한():
    r = pc.calc_insurance(300_000, RULES, PERIOD)  # 하한 40만 미만 → 40만 기준
    assert r["pension_base"] == 400_000 and r["pension_emp"] == 20_000


def test_국민연금_상하한은_귀속월의_적용기간_값을_쓴다():
    """같은 보수라도 귀속월이 2026.1~6월이면 h1(하한 30만/상한 500만), 2026.7월 이후면 h2(하한 40만/상한 600만)."""
    assert pc.pension_limits(RULES, (2026, 6)) == (300_000, 5_000_000)
    assert pc.pension_limits(RULES, (2026, 7)) == (400_000, 6_000_000)
    assert pc.pension_limits(RULES, (2027, 6)) == (400_000, 6_000_000)
    low = pc.calc_insurance(350_000, RULES, (2026, 3))   # h1 하한 30만 이상 → 35만 그대로
    assert low["pension_base"] == 350_000 and low["pension_emp"] == 17_500
    assert pc.calc_insurance(350_000, RULES, (2026, 10))["pension_base"] == 400_000   # h2 하한 40만
    assert pc.calc_insurance(5_500_000, RULES, (2026, 3))["pension_base"] == 5_000_000  # h1 상한
    assert pc.calc_insurance(5_500_000, RULES, (2026, 10))["pension_base"] == 5_500_000  # h2 상한 600만 미만


def test_국민연금_적용기간에_없는_귀속월은_오류():
    with pytest.raises(RuleMissing):
        pc.pension_limits(RULES, (2027, 7))


def test_절사_기준소득월액_1000원_보험료_10원():
    """과세급여 2,833,333: 기준소득월액 2,833,000 → 연금 141,650 / 건보 84,999.99 → 10원 미만 절사 84,990.
    장기요양은 보수 × 0.4% = 11,333.33 → 10원 미만 절사 11,330 (건강보험료 기준이 아님)."""
    r = pc.calc_insurance(2_833_333, RULES, PERIOD)
    assert r["pension_base"] == 2_833_000
    assert r["pension_emp"] == 141_650
    assert r["health_emp"] == 84_990
    assert r["ltc_emp"] == 11_330


def test_자녀_세액공제():
    """부양가족 3명(가족 4명 → f4=30,000), 자녀 2명 → 공제 25,000 → 소득세 5,000.
    자녀 3명이면 공제 25,000+20,000=45,000 > 30,000 → 0 (음수 방지)."""
    two = pc.calc_employee(emp(dependents=3, children=2), RULES, TABLE, PERIOD)
    assert (two["table_tax"], two["child_credit"], two["income_tax"]) == (30_000, 25_000, 5_000)
    three = pc.calc_employee(emp(dependents=3, children=3), RULES, TABLE, PERIOD)
    assert (three["child_credit"], three["income_tax"], three["local_tax"]) == (45_000, 0, 0)


def test_사업소득_33퍼센트():
    """지급 1,000,000 × 3% = 30,000 / 지방소득세 3,000 (합계 3.3%)"""
    p = pc.Payee(2, "사업소득", "가", "900101-1******", date(2026, 10, 25), 1_000_000, "")
    r = pc.calc_payee(p, RULES)
    assert (r["income_tax"], r["local_tax"]) == (30_000, 3_000)


def test_기타소득_필요경비_공제후_세율():
    """지급 1,000,000 → 필요경비 60% 600,000 → 소득금액 400,000 × 20% = 80,000 / 지방 8,000"""
    p = pc.Payee(2, "기타소득", "나", "900101-1******", date(2026, 10, 20), 1_000_000, "강연료")
    r = pc.calc_payee(p, RULES)
    assert (r["expense"], r["income"], r["income_tax"], r["local_tax"]) == (600_000, 400_000, 80_000, 8_000)


def test_사업소득_원천징수는_10원_미만_버림():
    """지급 1,234,567 × 3% = 37,037.01 → 37,030 / 지방소득세 37,030 × 10% = 3,703 → 3,700"""
    p = pc.Payee(2, "사업소득", "가", "900101-1******", date(2026, 10, 25), 1_234_567, "")
    r = pc.calc_payee(p, RULES)
    assert (r["income_tax"], r["local_tax"]) == (37_030, 3_700)


def test_기타소득_원천징수도_10원_미만_버림():
    """지급 1,000,001 → 필요경비 60% 600,000.6 → 소득금액 400,000.4 × 20% = 80,000.08 → 80,000 (원 단위가 아니라 10원 단위)
    지급 1,234,567 → 필요경비 740,740 → 소득금액 493,827 × 20% = 98,765.4 → 98,760"""
    p = pc.Payee(2, "기타소득", "나", "900101-1******", date(2026, 10, 20), 1_234_567, "강연료")
    r = pc.calc_payee(p, RULES)
    assert r["income_tax"] == 98_760 and r["income_tax"] % 10 == 0


def test_기타소득_과세최저한():
    """지급 100,000 → 소득금액 40,000 ≤ 50,000 → 원천징수 없음"""
    p = pc.Payee(2, "기타소득", "다", "900101-1******", date(2026, 10, 20), 100_000, "원고료")
    r = pc.calc_payee(p, RULES)
    assert r["below_minimum"] and r["income_tax"] == 0 and r["local_tax"] == 0


def test_원천징수_집계():
    wage = [pc.calc_employee(emp(meal=200_000, dependents=1), RULES, TABLE, PERIOD)]
    payees = [pc.calc_payee(pc.Payee(2, "사업소득", "가", "x", date(2026, 10, 25), 1_000_000, ""), RULES),
              pc.calc_payee(pc.Payee(3, "기타소득", "나", "x", date(2026, 10, 25), 1_000_000, ""), RULES)]
    s = pc.summarize_withholding(wage, payees)
    assert [x["label"] for x in s] == ["근로소득 (간이세액)", "사업소득", "기타소득"]
    assert (s[0]["headcount"], s[0]["paid"], s[0]["tax"], s[0]["local"]) == (1, 2_800_000, 50_000, 5_000)
    assert (s[1]["paid"], s[1]["tax"], s[1]["local"]) == (1_000_000, 30_000, 3_000)
    assert (s[2]["paid"], s[2]["tax"], s[2]["local"]) == (1_000_000, 80_000, 8_000)


# ---------------------------------------------------------------- 규칙·세액표
def test_규칙값이_비면_계산하지_않는다():
    data = {"insurance": {"national_pension": {"employee_rate": L(None)}}}
    with pytest.raises(RuleMissing):
        Rules(data).v("insurance.national_pension.employee_rate")


def test_간이세액표_구간_조회():
    assert TABLE.lookup(2_800_000, 2) == (50_000, True)
    assert TABLE.lookup(2_800_000, 99) == (0, True)  # 가족수 11명 초과는 11명 칸(0원)
    assert TABLE.lookup(10_000_000, 1) == (0, False)  # 구간 없음


def test_홈택스_간이세액표_로드():
    """2026.03.01 시행 홈택스 간이세액표: 770천원 이상 ~ 10,000천원(정확히) 647개 구간, 구간이 빈틈없이 이어진다."""
    from app.core import config
    t = pc.TaxTable.from_csv(config.RULES_DIR / "2026" / "simple_tax_table.csv")
    assert t.coverage == (770_000, 10_000_001) and len(t.rows) == 647
    assert all(a[1] == b[0] for a, b in zip(t.rows, t.rows[1:]))
    assert t.lookup(760_000, 1) == (0, False)          # 표 시작(770천원) 아래는 구간 없음
    assert t.lookup(10_000_001, 1) == (0, False)       # 1천만원 초과는 계산식 — 표에 없음


def test_홈택스_간이세액표_원본_엑셀에서_직접_찾은_값():
    """원본 엑셀을 눈으로 찾아 옮긴 값 (월급여 구간 / 가족수 → 세액)."""
    from app.core import config
    t = pc.TaxTable.from_csv(config.RULES_DIR / "2026" / "simple_tax_table.csv")
    assert t.lookup(1_500_000, 2) == (4_420, True)      # 1,500~1,510천원, 2명
    assert t.lookup(2_000_000, 1) == (19_520, True)     # 2,000~2,010천원, 1명
    assert t.lookup(3_000_000, 1) == (74_350, True)     # 3,000~3,020천원, 1명 (경계 포함: 3,019,999 도 같은 구간)
    assert t.lookup(3_019_999, 1) == (74_350, True)
    assert t.lookup(3_020_000, 1)[0] != 74_350          # 다음 구간
    assert t.lookup(4_500_000, 2) == (234_460, True)    # 4,500~4,520천원, 2명
    assert t.lookup(9_990_000, 11) == (958_650, True)   # 9,980~10,000천원, 11명
    assert t.lookup(10_000_000, 1) == (1_507_400, True)  # 월급여 정확히 10,000천원
    assert t.lookup(800_000, 1) == (0, True)            # '-' 는 0원


def test_월급여_1천만원_초과는_구간_없음_이상항목():
    """1천만원 초과는 표가 아니라 계산식이라 CSV에 없다 → 소득세 0원으로 두고 '간이세액표 구간 없음' 경고."""
    from app.core import config
    t = pc.TaxTable.from_csv(config.RULES_DIR / "2026" / "simple_tax_table.csv")
    r = pc.calc_employee(emp(base=12_000_000), RULES, t, PERIOD)
    assert r["tax_in_range"] is False and r["income_tax"] == 0
    found = pc.find_anomalies([r], [], None, RULES, PERIOD, t)
    msg = next(a for a in found if a["category"] == "간이세액표 구간 없음")["message"]
    assert "770,000원 이상" in msg and "계산식" in msg


def test_가족_11명_초과는_안내문4의_계산식():
    """(11명 세액) − (10명 세액 − 11명 세액) × 초과 인원, 음수면 0. 9,980~10,000천원: 10명 988,650 / 11명 958,650 → 차 30,000"""
    from app.core import config
    t = pc.TaxTable.from_csv(config.RULES_DIR / "2026" / "simple_tax_table.csv")
    assert t.lookup(9_990_000, 12) == (928_650, True)
    assert t.lookup(9_990_000, 13) == (898_650, True)
    assert t.lookup(1_500_000, 14)[0] == 0              # 음수 방지


# ---------------------------------------------------------------- 입력 검증
HEADER_OK = dict(emp_no="E001", name="가", rrn="900101-1******", hire_date=date(2020, 1, 2), base=2_800_000,
                 meal=200_000, car=None, bonus=None, dependents=1, children=0)


def validate(*rows):
    return pc.validate_payroll([(i + 2, dict(r)) for i, r in enumerate(rows)], [])


def test_정상_행은_통과():
    emps, issues = validate(HEADER_OK)
    assert len(emps) == 1 and issues == []
    assert emps[0].car == 0  # 빈 칸 = 0


def test_필수값_누락():
    emps, issues = validate({**HEADER_OK, "rrn": None, "base": ""})
    assert not emps
    msgs = " ".join(i["message"] for i in issues)
    assert "주민번호 누락" in msgs and "기본급 누락" in msgs
    assert all(i["severity"] == "error" for i in issues)


def test_숫자_형식_오류():
    _, issues = validate({**HEADER_OK, "base": "삼백만원"}, {**HEADER_OK, "emp_no": "E002", "meal": -1},
                         {**HEADER_OK, "emp_no": "E003", "dependents": 1.5})
    assert len([i for i in issues if i["category"] == "숫자 형식"]) == 3


def test_콤마_문자열_숫자는_허용():
    emps, issues = validate({**HEADER_OK, "base": "2,800,000"})
    assert not issues and emps[0].base == 2_800_000


def test_중복_사번():
    emps, issues = validate(HEADER_OK, {**HEADER_OK, "name": "나"})
    assert any(i["category"] == "중복 사번" and "행 2" in i["message"] for i in issues)
    assert len(emps) == 1


def test_마스킹_안된_주민번호는_오류이며_원본을_남기지_않는다():
    _, issues = validate({**HEADER_OK, "rrn": "900101-1234567"})
    bad = [i for i in issues if i["category"] == "주민번호"]
    assert bad and "1234567" not in bad[0]["message"] + bad[0]["row_ref"]


def test_자녀수가_부양가족수보다_많으면_오류():
    _, issues = validate({**HEADER_OK, "dependents": 1, "children": 2})
    assert any(i["category"] == "논리 오류" for i in issues)


def test_필수_열이_없으면_오류():
    _, issues = pc.validate_payroll([], ["emp_no"])
    assert issues and "사번" in issues[0]["message"]


def test_소득자_검증():
    ok = dict(kind="사업소득", name="가", rrn="900101-1******", pay_date=date(2026, 10, 25), amount=1_000_000,
              income_type="")
    payees, issues = pc.validate_payees([(2, ok), (3, {**ok, "kind": "근로소득"}), (4, {**ok, "amount": "abc"})], [])
    assert len(payees) == 1 and len(issues) == 2


# ---------------------------------------------------------------- 이상항목
def _anoms(e, prev=None, **kw):
    r = pc.calc_employee(e, RULES, TABLE, PERIOD)
    return pc.find_anomalies([r], [], prev, RULES, PERIOD, TABLE)


def _cats(a):
    return [x["category"] for x in a]


def test_전월대비_30퍼센트_경계():
    e = emp(base=2_600_000)  # 총지급 2,600,000
    assert "전월 대비 변동" in _cats(_anoms(e, {"E001": 2_000_000}))      # +30.0% → 플래그
    assert "전월 대비 변동" not in _cats(_anoms(e, {"E001": 2_010_000}))  # +29.35% → 없음
    assert "전월 대비 변동" not in _cats(_anoms(e, {"E001": 3_700_000}))  # 감소 -29.7% → 기준 미만


def test_전월대비_감소도_플래그():
    e = emp(base=2_000_000)
    assert "전월 대비 변동" in _cats(_anoms(e, {"E001": 3_000_000}))  # -33.3%


def test_최저임금_미달_의심():
    """테스트 규칙: 시급 10,000 × 200시간 = 2,000,000. 기본급 1,990,000 → 시급 9,950 < 10,000"""
    assert "최저임금 미달 의심" in _cats(_anoms(emp(base=1_990_000)))
    assert "최저임금 미달 의심" not in _cats(_anoms(emp(base=2_000_000)))


def test_국민연금_상한_해당자():
    assert "국민연금 상한 해당" in _cats(_anoms(emp(base=6_000_000)))
    assert "국민연금 상한 해당" not in _cats(_anoms(emp(base=5_999_000)))


def test_입사월과_표_구간밖과_전월없음():
    new = emp(hire=date(2026, 10, 1), no="N1")
    cats = _cats(_anoms(new, {"E999": 1}))
    assert "입사월" in cats and "전월 자료 없음" not in cats  # 신규 입사자는 전월 자료 없음이 정상
    old = emp(base=9_000_000)
    cats = _cats(_anoms(old, {"X": 1}))
    assert "전월 자료 없음" in cats and "간이세액표 구간 없음" in cats
