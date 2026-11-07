"""배트 계산 테스트 — 손으로 계산 가능한 작은 데이터로 검증한다.

⚠ 여기서 쓰는 세율·가산세율·키워드는 '테스트 전용 가정값'(10%, 1%, 2% …)이며 법령 값이 아니다.
"""
from datetime import date
from decimal import Decimal

from app.agents import vat_calc as vc
from app.core import config
from app.core.rules import Rules

D = date


def L(value):
    return {"value": value, "source": "테스트 가정값", "year": 2026, "verified": False, "verified_on": None}


RULES = Rules({"vat": {
    "standard_rate": L(0.1),
    "matching": {"amount_tolerance": L(0)},
    "invoice": {"issue_deadline_day": L(10)},
    "penalty": {"late_issue_rate": L(0.01), "unissued_rate": L(0.02)},
    "non_deductible": {"entertainment": L(["접대", "골프"]), "vehicle": L(["승용차", "주유"]),
                       "exempt": L(["면세용"]), "personal": L(["생활용품"])},
}}, year=2026)


def inv(row, kind, wd, biz, supply, tax=None, issue=None, item="", name="", appr=""):
    tax = int(supply * 0.1) if tax is None else tax
    return vc.Invoice(row, kind, wd, issue or wd, appr, biz, name or biz, supply, tax, supply + tax, item, "")


def led(row, kind, d, biz, supply, tax=None, account="", memo="", name=""):
    tax = int(supply * 0.1) if tax is None else tax
    return vc.LedgerEntry(row, d, kind, name or biz, biz, account, memo, supply, tax)


def card(row, d, name, biz, supply, tax, memo=""):
    return vc.CardPurchase(row, d, name, biz, supply, tax, supply + tax, memo)


B = {n: str(n) * 10 for n in range(1, 9)}  # 1111111111 형태의 가짜 사업자번호


# ---------------------------------------------------------------- 사업자번호·기간
def test_사업자번호_정규화():
    assert vc.norm_biz("123-45-67890") == "1234567890"
    assert vc.norm_biz("12-345") is None and vc.norm_biz(None) is None
    assert vc.fmt_biz("1234567890") == "123-45-67890"


def test_과세기간_분기와_신고기한():
    assert vc.detect_period([D(2026, 7, 1), D(2026, 9, 30)]) == (2026, 3)
    assert vc.period_label((2026, 3)) == "2026년 2기 예정신고 (7~9월)"
    assert vc.period_bounds((2026, 4)) == (D(2026, 10, 1), D(2026, 12, 31))
    rules_dir = config.RULES_DIR / "2026"
    today = D(2026, 11, 7)
    # 현재 rules/2026/deadlines.yaml 기준 (검증 전 값): 예정 4/25·10/25, 확정 7/25·다음해 1/25 — 토·일이면 다음 월요일로 순연
    assert vc.period_deadline(rules_dir, (2026, 1), today)["date"] == D(2026, 4, 27)   # 4/25 토요일
    assert vc.period_deadline(rules_dir, (2026, 2), today)["date"] == D(2026, 7, 27)   # 7/25 토요일
    q3 = vc.period_deadline(rules_dir, (2026, 3), today)
    assert q3["original_date"] == D(2026, 10, 25) and q3["date"] == D(2026, 10, 26) and q3["d_day"] == -12
    assert q3["verified"] is True and "일요일" in q3["shift_reason"]   # 부가가치세법 제48조① — 2026-11-07 확인
    q4 = vc.period_deadline(rules_dir, (2026, 4), today)
    assert q4["date"] == D(2027, 1, 25) and q4["d_day"] == 79


# ---------------------------------------------------------------- 입력 검증
def row(**kw):
    base = {"write_date": "2026-07-05", "issue_date": "2026-07-05", "approval_no": "A1", "buyer_biz": "111-11-11111",
            "buyer_name": "가", "supply": 1_000_000, "tax": 100_000, "total": 1_100_000, "item": "x"}
    base.update(kw)
    return base


def validate(*rows, kind=vc.SALES):
    return vc.validate_invoices([(i + 2, r) for i, r in enumerate(rows)], [], kind, Decimal("0.1"))


def test_정상_세금계산서():
    invs, issues = validate(row())
    assert len(invs) == 1 and not issues
    assert (invs[0].biz, invs[0].supply, invs[0].tax, invs[0].kind) == ("1111111111", 1_000_000, 100_000, vc.SALES)


def test_입력오류_종류별():
    _, issues = validate(row(approval_no="A1"), row(approval_no="A1"),                    # 승인번호 중복(행 3)
                         row(approval_no="A3", buyer_biz="33-333"),                      # 사업자번호 형식(행 4)
                         row(approval_no="A4", total=1_101_000),                         # 합계 불일치(행 5)
                         row(approval_no="A5", write_date="9월2일"),                    # 작성일 형식(행 6)
                         row(approval_no="A6", supply="백만원"),                         # 숫자 형식(행 7)
                         row(approval_no="A7", write_date=None))                         # 필수값 누락(행 8)
    cats = {i["category"]: i["row_ref"] for i in issues if i["severity"] == "error"}
    assert cats["중복 승인번호"].endswith("행 3") and cats["사업자번호"].endswith("행 4")
    assert cats["합계 불일치"].endswith("행 5") and cats["형식 오류"].endswith("행 6")
    assert cats["숫자 형식"].endswith("행 7") and cats["필수값 누락"].endswith("행 8")


def test_세액_점검_경고와_안내():
    _, issues = validate(row(approval_no="B1", tax=90_000, total=1_090_000),            # 세율 불일치 → 경고
                         row(approval_no="B2", tax=0, total=1_000_000),                 # 세액 0 → 영세율·면세 확인
                         row(approval_no="B3", supply=-500_000, tax=-50_000, total=-550_000))  # 수정세금계산서
    cats = {i["category"]: i["severity"] for i in issues}
    assert cats == {"세액 점검": "warning", "세액 0원": "info", "수정세금계산서": "info"}


def test_매입은_공급자_열을_상대방으로():
    r = {"write_date": "2026-07-05", "supplier_biz": "222-22-22222", "supplier_name": "공급사", "supply": 100, "tax": 10}
    invs, issues = vc.validate_invoices([(2, r)], [], vc.PURCHASE, Decimal("0.1"))
    assert not issues and invs[0].biz == "2222222222" and invs[0].name == "공급사"


def test_필수_열이_없으면_오류():
    _, issues = vc.validate_invoices([], ["write_date", "buyer_biz", "partner_biz"], vc.SALES, Decimal("0.1"))
    assert any("작성일자" in i["message"] for i in issues) and any("사업자등록번호" in i["message"] for i in issues)


# ---------------------------------------------------------------- 대사 4분류
def statuses(res):
    return [(r["status"], (r["invoice"] or r["ledger"]).row) for r in res]


def test_네_가지_분류():
    invoices = [inv(2, vc.SALES, D(2026, 7, 1), B[1], 100_000),                 # 장부와 일치
                inv(3, vc.SALES, D(2026, 7, 2), B[2], 500_000),                 # 공급가액 다름
                inv(4, vc.SALES, D(2026, 7, 3), B[3], 300_000, tax=30_000),     # 세액만 다름
                inv(5, vc.SALES, D(2026, 7, 4), B[4], 400_000)]                 # 장부에 없음
    ledger = [led(2, vc.SALES, D(2026, 7, 1), B[1], 100_000),
              led(3, vc.SALES, D(2026, 7, 2), B[2], 450_000),
              led(4, vc.SALES, D(2026, 7, 3), B[3], 300_000, tax=33_000),
              led(5, vc.SALES, D(2026, 7, 5), B[5], 200_000)]                   # 세금계산서에 없음
    res = vc.reconcile(invoices, ledger, vc.SALES)
    got = {(r["status"], (r["invoice"] or r["ledger"]).row) for r in res}
    assert got == {(vc.MATCH, 2), (vc.DIFF, 3), (vc.DIFF, 4), (vc.NO_LEDGER, 5), (vc.NO_INVOICE, 5)}
    notes = {(r["status"], (r["invoice"] or r["ledger"]).row): r["note"] for r in res}
    assert "+50,000" in notes[(vc.DIFF, 3)] and "세액 차이" in notes[(vc.DIFF, 4)]
    assert [r["status"] for r in res] == sorted([r["status"] for r in res], key=vc.STATUSES.index)  # 분류 순서대로 정렬


def test_사업자번호가_다르면_같은_금액이어도_불일치():
    res = vc.reconcile([inv(2, vc.SALES, D(2026, 7, 1), B[1], 100_000)], [led(2, vc.SALES, D(2026, 7, 1), B[2], 100_000)], vc.SALES)
    assert sorted(r["status"] for r in res) == sorted([vc.NO_LEDGER, vc.NO_INVOICE])


def test_날짜만_다르면_두건으로_나오되_참고_안내():
    res = vc.reconcile([inv(2, vc.PURCHASE, D(2026, 7, 1), B[1], 600_000)], [led(2, vc.PURCHASE, D(2026, 7, 9), B[1], 600_000)], vc.PURCHASE)
    assert len(res) == 2 and all("날짜만 다름" in r["note"] for r in res)


def test_일대일_대응():
    """같은 세금계산서 2장, 장부 1건 → 1건 일치 + 1건 장부누락"""
    two = [inv(2, vc.SALES, D(2026, 7, 1), B[1], 100_000), inv(3, vc.SALES, D(2026, 7, 1), B[1], 100_000)]
    res = vc.reconcile(two, [led(2, vc.SALES, D(2026, 7, 1), B[1], 100_000)], vc.SALES)
    assert sorted(r["status"] for r in res) == sorted([vc.MATCH, vc.NO_LEDGER])


def test_허용오차():
    i, l = [inv(2, vc.SALES, D(2026, 7, 1), B[1], 100_005)], [led(2, vc.SALES, D(2026, 7, 1), B[1], 100_000)]
    # 공급가액 5원 차이(세액은 둘 다 10,000원): 허용오차 10원이면 일치, 0원이면 금액차이
    assert vc.reconcile(i, l, vc.SALES, tolerance=10)[0]["status"] == vc.MATCH
    assert vc.reconcile(i, l, vc.SALES, tolerance=0)[0]["status"] == vc.DIFF


def test_매출_매입을_섞지_않는다():
    i = [inv(2, vc.SALES, D(2026, 7, 1), B[1], 100_000)]
    res = vc.reconcile(i, [led(2, vc.PURCHASE, D(2026, 7, 1), B[1], 100_000)], vc.SALES)
    assert sorted(r["status"] for r in res) == [vc.NO_LEDGER]  # 매입 장부는 매출 대사에 안 들어감


# ---------------------------------------------------------------- 불공제 후보
def test_키워드_유형_순서():
    hits = vc.find_keywords("거래처 골프 접대 + 승용차 주유", RULES)
    assert [c for c, _ in hits] == ["entertainment", "entertainment", "vehicle", "vehicle"]
    assert vc.find_keywords("원재료 매입", RULES) == []


def test_세금계산서_후보는_장부_계정과목도_본다():
    results = [
        {"kind": vc.PURCHASE, "status": vc.MATCH, "invoice": inv(2, vc.PURCHASE, D(2026, 7, 1), B[1], 1_000_000, item="식사"),
         "ledger": led(2, vc.PURCHASE, D(2026, 7, 1), B[1], 1_000_000, account="접대비", memo="거래처 접대"), "note": ""},          # 장부 적요로 후보
        {"kind": vc.PURCHASE, "status": vc.MATCH, "invoice": inv(3, vc.PURCHASE, D(2026, 7, 2), B[2], 500_000, item="원재료"),
         "ledger": led(3, vc.PURCHASE, D(2026, 7, 2), B[2], 500_000, account="원재료"), "note": ""},                           # 후보 아님
        {"kind": vc.PURCHASE, "status": vc.NO_LEDGER, "invoice": inv(4, vc.PURCHASE, D(2026, 7, 3), B[3], 200_000, item="승용차 렌트"),
         "ledger": None, "note": ""},                                                                                          # 장부 없어도 품목으로 후보
        {"kind": vc.PURCHASE, "status": vc.NO_INVOICE, "invoice": None,
         "ledger": led(5, vc.PURCHASE, D(2026, 7, 4), B[4], 100_000, memo="접대"), "note": ""},                                # 세금계산서 없음 → 후보 제외
        {"kind": vc.PURCHASE, "status": vc.MATCH, "invoice": inv(6, vc.PURCHASE, D(2026, 7, 5), B[5], 100_000, tax=0, item="접대"),
         "ledger": None, "note": ""},                                                                                          # 세액 0 → 후보 제외
    ]
    cands = vc.find_candidates(results, [], RULES)
    assert [(c["ref"], c["types"]) for c in cands] == [("매입 세금계산서 행 2", ["기업업무추진비 관련"]),
                                                       ("매입 세금계산서 행 4", ["비영업용 소형승용차 관련"])]
    assert cands[0]["tax"] == 100_000 and cands[0]["account"] == "접대비"


def test_카드_후보와_공제요건():
    cards = [card(2, D(2026, 9, 1), "한빛주유소", "2222222222", 77_273, 7_727, "주유"),     # 후보
             card(3, D(2026, 9, 2), "개인택시", "", 56_000, 0, "주유"),                   # 사업자번호·세액 없음 → 후보 아님
             card(4, D(2026, 9, 3), "문구점", "3333333333", 10_000, 1_000, "문구")]
    cands = vc.find_candidates([], cards, RULES)
    assert [c["ref"] for c in cands] == ["카드매입 행 2"] and cands[0]["source"] == "카드"
    assert [vc.card_eligible(c) for c in cards] == [True, False, True]


# ---------------------------------------------------------------- 신고서 요약
def _summary_inputs():
    results = [
        {"kind": vc.SALES, "status": vc.MATCH, "invoice": inv(2, vc.SALES, D(2026, 7, 1), B[1], 10_000_000), "ledger": led(2, vc.SALES, D(2026, 7, 1), B[1], 10_000_000), "note": ""},
        {"kind": vc.PURCHASE, "status": vc.MATCH, "invoice": inv(2, vc.PURCHASE, D(2026, 7, 2), B[2], 3_000_000, item="원재료"), "ledger": led(2, vc.PURCHASE, D(2026, 7, 2), B[2], 3_000_000), "note": ""},
        {"kind": vc.PURCHASE, "status": vc.NO_LEDGER, "invoice": inv(3, vc.PURCHASE, D(2026, 7, 3), B[3], 1_000_000, item="접대 식사"), "ledger": None, "note": ""},
    ]
    cards = [card(2, D(2026, 9, 1), "문구점", B[4], 100_000, 10_000, "문구"),
             card(3, D(2026, 9, 2), "한빛주유소", B[5], 50_000, 5_000, "주유"),
             card(4, D(2026, 9, 3), "개인택시", "", 20_000, 0, "택시")]
    return results, cards


def test_신고서_요약_손계산():
    """매출세액 1,000,000 / 매입세액 400,000 중 접대 100,000 은 불공제 후보 → 공제 300,000
    카드 세액 15,000 중 주유 5,000 후보 → 카드 공제 10,000 / 납부 = 1,000,000 − 300,000 − 10,000 = 690,000
    후보를 모두 공제로 보면 690,000 − 100,000 − 5,000 = 585,000"""
    results, cards = _summary_inputs()
    cands = vc.find_candidates([r for r in results if r["kind"] == vc.PURCHASE], cards, RULES)
    s = vc.summarize(results, cands, cards)
    assert (s["sales_supply"], s["sales_tax"]) == (10_000_000, 1_000_000)
    assert (s["purchase_tax"], s["nd_invoice_tax"], s["deductible_invoice_tax"]) == (400_000, 100_000, 300_000)
    assert (s["card_tax"], s["card_nd_tax"], s["card_deductible"]) == (15_000, 5_000, 10_000)
    assert s["payable"] == 690_000 and s["alt_payable"] == 585_000


def test_환급세액():
    results = [
        {"kind": vc.SALES, "status": vc.MATCH, "invoice": inv(2, vc.SALES, D(2026, 7, 1), B[1], 1_000_000), "ledger": None, "note": ""},
        {"kind": vc.PURCHASE, "status": vc.MATCH, "invoice": inv(2, vc.PURCHASE, D(2026, 7, 2), B[2], 5_000_000), "ledger": None, "note": ""},
    ]
    s = vc.summarize(results, [], [])
    assert s["payable"] == 100_000 - 500_000 == -400_000  # 음수 = 환급


# ---------------------------------------------------------------- 발행 관리
def sales_result(i=None, l=None, status=vc.MATCH):
    return {"kind": vc.SALES, "status": status, "invoice": i, "ledger": l, "note": ""}


def test_지연발행_경계와_가산세_위험():
    """8월 작성 → 발급기한 9/10. 9/10 발급은 정상, 9/11 은 1일 지연: 2,000,000 × 1% = 20,000"""
    on_time = sales_result(inv(2, vc.SALES, D(2026, 8, 18), B[1], 2_000_000, issue=D(2026, 9, 10)))
    late = sales_result(inv(3, vc.SALES, D(2026, 8, 18), B[1], 2_000_000, issue=D(2026, 9, 11)))
    out = vc.issuance_review([on_time, late], RULES, D(2026, 11, 7))
    assert len(out) == 1 and out[0]["type"] == "지연발행 의심"
    assert (out[0]["due"], out[0]["days"], out[0]["penalty"]) == (D(2026, 9, 10), 1, 20_000)


def test_12월_작성분은_다음해_1월_기한():
    r = sales_result(inv(2, vc.SALES, D(2026, 12, 20), B[1], 1_000_000, issue=D(2027, 1, 11)))
    out = vc.issuance_review([r], RULES, D(2027, 2, 1))
    assert out[0]["due"] == D(2027, 1, 10) and out[0]["days"] == 1


def test_미발행_의심은_기한_경과_여부로_가산세_위험():
    """장부 매출 7,000,000 (9/12 작성) → 기한 10/10. 10/10 당일은 아직 위험 0, 10/11 은 1일 경과 → 7,000,000 × 2% = 140,000"""
    r = sales_result(None, led(2, vc.SALES, D(2026, 9, 12), B[1], 7_000_000), vc.NO_INVOICE)
    before = vc.issuance_review([r], RULES, D(2026, 10, 10))[0]
    after = vc.issuance_review([r], RULES, D(2026, 10, 11))[0]
    assert before["type"] == after["type"] == "미발행 의심"
    assert (before["days"], before["penalty"]) == (0, 0) and "D-0" in before["note"]
    assert (after["days"], after["penalty"]) == (1, 140_000) and "가산세 위험" in after["note"]


def test_가산세_금액은_반올림():
    assert vc.penalty_amount(12_345_678, 0.01) == 123_457   # 123,456.78 → 반올림
    assert vc.penalty_amount(150_050, 0.01) == 1_501         # 1,500.5 → 1,501 (엑셀 ROUND 와 같게)


def test_매입은_발행관리_대상_아님():
    r = {"kind": vc.PURCHASE, "status": vc.NO_INVOICE, "invoice": None, "ledger": led(2, vc.PURCHASE, D(2026, 9, 1), B[1], 1_000_000), "note": ""}
    assert vc.issuance_review([r], RULES, D(2026, 12, 1)) == []


def test_파일_역할_판별():
    assert vc.detect_role("매출세금계산서_3분기.csv") == "sales"
    assert vc.detect_role("매입세금계산서.xlsx") == "purchase"
    assert vc.detect_role("장부_매출매입.csv") == "ledger"
    assert vc.detect_role("카드매입내역.csv") == "card"      # '매입'이 있어도 카드가 먼저
    assert vc.detect_role("메모.txt") is None
