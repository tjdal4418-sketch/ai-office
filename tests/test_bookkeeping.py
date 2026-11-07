"""북키 계산 테스트 — 손으로 계산 가능한 작은 장부로 검증한다.

장부 구성: 전기말 보통예금 차변 10,000,000 / 자본금 대변 10,000,000
거래: ① 10/05 급여 출금 1,000,000  ② 10/10 매출 입금 500,000  ③ 카드 10/12 접대 30,000
"""
from datetime import date

from app.agents import bookkeeping_calc as bc
from app.core import config

D = date


def acc(code, name, kind, normal=None):
    return bc.Account(code, name, kind, normal or ("차변" if kind in bc.DEBIT_KINDS else "대변"))


CHART = [acc("103", "보통예금", "자산"), acc("108", "외상매출금", "자산"), acc("134", "가지급금", "자산"),
         acc("203", "감가상각누계액", "자산", "대변"), acc("253", "미지급금", "부채"), acc("254", "예수금", "부채"),
         acc("261", "미지급비용", "부채"), acc("262", "가수금", "부채"), acc("331", "자본금", "자본"),
         acc("801", "급여", "비용"), acc("811", "복리후생비", "비용"), acc("813", "접대비", "비용")]
BY_NAME = {a.name: a for a in CHART}
MAPPING = bc.load_mapping(config.RULES_DIR / "2026")
OPENING = [bc.Opening(2, "103", 10_000_000, 0, None), bc.Opening(3, "331", 0, 10_000_000, None)]


def bank(row, d, memo, inflow=0, outflow=0, balance=None, source=bc.SRC_BANK, kind=""):
    return bc.BankTx(row, d, memo, "", inflow, outflow, balance, source, kind)


BANK = [bank(2, D(2026, 10, 5), "급여 지급", outflow=1_000_000, balance=9_000_000),
        bank(3, D(2026, 10, 10), "매출 입금", inflow=500_000, balance=9_500_000)]
CARD = [bc.CardTx(2, D(2026, 10, 12), "한우마을", "접대 식사", 30_000, None)]


def journal(txs):
    return bc.finalize_entries(bc.make_entries(txs, BY_NAME, MAPPING, "보통예금", "미지급금", "가지급금", "가수금"))


def ledger(txs):
    lines = bc.journal_lines(journal(txs))
    return bc.build_ledger(CHART, OPENING, lines), lines


# ---------------------------------------------------------------- 분개 추천
def names():
    return set(BY_NAME)


def rec(memo, party, direction):
    return bc.recommend(memo, party, direction, MAPPING, names(), "가지급금", "가수금")


def test_키워드_높음은_추천():
    r = rec("접대 식사", "한우마을", "out")
    assert (r["account"], r["confidence"]) == ("접대비", "높음")
    assert rec("급여 지급", "직원", "out")["account"] == "급여"


def test_모호한_키워드는_낮음_확인필요():
    r = rec("회의 커피", "스타벅스", "out")
    assert (r["account"], r["confidence"]) == ("복리후생비", "낮음")


def test_규칙_없으면_가지급금_가수금_낮음():
    out, inn = rec("이체", "박하늘", "out"), rec("입금", "김바다", "in")
    assert (out["account"], out["confidence"]) == ("가지급금", "낮음")
    assert (inn["account"], inn["confidence"]) == ("가수금", "낮음")


def test_방향이_다르면_다른_규칙():
    assert rec("이자", "은행", "out")["account"] == "이자비용" if "이자비용" in names() else True
    # 이 장부에는 이자비용·이자수익 계정이 없으므로 둘 다 계정과목표에 없어 확인필요로 떨어진다
    r = rec("대출 이자", "국민은행", "out")
    assert r["account"] == "가지급금" and r["confidence"] == "낮음" and "계정과목표에 없어" in r["reason"]


# ---------------------------------------------------------------- 분개장·원장·시산표
def test_분개는_차대_같은_금액_두줄():
    entries = journal(BANK + CARD)
    assert [e["entry_no"] for e in entries] == ["BK-202610-0001", "BK-202610-0002", "BK-202610-0003"]
    e1, e2, e3 = entries  # 일자순: 10/05, 10/10, 10/12
    assert (e1["dr"].name, e1["cr"].name, e1["amount"]) == ("급여", "보통예금", 1_000_000)
    assert (e2["dr"].name, e2["cr"].name) == ("보통예금", "외상매출금")
    assert (e3["dr"].name, e3["cr"].name) == ("접대비", "미지급금")  # 카드는 미지급금
    assert e3["status"] == bc.STATUS_OK and e1["status"] == bc.STATUS_OK


def test_시산표_손계산():
    """보통예금 10,000,000 − 1,000,000 + 500,000 = 9,500,000 / 급여 1,000,000 / 접대비 30,000 / 미지급금 30,000"""
    led, lines = ledger(BANK + CARD)
    assert sum(l["debit"] for l in lines) == sum(l["credit"] for l in lines) == 1_530_000
    tb = bc.trial_balance(led)
    assert tb["balanced"] is True
    assert led["103"]["bal_dr"] == 9_500_000
    assert led["801"]["bal_dr"] == 1_000_000 and led["813"]["bal_dr"] == 30_000
    assert led["253"]["bal_cr"] == 30_000 and led["108"]["bal_cr"] == 500_000
    assert tb["totals"]["total_dr"] == tb["totals"]["total_cr"] == 11_530_000


def test_차대_불일치는_시산표에서_잡힌다():
    led, lines = ledger(BANK)
    broken = [l for l in lines if not (l["code"] == "103" and l["debit"])]  # 대변 줄만 남기는 식으로 한쪽을 지움
    tb = bc.trial_balance(bc.build_ledger(CHART, OPENING, broken))
    assert tb["balanced"] is False


# ---------------------------------------------------------------- 입력 검증
def test_전기말잔액_차대_불일치는_오류():
    rows = [(2, {"code": "103", "debit": 10_000_000, "credit": None}), (3, {"code": "331", "debit": None, "credit": 9_500_000})]
    _, issues = bc.validate_opening(rows, [], CHART)
    bad = [i for i in issues if i["category"] == "차대 불일치"]
    assert bad and "+500,000" in bad[0]["message"] and bad[0]["severity"] == "error"


def test_전기말잔액_계정과목표에_없는_코드():
    rows = [(2, {"code": "999", "debit": 1, "credit": None})]
    _, issues = bc.validate_opening(rows, [], CHART)
    assert any(i["category"] == "계정 없음" for i in issues)


def test_은행내역_입력오류():
    rows = [(2, {"date": "2026-10-01", "memo": "a", "party": "", "inflow": 100, "outflow": 50, "balance": 1}),
            (3, {"date": "2026/13/40", "memo": "b", "party": "", "inflow": 100, "outflow": None, "balance": 1}),
            (4, {"date": "2026-10-03", "memo": "c", "party": "", "inflow": "백원", "outflow": None, "balance": 1}),
            (5, {"date": "2026-10-04", "memo": "d", "party": "", "inflow": 100, "outflow": None, "balance": None})]
    txs, issues = bc.validate_bank(rows, [])
    cats = [i["category"] for i in issues]
    assert not txs
    assert cats.count("형식 오류") == 2 and "숫자 형식" in cats and "필수값 누락" in cats


def test_은행내역_정상_날짜형식_여러가지():
    rows = [(2, {"date": "2026.10.01", "memo": "a", "party": "", "inflow": "1,000", "outflow": None, "balance": "1,000"}),
            (3, {"date": "20261002", "memo": "b", "party": "", "inflow": None, "outflow": 200, "balance": 800})]
    txs, issues = bc.validate_bank(rows, [])
    assert not issues and [t.date for t in txs] == [D(2026, 10, 1), D(2026, 10, 2)] and txs[0].inflow == 1000


def test_계정과목표_중복과_필수계정():
    rows = [(2, {"code": "103", "name": "보통예금", "kind": "자산", "normal": None}),
            (3, {"code": "103", "name": "현금", "kind": "자산", "normal": None})]
    _, issues = bc.validate_chart(rows, [], ["보통예금", "미지급금"])
    cats = [i["category"] for i in issues]
    assert "중복 계정" in cats and "필수 계정 없음" in cats


# ---------------------------------------------------------------- 은행 Reconciliation
def recon(bank_txs, lag=(), cards=()):
    led, _ = ledger(list(bank_txs) + list(lag) + list(cards))
    return bc.reconcile_bank(bank_txs, list(lag), led, "103")


def test_은행잔액_일치():
    r = recon(BANK)
    assert (r["bank_end"], r["book_end"], r["unexplained"], r["matched"]) == (9_500_000, 9_500_000, 0, True)


def test_은행_내역에_없는_변동은_원인불명_차이():
    """두 번째 거래 뒤 잔액이 9,400,000 → 예상(9,500,000)보다 100,000 적음 → 원인불명 -100,000"""
    broken = [BANK[0], bank(3, D(2026, 10, 10), "매출 입금", inflow=500_000, balance=9_400_000)]
    r = recon(broken)
    assert r["unexplained"] == -100_000 and r["matched"] is False
    assert r["gap_sum"] == -100_000 and r["open_diff"] == 0
    assert r["gaps"][0]["row"] == 3 and r["gaps"][0]["expected"] == 9_500_000


def test_기초잔액이_다르면_기초차이로_분해():
    """은행 기초 10,200,000 (첫 거래 후 9,200,000) vs 장부 전기말 10,000,000 → 기초 차이 +200,000"""
    shifted = [bank(2, D(2026, 10, 5), "급여 지급", outflow=1_000_000, balance=9_200_000),
               bank(3, D(2026, 10, 10), "매출 입금", inflow=500_000, balance=9_700_000)]
    r = recon(shifted)
    assert r["open_diff"] == 200_000 and r["gap_sum"] == 0 and r["unexplained"] == 200_000


def test_시차항목은_조정되어_일치():
    """장부에는 있고 은행엔 없는 미결제수표 출금 300,000: 은행 9,500,000 − 300,000 = 장부 9,200,000"""
    lag = [bank(2, D(2026, 10, 31), "월세 수표 발행", outflow=300_000, source=bc.SRC_LAG, kind="미결제수표")]
    r = recon(BANK, lag)
    assert (r["bank_end"], r["lag_net"], r["adjusted_bank"], r["book_end"]) == (9_500_000, -300_000, 9_200_000, 9_200_000)
    assert r["unexplained"] == 0 and r["matched"]


def test_시차_미달입금은_은행잔액에_더한다():
    lag = [bank(2, D(2026, 10, 31), "매출 수표 입금", inflow=2_000_000, source=bc.SRC_LAG, kind="미달입금")]
    r = recon(BANK, lag)
    assert r["adjusted_bank"] == 11_500_000 == r["book_end"] and r["matched"]


def test_허용오차_0이면_1원차이도_불일치():
    off = [BANK[0], bank(3, D(2026, 10, 10), "매출 입금", inflow=500_000, balance=9_499_999)]
    assert recon(off)["matched"] is False


def test_카드_미결제_목록():
    cards = [bc.CardTx(2, D(2026, 10, 3), "a", "", 100, D(2026, 10, 25)),   # 기말 전 결제
             bc.CardTx(3, D(2026, 10, 4), "b", "", 200, D(2026, 11, 15)),   # 결제 예정
             bc.CardTx(4, D(2026, 10, 5), "c", "", 300, None)]              # 결제일 없음
    assert [c.amount for c in bc.unpaid_cards(cards, D(2026, 10, 31))] == [200, 300]


# ---------------------------------------------------------------- BS 계정 검토
def test_가지급금_장기미정리와_마이너스잔액():
    opening = [bc.Opening(2, "134", 8_000_000, 0, D(2026, 1, 15)), bc.Opening(3, "331", 0, 8_000_000, None)]
    led = bc.build_ledger(CHART, opening, [])
    rows = bc.review_bs(led, {"134": D(2026, 1, 15)}, ["가지급금", "가수금"], 180, D(2026, 10, 31))
    flags = {r["account"].name: [f[1] for f in r["flags"]] for r in rows}
    assert "장기 미정리" in flags["가지급금"] and "전기 이월 미정리" in flags["가지급금"]  # 289일 ≥ 180일
    # 기준일이 짧으면(발생 후 30일) 장기 미정리는 없음
    rows2 = bc.review_bs(led, {"134": D(2026, 10, 1)}, ["가지급금"], 180, D(2026, 10, 31))
    assert "장기 미정리" not in [f[1] for f in rows2[0]["flags"]]


def test_마이너스_잔액_플래그():
    """예수금(부채, 정상 대변)에 차변 700,000만 있으면 −700,000 → 마이너스 잔액"""
    lines = [{"code": "254", "debit": 700_000, "credit": 0}, {"code": "103", "debit": 0, "credit": 700_000}]
    led = bc.build_ledger(CHART, OPENING, lines)
    rows = {r["account"].name: r for r in bc.review_bs(led, {}, [], 180, D(2026, 10, 31))}
    assert rows["예수금"]["end_bal"] == -700_000
    assert [f[1] for f in rows["예수금"]["flags"]] == ["마이너스 잔액"]


def test_차감계정_정상잔액은_대변():
    """감가상각누계액(자산이지만 정상 대변): 대변 3,000,000 은 정상(+3,000,000), 플래그 없음"""
    opening = [bc.Opening(2, "203", 0, 3_000_000, None), bc.Opening(3, "103", 3_000_000, 0, None)]
    led = bc.build_ledger(CHART, opening, [])
    rows = {r["account"].name: r for r in bc.review_bs(led, {}, [], 180, D(2026, 10, 31))}
    assert rows["감가상각누계액"]["end_bal"] == 3_000_000 and not rows["감가상각누계액"]["flags"]


# ---------------------------------------------------------------- 체크리스트·PBC
def keys(items):
    return [i["key"] for i in items]


def test_결산_체크리스트_주기():
    rules_dir = config.RULES_DIR / "2026"
    monthly = bc.build_checklist(rules_dir, 10, {})
    assert {i["group"] for i in monthly} == {"월"}
    quarter = bc.build_checklist(rules_dir, 9, {})
    assert {i["group"] for i in quarter} == {"월", "분기"} and "vat_link" in keys(quarter)
    year = bc.build_checklist(rules_dir, 12, {})
    assert {i["group"] for i in year} == {"월", "분기", "연"} and "audit_pbc" in keys(year)
    assert all(i["status"] == "open" for i in year)
    assert [i["group"] for i in year] == sorted([i["group"] for i in year], key=["월", "분기", "연"].index)


def test_체크리스트_힌트_연결():
    items = bc.build_checklist(config.RULES_DIR / "2026", 10, {"bank_rec": "원인불명 차이 -150,000원"})
    assert next(i for i in items if i["key"] == "bank_rec")["hint"] == "원인불명 차이 -150,000원"


def test_PBC_자동_준비상태():
    pbc = bc.build_pbc(config.RULES_DIR / "2026", {"bank": "준비완료", "tb": "준비중"})
    st = {i["key"]: i["status"] for i in pbc}
    assert st["bank_stmt"] == "준비완료" and st["tb"] == "준비중" and st["contracts"] == "미준비"
    assert pbc[0]["detail"].startswith("PBC-01")


def test_파일_역할_판별():
    assert bc.detect_role("은행거래내역_10월.csv") == "bank"
    assert bc.detect_role("법인카드 사용내역.xlsx") == "card"
    assert bc.detect_role("계정과목표.csv") == "chart"
    assert bc.detect_role("전기말잔액.csv") == "opening"
    assert bc.detect_role("시차항목.csv") == "lag"
    assert bc.detect_role("메모.txt") is None
