"""배트 에이전트 통합 테스트 — 샘플·오류 데이터로 끝까지 실행하고 상태·이상항목·엑셀 수식을 확인한다."""
import asyncio
import csv
import shutil

import pytest
from openpyxl import load_workbook

from app.agents import REGISTRY
from app.core import config, db, state

SHEETS = ["대사결과", "불일치 상세", "불공제 후보", "신고서 요약", "계산근거"]
GANA = ("ganasangsa", "(주)가나상사")
DARA = ("daratech", "(주)다라테크")
SAMPLE = config.SAMPLE_DIR / "vat"


def run_vat(client=GANA, year=2026, **params):
    tid = db.create_task("vat", client[0], year, params, client_name=client[1], title="부가세 대사")["id"]
    asyncio.run(REGISTRY["vat"].run(tid))
    return tid, db.get_task(tid)


def anomalies(tid):
    return db.list_anomalies(tid)


def cats(tid):
    return [a["message"].split("]")[0].lstrip("[") for a in anomalies(tid)]


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def upload(name, rows=None, src=None):
    folder = config.UPLOAD_DIR / "vat"
    folder.mkdir(parents=True, exist_ok=True)
    if src:
        shutil.copy(src, folder / name)
    else:
        with open(folder / name, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(rows)


# ---------------------------------------------------------------- 정상 실행
def test_가나상사_검토필요와_엑셀_시트():
    tid, task = run_vat()
    assert task["status"] == "review" and state.get_state("vat").status == "review"
    assert load_workbook(task["output_path"]).sheetnames == SHEETS
    for text in ("일치 11", "금액차이 1", "장부누락 2", "세금계산서누락 3", "불공제 후보 5건"):
        assert text in task["result"]


def test_네가지_분류와_발행관리_이상항목():
    tid, _ = run_vat()
    c = cats(tid)
    assert c.count("금액차이") == 1 and c.count("장부누락") == 2
    assert "세금계산서누락(미수취)" in c
    assert c.count("미발행 의심") == 2 and c.count("지연발행 의심") == 1   # 오늘(2026-11-07) 기준 기한(10/10) 경과
    assert c.count("불공제 후보") == 5
    assert "카드매입 공제 불가" in c and "신고기한" in c and "법령 수치 검증 전" in c


def test_불공제_후보는_유형과_키워드를_보여주고_담당자가_판단():
    tid, task = run_vat()
    ws = load_workbook(task["output_path"])["불공제 후보"]
    rows = [[c.value for c in r] for r in ws.iter_rows(min_row=2, max_row=6)]
    types = {r[2]: r[8] for r in rows}
    assert types["한우마을"] == "기업업무추진비 관련" and "비영업용" in types["현대렌터카"] and "면세" in types["정다운"]
    assert "사업 무관" in types["늘봄마트"] and "비영업용" in types["한빛주유소"]
    assert {r[10] for r in rows} == {"확인필요"}  # AI 는 확정하지 않는다
    assert ws.data_validations.dataValidation


def test_다라테크는_전부_일치하고_카드_접대만_후보():
    tid, task = run_vat(DARA)
    assert task["status"] == "review"
    assert "일치 9" in task["result"] and "금액차이 0" in task["result"]
    c = cats(tid)
    assert c.count("불공제 후보") == 1 and "금액차이" not in c and "장부누락" not in c and "미발행 의심" not in c


def test_기한_전이면_미발행은_가산세_위험_없음_기한_D_day():
    """오늘을 2026-10-05 로: 미발행 기한(10/10) 전, 신고기한(10/25 일요일 → 10/26 순연) D-21 → 경고가 아니라 안내"""
    tid, task = run_vat(today="2026-10-05")
    dl = [a for a in anomalies(tid) if a["message"].startswith("[신고기한]")][0]
    assert dl["severity"] == "info" and "D-21" in dl["message"]
    unissued = [a["message"] for a in anomalies(tid) if a["message"].startswith("[미발행 의심]")]
    assert unissued and all("D-5" in m for m in unissued) and not any("경과" in m for m in unissued)
    ws = load_workbook(task["output_path"])["신고서 요약"]
    assert "D-21" in str(ws["C2"].value)


# ---------------------------------------------------------------- 오류 상태
def test_입력_오류_샘플은_오류_상태와_행번호():
    upload("매출세금계산서_오류시연.csv", src=SAMPLE / "errors" / "매출세금계산서_오류시연.csv")
    tid, task = run_vat()
    assert task["status"] == "error" and state.get_state("vat").status == "error"
    assert task["error_reason"].startswith("입력 오류") and not task["output_path"]
    got = {a["message"].split("]")[0].lstrip("[") for a in anomalies(tid)}
    assert {"중복 승인번호", "사업자번호", "합계 불일치", "형식 오류"} <= got
    refs = " ".join(a["row_ref"] for a in anomalies(tid))
    assert all(f"행 {n}" in refs for n in (3, 4, 5, 6))


def test_다른_고객사_이름이_든_업로드는_건너뛴다():
    upload("daratech_매출세금계산서.csv", [["작성일자"], ["깨진 데이터"]])
    _, task = run_vat(GANA)
    assert task["status"] == "review"


def test_필수_입력_파일이_없으면_안내_오류(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAMPLE_DIR", tmp_path)
    _, task = run_vat()
    assert task["status"] == "error" and "입력 파일이 없습니다" in task["error_reason"]


def test_적용연도와_작성일_연도가_다르면_오류():
    _, task = run_vat(year=2025)
    assert task["status"] == "error"


def test_카드_파일이_없어도_실행된다(monkeypatch, tmp_path):
    shutil.copytree(SAMPLE / "daratech", tmp_path / "vat" / "daratech")
    (tmp_path / "vat" / "daratech" / "card_purchases.csv").unlink()
    monkeypatch.setattr(config, "SAMPLE_DIR", tmp_path)
    tid, task = run_vat(DARA)
    assert task["status"] == "review" and "불공제 후보 0건" in task["result"]


# ---------------------------------------------------------------- 엑셀 수식
def test_엑셀_계산셀은_수식():
    _, task = run_vat()
    wb = load_workbook(task["output_path"])
    rec = wb["대사결과"]
    assert rec["B4"].value.startswith("=COUNTIFS(") and rec["F6"].value == "=SUM(F4:F5)"
    s = wb["신고서 요약"]
    assert s["B7"].value.startswith("=SUMIFS(") and s["B24"].value == "=B8-B16-B21" and s["B26"].value == "=B24-B15-B20"
    assert "SUMIFS" in s["B15"].value and "<>공제" in s["B15"].value
    diff = wb["불일치 상세"]
    formulas = [c.value for r in diff.iter_rows() for c in r if isinstance(c.value, str) and c.value.startswith("=IF(H")]
    assert formulas and "ROUND(E" in formulas[0]


def calc(path, tmp_path):
    formulas = pytest.importorskip("formulas")
    p = shutil.copy(path, tmp_path / "v.xlsx")
    sol = formulas.ExcelModel().loads(str(p)).finish().calculate()

    def cell(sheet, addr):
        v = sol[f"'[v.xlsx]{sheet.upper()}'!{addr}"].value[0, 0]
        return v if isinstance(v, str) else float(v)
    return cell


def test_수식_계산결과가_파이썬과_일치(tmp_path):
    pytest.importorskip("formulas")
    tid, task = run_vat()
    cell = calc(task["output_path"], tmp_path)
    # 대사결과 건수: 매출 6장+장부 2건 중 …  매출(일치4·금액차이1·장부누락1·세금계산서누락2) / 매입(일치6·장부누락1·세금계산서누락1)
    assert [cell("대사결과", f"{c}4") for c in "BCDEF"] == [4, 1, 1, 2, 8]
    assert [cell("대사결과", f"{c}5") for c in "BCDEF"] == [7, 0, 1, 1, 9]
    # 신고서 요약
    assert cell("신고서 요약", "B7") == 36_000_000 and cell("신고서 요약", "B8") == 3_600_000
    assert cell("신고서 요약", "B9") == 41_000_000 and cell("신고서 요약", "B10") == -5_000_000   # 장부 매출이 5백만원 더 큼(누락·차이 반영)
    assert cell("신고서 요약", "B14") == 1_988_000                                                 # 매입 세금계산서 세액 합계
    assert cell("신고서 요약", "B15") == 100_000 + 120_000 + 200_000       # 세금계산서 후보 3건
    assert cell("신고서 요약", "B20") == 7_727 + 9_091                     # 카드 후보 2건
    assert cell("신고서 요약", "B24") == 1_994_728 and cell("신고서 요약", "B25") == "납부세액"
    assert cell("신고서 요약", "B26") == 1_994_728 - 420_000 - 16_818
    # 가산세 위험액: 지연발행 2,000,000×1% + 미발행(7,000,000+1,500,000)×2% = 20,000 + 170,000
    wb = load_workbook(task["output_path"])
    diff = wb["불일치 상세"]
    total_row = next(r for r in range(1, diff.max_row + 1) if diff.cell(row=r, column=1).value == "가산세 위험액 합계")
    assert cell("불일치 상세", f"J{total_row}") == 190_000


def test_담당자가_판단을_공제로_바꾸면_납부세액이_바로_바뀐다(tmp_path):
    """접대비 후보(세액 100,000)를 '공제'로 바꾸고 다시 계산 → 납부세액 100,000 감소 (수식이 살아있다는 증거)"""
    pytest.importorskip("formulas")
    tid, task = run_vat()
    wb = load_workbook(task["output_path"])
    nd = wb["불공제 후보"]
    row = next(r for r in range(2, 8) if nd.cell(row=r, column=3).value == "한우마을")
    nd.cell(row=row, column=11, value="공제")
    edited = tmp_path / "edited.xlsx"
    wb.save(edited)
    cell = calc(edited, tmp_path)
    assert cell("신고서 요약", "B15") == 320_000
    assert cell("신고서 요약", "B24") == 1_994_728 - 100_000
