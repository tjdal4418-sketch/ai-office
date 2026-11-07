from datetime import date

from app.core import deadlines


def test_upcoming_from_rules_file_sorted_with_dday():
    items = deadlines.upcoming(today=date(2026, 11, 7), limit=3)
    assert len(items) == 3
    assert [i["date"] for i in items] == sorted(i["date"] for i in items)
    assert items[0]["id"] == "withholding_monthly" and items[0]["d_day"] == 3
    # 2026-11-07 법령 MCP 로 확인한 기한 — 확인일이 함께 내려간다
    assert all(i["verified"] is True and i["verified_on"] == "2026-11-07" for i in items)


def _events(start, end):
    return deadlines.events_between(start, end, today=date(2026, 11, 7))


def test_근로소득_간이지급명세서는_매월_다음달_말일():
    """소득세법 제164조의3①1호 — 지급일이 속한 달의 다음 달 말일. 반기(1/31·7/31)가 아니라 매월."""
    evs = [e for e in _events(date(2026, 1, 1), date(2026, 12, 31)) if e["id"] == "simple_statement_wage"]
    assert len(evs) == 12
    # 2026-10-31 은 토요일 → 11/2(월)로 순연
    oct_ev = next(e for e in evs if e["original_date"] == "2026-10-31")
    assert oct_ev["date"] == "2026-11-02" and oct_ev["shifted"] and oct_ev["verified"] is True
    # 2월은 28일이 말일
    assert any(e["original_date"] == "2026-02-28" for e in evs)


def test_기타소득_간이지급명세서와_지급명세서가_추가됐다():
    evs = _events(date(2026, 1, 1), date(2027, 3, 31))
    assert any(e["id"] == "simple_statement_other" and e["original_date"] == "2026-06-30" for e in evs)
    # 기타소득 지급명세서: 다음 연도 2월 말일 (3/10 은 근로·퇴직·사업소득만)
    other = [e for e in evs if e["id"] == "payment_statement_other"]
    assert [e["original_date"] for e in other] == ["2026-02-28", "2027-02-28"]
    annual = [e for e in evs if e["id"] == "payment_statement_annual"]
    assert [e["original_date"] for e in annual] == ["2026-03-10", "2027-03-10"]


def test_말일은_토요일이면_다음_영업일_공휴일_겹치면_한_번_더_순연():
    """2026-02-28(토) → 3/1(일) 삼일절 → 3/2(월) 대체공휴일 → 3/3(화)."""
    ev = next(e for e in _events(date(2026, 2, 1), date(2026, 3, 31)) if e["id"] == "payment_statement_other")
    assert ev["original_date"] == "2026-02-28" and ev["date"] == "2026-03-03" and ev["shifted"]


def test_노동절_5월1일도_기한_순연_사유():
    """국세기본법 제5조①3호 — 5/1(금) 은 노동절이라 다음 영업일 5/4(월)."""
    due, reason = deadlines.shift_to_business_day(date(2026, 5, 1))
    assert due == date(2026, 5, 4) and "노동절" in reason
    assert deadlines.load_holidays(2026)[date(2026, 5, 1)] == "노동절"
    # 공휴일 파일이 없는 해에는 아무것도 하지 않는다
    assert deadlines.load_holidays(1999) == {}


def test_순연_규칙_근거와_검증_정보():
    assert "국세기본법 제5조" in deadlines.shift_source(date(2026, 11, 7))
    assert deadlines.shift_verification(date(2026, 11, 7)) == (True, "2026-11-07")


def test_4대보험_기한도_2차_검증으로_확인됐고_2027년_제도변경_메모가_붙는다():
    evs = {e["id"]: e for e in _events(date(2026, 1, 1), date(2026, 12, 31))}
    assert evs["insurance_pay_monthly"]["verified"] is True and "국민건강보험법 제78조" in evs["insurance_pay_monthly"]["source"]
    wage = evs["insurance_wage_report"]
    assert wage["verified"] is True and wage["verified_on"] == "2026-11-07" and "제21472호" in wage["source"]
    assert "2027" in wage["note"] and "월 보수 신고" in wage["note"]       # 2027년부터 매월 신고로 바뀐다는 메모
    assert evs["withholding_monthly"]["note"] == ""                        # 메모 없는 기한은 빈 문자열
