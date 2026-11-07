"""코택 계산·에이전트 테스트 — 손으로 계산 가능한 한도, 시산표 이어받기, 엑셀 수식.

⚠ 단위 테스트의 한도·요율은 '테스트 전용 가정값'(1%, 0.5%, 5% …)이며 법령 값이 아니다.
   통합 테스트(샘플 데이터)는 rules/2026 의 임시값(검증 전)을 그대로 쓴다.
"""
import asyncio
import json
import shutil

import pytest
from openpyxl import load_workbook

from app.agents import REGISTRY
from app.agents import corptax_calc as cc
from app.core import config, db, state
from app.core.rules import Rules


def L(value):
    return {"value": value, "source": "테스트 가정값", "year": 2026, "verified": False, "verified_on": None}


RULES = Rules({"corptax": {
    "entertainment": {"basic_limit_sme": L(30_000_000), "basic_limit_general": L(10_000_000),
                      "tier1_rate": L(0.01), "tier2_over": L(1_000_000_000), "tier2_rate": L(0.005),
                      "tier3_over": L(5_000_000_000), "tier3_rate": L(0.001)},
    "imputed_interest": {"rate": L(0.05)},
    "bad_debt": {"fixed_rate": L(0.01)},
    "vehicle": {"depreciation_limit": L(8_000_000), "log_threshold": L(15_000_000)},
}}, year=2026)
ITEMS = cc.load_items(config.RULES_DIR / "2026", "tax_adjustment_items.yaml")


def tb(*rows):
    """(이름, 구분, 정상, 금액) → TbRow 목록. 금액은 정상 방향 잔액."""
    out = []
    for i, (name, kind, normal, amt) in enumerate(rows):
        out.append(cc.TbRow(str(100 + i), name, kind, normal, amt if normal == "차변" else 0, amt if normal == "대변" else 0))
    return out


# ---------------------------------------------------------------- 접대비 한도
def test_접대비_한도_손계산_중소기업():
    """수입금액 20억: 10억×1% + 10억×0.5% = 15,000,000 → 중소기업 한도 30,000,000 + 15,000,000 = 45,000,000"""
    e = cc.entertainment_limit(True, 12, 2_000_000_000, RULES)
    assert (e["basic_prorated"], e["revenue_part"], e["limit"]) == (30_000_000, 15_000_000, 45_000_000)


def test_접대비_한도_일반기업과_개월수_환산():
    assert cc.entertainment_limit(False, 12, 2_000_000_000, RULES)["limit"] == 10_000_000 + 15_000_000
    # 6개월 사업연도: 기본한도만 6/12 환산 (수입금액 기준 한도는 그대로)
    assert cc.entertainment_limit(True, 6, 2_000_000_000, RULES)["limit"] == 15_000_000 + 15_000_000


def test_접대비_수입금액_구간_경계():
    assert cc.entertainment_limit(True, 12, 1_000_000_000, RULES)["revenue_part"] == 10_000_000      # 경계 = 1구간 끝
    # 60억: 10억×1% + 40억×0.5% + 10억×0.1% = 10,000,000 + 20,000,000 + 1,000,000
    assert cc.entertainment_limit(True, 12, 6_000_000_000, RULES)["revenue_part"] == 31_000_000
    assert cc.entertainment_limit(True, 12, 0, RULES)["limit"] == 30_000_000


def test_기업업무추진비_수입금액_한도는_구간별_누적_법령값():
    """법인세법 제25조④2호 실제 규칙값(rules/2026): 2구간은 '3천만원 + 100억 초과분 × 0.2%', 3구간은 '1억1천만원 + 500억 초과분 × 0.03%'."""
    from app.core.rules import load_rules
    real = load_rules(2026, ("corptax",))
    part = lambda rev: cc.entertainment_limit(False, 12, rev, real)["revenue_part"]  # noqa: E731
    assert part(10_000_000_000) == 30_000_000                       # 100억 × 0.3% = 3천만원
    assert part(20_000_000_000) == 30_000_000 + 20_000_000          # 3천만 + 100억 × 0.2%
    assert part(50_000_000_000) == 110_000_000                      # 3천만 + 400억 × 0.2% = 1억1천만원
    assert part(60_000_000_000) == 110_000_000 + 3_000_000          # 1억1천만 + 100억 × 0.03%
    assert cc.entertainment_limit(True, 12, 0, real)["limit"] == 36_000_000   # 중소기업 기본한도 3,600만원
    assert cc.entertainment_limit(False, 12, 0, real)["limit"] == 12_000_000  # 일반기업 1,200만원


def test_기업업무추진비와_옛_계정명_접대비를_합산한다():
    """고객사 계정과목표에 '접대비'가 남아 있어도, '기업업무추진비'로 바꿔 쓰는 고객사도 같은 항목으로 잡는다."""
    old = by_key(review(tb(("접대비", "비용", "차변", 40_000_000))))["entertainment"]
    new = by_key(review(tb(("기업업무추진비", "비용", "차변", 40_000_000))))["entertainment"]
    both = by_key(review(tb(("접대비", "비용", "차변", 10_000_000), ("기업업무추진비", "비용", "차변", 30_000_000))))["entertainment"]
    assert old["amount"] == new["amount"] == both["amount"] == 40_000_000
    assert "기업업무추진비" in old["title"] and "접대비" not in old["title"]


def test_인정이자와_대손충당금_한도():
    assert cc.imputed_interest(10_000_000, 12, RULES) == 500_000        # 1천만 × 5%
    assert cc.imputed_interest(10_000_000, 6, RULES) == 250_000         # 6개월
    assert cc.bad_debt_limit(100_000_000, RULES) == 1_000_000           # 1억 × 1%


# ---------------------------------------------------------------- 세무조정 검토표
def review(rows, revenue=2_000_000_000, sme=True, months=12):
    return cc.build_review(rows, RULES, ITEMS, sme, months, revenue)


def by_key(rv):
    return {i["key"]: i for i in rv["items"]}


def test_검토_필요_여부_판정():
    rows = tb(("접대비", "비용", "차변", 50_000_000), ("가지급금", "자산", "차변", 10_000_000),
              ("외상매출금", "자산", "차변", 90_000_000), ("미수금", "자산", "차변", 10_000_000),
              ("대손충당금", "자산", "대변", 1_500_000), ("감가상각비", "비용", "차변", 2_000_000))
    it = by_key(review(rows))
    assert (it["entertainment"]["status"], it["entertainment"]["adjust"]) == (cc.STATUS_ADJUST, 5_000_000)   # 5천만 − 한도 4,500만
    assert (it["imputed"]["status"], it["imputed"]["adjust"]) == (cc.STATUS_ADJUST, 500_000)
    assert (it["bad_debt"]["status"], it["bad_debt"]["adjust"]) == (cc.STATUS_ADJUST, 500_000)           # 한도 100만, 충당금 150만
    assert it["depreciation"]["status"] == cc.STATUS_REVIEW
    assert all(it[k]["status"] == cc.STATUS_NONE for k in ("retirement", "donation", "vehicle", "taxes"))
    assert len(it) == 8


def test_접대비_한도_내이면_검토필요_잔액없으면_해당없음():
    inside = by_key(review(tb(("접대비", "비용", "차변", 40_000_000))))["entertainment"]
    assert (inside["status"], inside["adjust"]) == (cc.STATUS_REVIEW, 0) and "여유 5,000,000원" in inside["summary"]
    assert by_key(review(tb(("현금", "자산", "차변", 1))))["entertainment"]["status"] == cc.STATUS_NONE


def test_대손충당금_미설정도_검토():
    rv = by_key(review(tb(("외상매출금", "자산", "차변", 100_000_000))))["bad_debt"]
    assert rv["status"] == cc.STATUS_REVIEW and "미설정" in rv["summary"]


def test_업무용승용차와_퇴직급여_안내():
    rv = by_key(review(tb(("차량유지비", "비용", "차변", 18_000_000), ("퇴직급여충당부채", "부채", "대변", 30_000_000))))
    assert rv["vehicle"]["status"] == cc.STATUS_REVIEW and "8,000,000" in rv["vehicle"]["summary"] and "15,000,000" in rv["vehicle"]["summary"]
    assert rv["retirement"]["status"] == cc.STATUS_REVIEW and "수기 검토" in rv["retirement"]["summary"]


def test_정상방향_잔액과_수입금액_추정():
    rows = tb(("대손충당금", "자산", "대변", 1_500_000), ("제품매출", "수익", "대변", 700), ("이자수익", "수익", "대변", 50), ("용역매출", "수익", "대변", 300))
    assert rows[0].balance == 1_500_000
    assert cc.revenue_from_tb(rows) == 1_000                      # '매출' 이름이 든 수익 계정만
    assert cc.balance_of(cc.by_name(rows), ["제품매출", "없는계정"]) == 700


# ---------------------------------------------------------------- 시산표 검증
def tbrow(**kw):
    base = {"code": "101", "name": "현금", "kind": "자산", "normal": None, "debit": 100, "credit": None}
    base.update(kw)
    return base


def test_시산표_정상과_차대_불일치():
    ok, issues = cc.validate_tb([(2, tbrow()), (3, tbrow(code="331", name="자본금", kind="자본", debit=None, credit=100))], [])
    assert not issues and len(ok) == 2
    _, issues = cc.validate_tb([(2, tbrow()), (3, tbrow(code="331", name="자본금", kind="자본", debit=None, credit=90))], [])
    bad = [i for i in issues if i["category"] == "차대 불일치"]
    assert bad and "+10원" in bad[0]["message"]


def test_시산표_입력오류():
    _, issues = cc.validate_tb([(2, tbrow()), (3, tbrow()), (4, tbrow(code="102", name="예금", kind="부자")),
                                (5, tbrow(code="103", name="a", debit="백")), (6, tbrow(code="104", name="b", debit=-5))], [])
    cats = {i["category"] for i in issues}
    assert {"중복 계정", "형식 오류", "숫자 형식"} <= cats
    _, issues = cc.validate_tb([], ["kind"])
    assert issues and "구분" in issues[0]["message"]


# ---------------------------------------------------------------- 북키 시산표 이어받기
def make_bk_task(tmp_path, status="review", client="ganasangsa", year=2026, sidecar=True):
    tid = db.create_task("bookkeeping", client, year, {}, client_name="(주)가나상사", title="x")["id"]
    out = config.OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    xlsx = out / f"bookkeeping_{tid}.xlsx"
    xlsx.write_bytes(b"")
    rows = [{"code": "103", "name": "보통예금", "kind": "자산", "normal": "차변", "bal_dr": 1_000, "bal_cr": 0},
            {"code": "331", "name": "자본금", "kind": "자본", "normal": "대변", "bal_dr": 0, "bal_cr": 1_000}]
    if sidecar:
        cc.sidecar_path(xlsx).write_text(json.dumps({"period": [2026, 10], "rows": rows}), encoding="utf-8")
    db.update_task(tid, status=status, output_path=str(xlsx))
    return tid


def test_북키_작업_시산표를_이어받는다(tmp_path):
    tid = make_bk_task(tmp_path)
    task, data = cc.find_upstream("ganasangsa", 2026)
    assert task["id"] == tid and [t.name for t in cc.tb_from_bookkeeping(data)] == ["보통예금", "자본금"]


@pytest.mark.parametrize("kw", [{"status": "error"}, {"client": "daratech"}, {"year": 2025}, {"sidecar": False}])
def test_조건_안_맞는_북키_작업은_이어받지_않는다(tmp_path, kw):
    make_bk_task(tmp_path, **kw)
    assert cc.find_upstream("ganasangsa", 2026) is None


def test_가장_최근_북키_작업을_쓴다(tmp_path):
    make_bk_task(tmp_path)
    newer = make_bk_task(tmp_path, status="done")
    assert cc.find_upstream("ganasangsa", 2026)[0]["id"] == newer


# ---------------------------------------------------------------- 에이전트 통합
GANA = ("ganasangsa", "(주)가나상사")
DARA = ("daratech", "(주)다라테크")
SAMPLE = config.SAMPLE_DIR / "corptax"


def run_ct(client=GANA, year=2026, **params):
    tid = db.create_task("corptax", client[0], year, params, client_name=client[1], title="세무조정 점검")["id"]
    asyncio.run(REGISTRY["corptax"].run(tid))
    return tid, db.get_task(tid)


def cats(tid):
    return [a["message"].split("]")[0].lstrip("[") for a in db.list_anomalies(tid)]


def upload(name, src):
    folder = config.UPLOAD_DIR / "corptax"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, folder / name)


def test_가나상사_샘플_조정필요_3건():
    tid, task = run_ct()
    assert task["status"] == "review" and state.get_state("corptax").status == "review"
    for t in ("조정 필요 3", "검토 필요 5", "기업업무추진비 한도 43,200,000원", "계상 45,500,000원"):
        assert t in task["result"]
    assert cats(tid).count("조정 필요") == 3
    wb = load_workbook(task["output_path"])
    assert wb.sheetnames == ["세무조정 검토표", "한도 계산", "요청자료 목록", "시산표(입력)", "계산근거"]


def test_다라테크는_접대비_한도내_조정필요_없음():
    tid, task = run_ct(DARA)
    assert "조정 필요 0" in task["result"] and "기업업무추진비 한도 38,700,000원" in task["result"]
    assert "조정 필요" not in cats(tid)


def test_중소기업_수입금액_개월수_옵션이_한도에_반영():
    """일반기업·6개월·수입금액 10억: 기본 12,000,000×6/12 = 6,000,000 + 수입금액 기준 1,000,000,000×0.3% = 3,000,000 → 9,000,000"""
    tid, task = run_ct(is_sme=False, months=6, revenue=1_000_000_000)
    assert "기업업무추진비 한도 9,000,000원" in task["result"]


def test_북키_결과를_이어받으면_월단위_안내():
    bk = db.create_task("bookkeeping", "ganasangsa", 2026, {}, client_name=GANA[1], title="x")["id"]
    asyncio.run(REGISTRY["bookkeeping"].run(bk))
    state.reset()
    for a in REGISTRY:
        state.register(a)
    tid, task = run_ct()
    assert task["status"] == "review"
    msgs = " ".join(a["message"] for a in db.list_anomalies(tid))
    assert f"북키 작업 #{bk}" in msgs and "승인 전 초안" in msgs and "월 단위 시산표" in msgs
    assert "수입금액 없음" in cats(tid)  # 북키 시산표에는 매출 계정이 없다 → 입력 안내
    # 가지급금은 북키 시산표(기말 9,350,000원)에서 그대로 이어받아 인정이자 후보로 잡힌다
    assert any("가지급금 9,350,000원" in a["message"] for a in db.list_anomalies(tid))


def test_입력_오류():
    assert run_ct(months=13)[1]["status"] == "error"
    assert run_ct(revenue=-1)[1]["status"] == "error"
    assert run_ct(months="abc")[1]["status"] == "error"


def test_차대_불일치_시산표_업로드는_오류():
    upload("시산표_차대불일치.csv", SAMPLE / "errors" / "시산표_차대불일치.csv")
    tid, task = run_ct()
    assert task["status"] == "error" and task["error_reason"].startswith("입력 오류")
    assert any("[차대 불일치]" in a["message"] and "500,000" in a["message"] for a in db.list_anomalies(tid))


def test_다른_고객사_업로드는_건너뜀():
    folder = config.UPLOAD_DIR / "corptax"
    folder.mkdir(parents=True)
    (folder / "daratech_시산표.csv").write_text("깨진 데이터", encoding="utf-8")
    assert run_ct(GANA)[1]["status"] == "review"


def test_시산표가_어디에도_없으면_안내_오류(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAMPLE_DIR", tmp_path)
    tid, task = run_ct()
    assert task["status"] == "error" and "시산표가 없습니다" in task["error_reason"]


def test_체크리스트와_요청자료_저장():
    tid, _ = run_ct()
    check, pbc = db.list_work_items(tid, "checklist"), db.list_work_items(tid, "pbc")
    assert len(check) == 8 and len(pbc) == 16
    ent = next(i for i in check if i["key"] == "entertainment")
    assert ent["group"] == "조정 필요" and "초과 2,300,000원" in ent["hint"] and ent["status"] == "open"
    assert {i["key"]: i["status"] for i in pbc}["fs"] == "준비중" and {i["status"] for i in pbc} == {"준비중", "미준비"}


def test_엑셀_수식_구조():
    _, task = run_ct()
    wb = load_workbook(task["output_path"])
    rv, lim = wb["세무조정 검토표"], wb["한도 계산"]
    assert rv["D4"].value.startswith("=SUMIF(") and rv["G4"].value == "='한도 계산'!B11"
    assert lim["B9"].value == "=B6+B8" and lim["B11"].value == "=MAX(0,B10-B9)" and lim["B3"].value == "중소기업"
    assert lim.data_validations.dataValidation and wb["요청자료 목록"].data_validations.dataValidation


def calc(path, tmp_path):
    formulas = pytest.importorskip("formulas")
    p = shutil.copy(path, tmp_path / "c.xlsx")
    sol = formulas.ExcelModel().loads(str(p)).finish().calculate()

    def cell(sheet, addr):
        v = sol[f"'[c.xlsx]{sheet.upper()}'!{addr}"].value[0, 0]
        return v if isinstance(v, str) else float(v)
    return cell


def test_수식_결과가_파이썬과_일치(tmp_path):
    pytest.importorskip("formulas")
    _, task = run_ct()
    c = calc(task["output_path"], tmp_path)
    assert (c("한도 계산", "B6"), c("한도 계산", "B8"), c("한도 계산", "B9")) == (36_000_000, 7_200_000, 43_200_000)
    assert (c("한도 계산", "B10"), c("한도 계산", "B11")) == (45_500_000, 2_300_000)
    assert c("한도 계산", "B18") == 430_100                       # 가지급금 인정이자 추정
    assert (c("한도 계산", "B21"), c("한도 계산", "B23"), c("한도 계산", "B25")) == (82_000_000, 820_000, 380_000)
    assert c("세무조정 검토표", "D4") == 45_500_000 and c("세무조정 검토표", "G4") == 2_300_000
    wb = load_workbook(task["output_path"])
    tb = wb["시산표(입력)"]
    chk = next(r for r in range(1, tb.max_row + 1) if tb.cell(row=r, column=1).value == "차대 일치 검증")
    assert c("시산표(입력)", f"B{chk}") == "일치"


def test_중소기업_여부를_바꾸면_한도가_바로_바뀐다(tmp_path):
    pytest.importorskip("formulas")
    _, task = run_ct()
    wb = load_workbook(task["output_path"])
    wb["한도 계산"]["B3"] = "일반기업"
    edited = tmp_path / "edited.xlsx"
    wb.save(edited)
    c = calc(edited, tmp_path)
    assert c("한도 계산", "B9") == 12_000_000 + 7_200_000 and c("한도 계산", "B11") == 45_500_000 - 19_200_000
    assert c("세무조정 검토표", "G4") == 26_300_000
