"""북키 에이전트 통합 테스트 — 샘플 데이터·오류 데이터로 끝까지 실행하고 상태·이상항목·엑셀을 확인한다."""
import asyncio
import csv
import shutil
import time

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.agents import REGISTRY
from app.agents import bookkeeping_calc as bc
from app.core import config, db, state
from app.main import app

SHEETS = ["분개장", "시산표", "은행 Reconciliation", "BS 계정 검토", "결산 체크리스트", "PBC 리스트", "계산근거"]
GANA = ("ganasangsa", "(주)가나상사")
DARA = ("daratech", "(주)다라테크")
SAMPLE = config.SAMPLE_DIR / "bookkeeping"


def run_bk(client=GANA):
    tid = db.create_task("bookkeeping", client[0], 2026, {}, client_name=client[1], title="분개·시산표 작성")["id"]
    asyncio.run(REGISTRY["bookkeeping"].run(tid))
    return tid, db.get_task(tid)


def anomalies(tid):
    return db.list_anomalies(tid)


def cats(tid):
    return [a["message"].split("]")[0].lstrip("[") for a in anomalies(tid)]


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def upload(name, rows=None, src=None):
    folder = config.UPLOAD_DIR / "bookkeeping"
    folder.mkdir(parents=True, exist_ok=True)
    if src:
        shutil.copy(src, folder / name)
    else:
        with open(folder / name, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(rows)


# ---------------------------------------------------------------- 정상 실행 (검토필요)
def test_가나상사_끝까지_실행되면_검토필요_엑셀_시트():
    tid, task = run_bk()
    assert task["status"] == "review" and state.get_state("bookkeeping").status == "review"
    assert "시산표 차대 일치" in task["result"] and "-150,000" in task["result"]
    assert load_workbook(task["output_path"]).sheetnames == SHEETS


def test_원인불명_차이는_오류로_별도_표시되고_잔액불연속_행도_나온다():
    tid, _ = run_bk()
    diff = [a for a in anomalies(tid) if "[원인불명 차이]" in a["message"]]
    assert len(diff) == 1 and diff[0]["severity"] == "error" and "-150,000" in diff[0]["message"]
    assert "은행 잔액 불연속" in cats(tid)


def test_신뢰도_낮음은_확인필요이며_AI가_확정하지_않는다():
    tid, task = run_bk()
    assert cats(tid).count("분개 확인필요") == 8
    ws = load_workbook(task["output_path"])["분개장"]
    statuses = [ws.cell(row=r, column=11).value for r in range(2, ws.max_row + 1)]
    assert statuses.count("확인필요") == 16  # 확인필요 8건 × 전표 2줄
    assert statuses.count("추천") > 0


def test_BS_계정_플래그_장기미정리_마이너스():
    tid, _ = run_bk()
    c = cats(tid)
    assert "장기 미정리" in c and "전기 이월 미정리" in c and "마이너스 잔액" in c


def test_다라테크는_시차항목만으로_은행대사_일치():
    tid, task = run_bk(DARA)
    assert task["status"] == "review"
    assert "은행 대사 일치" in task["result"] and "원인불명 차이" not in cats(tid)
    assert "시차 항목" in cats(tid)


def test_체크리스트와_PBC_항목이_저장된다():
    tid, _ = run_bk()
    check, pbc = db.list_work_items(tid, "checklist"), db.list_work_items(tid, "pbc")
    assert {i["group"] for i in check} == {"월"} and all(i["status"] == "open" for i in check)  # 10월은 월 결산만
    bank_rec = next(i for i in check if i["key"] == "bank_rec")
    assert "-150,000" in bank_rec["hint"]
    st = {i["key"]: i["status"] for i in pbc}
    assert st["bank_stmt"] == "준비완료" and st["tb"] == "준비중" and st["contracts"] == "미준비"


def test_재실행하면_체크항목을_다시_만든다():
    tid, _ = run_bk()
    first = db.list_work_items(tid, "checklist")
    asyncio.run(REGISTRY["bookkeeping"].run(tid))
    assert len(db.list_work_items(tid, "checklist")) == len(first)


# ---------------------------------------------------------------- 오류 상태
def test_차대_불일치_전기말잔액은_오류_상태():
    upload("전기말잔액_차대불일치.csv", src=SAMPLE / "errors" / "전기말잔액_차대불일치.csv")
    tid, task = run_bk()
    assert task["status"] == "error" and state.get_state("bookkeeping").status == "error"
    assert task["error_reason"].startswith("입력 오류")
    bad = [a for a in anomalies(tid) if "[차대 불일치]" in a["message"]]
    assert bad and "500,000" in bad[0]["message"]
    assert not task["output_path"]  # 오류면 결과 파일 없음


def test_시산표가_차대_불일치면_오류로_멈춘다(monkeypatch):
    orig = bc.trial_balance

    def broken(led):
        tb = orig(led)
        tb["balanced"] = False
        return tb
    monkeypatch.setattr(bc, "trial_balance", broken)
    tid, task = run_bk()
    assert task["status"] == "error" and "시산표 차대 불일치" in task["error_reason"]


def test_은행잔액이_다른_업로드는_검토필요_상태_원인불명_차이():
    """다라테크 샘플 은행 내역의 중간 잔액을 틀어서 올린다 → 오류가 아니라 '검토필요'(+ 원인불명 차이 error 항목)."""
    rows = read_csv(SAMPLE / "daratech" / "bank.csv")
    for r in rows[6:]:
        r[5] = str(int(r[5]) - 70_000)  # 6번째 거래부터 잔액이 7만원 적게 찍힘
    upload("은행거래내역_잔액차이.csv", rows)
    tid, task = run_bk(DARA)
    assert task["status"] == "review"
    diff = [a for a in anomalies(tid) if "[원인불명 차이]" in a["message"]]
    assert diff and diff[0]["severity"] == "error" and "-70,000" in diff[0]["message"]
    assert "은행 대사 일치" not in task["result"]


def test_입력_형식_오류는_행번호와_함께_보고():
    rows = read_csv(SAMPLE / "ganasangsa" / "bank.csv")
    rows[2][0] = "10월3일"                    # 날짜 형식 오류 (행 3)
    rows[3][3], rows[3][4] = "1000", "500"    # 입금·출금 동시 (행 4)
    upload("은행거래내역_오류.csv", rows)
    tid, task = run_bk()
    assert task["status"] == "error"
    refs = " ".join(a["row_ref"] for a in anomalies(tid))
    assert "행 3" in refs and "행 4" in refs


def test_다른_고객사_이름이_든_업로드는_건너뛴다():
    upload("daratech_은행거래내역.csv", [["거래일자"], ["깨진 데이터"]])
    tid, task = run_bk(GANA)
    assert task["status"] == "review"


def test_필수_입력_파일이_없으면_안내_오류(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAMPLE_DIR", tmp_path)  # 샘플도 없는 상태
    tid, task = run_bk()
    assert task["status"] == "error" and "입력 파일이 없습니다" in task["error_reason"]


def test_적용연도와_거래_귀속월이_다르면_오류():
    tid = db.create_task("bookkeeping", "daratech", 2025, {}, client_name=DARA[1], title="x")["id"]
    asyncio.run(REGISTRY["bookkeeping"].run(tid))
    assert db.get_task(tid)["status"] == "error"


# ---------------------------------------------------------------- 엑셀 수식
def test_엑셀_계산셀은_수식():
    _, task = run_bk()
    wb = load_workbook(task["output_path"])
    j = wb["분개장"]
    last = j.max_row  # 합계 행
    assert j[f"E{last}"].value == f"=SUM(E2:E{last - 1})" and "차대" in j[f"G{last}"].value
    tb = wb["시산표"]
    assert tb["F2"].value.startswith("=SUMIF(") and tb["J2"].value == "=MAX(0,H2-I2)"
    assert wb["은행 Reconciliation"]["B7"].value == "=B5-B6"
    assert wb["PBC 리스트"].data_validations.dataValidation  # 준비상태 목록


def test_수식_계산결과가_파이썬과_일치(tmp_path):
    formulas = pytest.importorskip("formulas")
    tid, task = run_bk()
    path = shutil.copy(task["output_path"], tmp_path / "bk.xlsx")
    sol = formulas.ExcelModel().loads(str(path)).finish().calculate()

    def cell(sheet, addr):
        # formulas 라이브러리는 시트 이름의 영문자를 대문자로 바꿔 보관한다 (예: '은행 RECONCILIATION')
        v = sol[f"'[bk.xlsx]{sheet.upper()}'!{addr}"].value[0, 0]
        return v if isinstance(v, str) else float(v)

    wb = load_workbook(task["output_path"])
    tb = wb["시산표"]
    n = sum(1 for r in range(2, tb.max_row + 1) if str(tb[f"A{r}"].value or "").isdigit())
    total_row = n + 2
    assert cell("시산표", f"B{total_row + 1}") == "일치"
    assert cell("시산표", f"H{total_row}") == cell("시산표", f"I{total_row}")
    # 분개장 합계의 차변 = 대변
    jl = wb["분개장"].max_row
    assert cell("분개장", f"E{jl}") == cell("분개장", f"F{jl}") and cell("분개장", f"G{jl}") == "차대 일치"
    # 은행 Reconciliation: 파이썬 계산(원인불명 차이 -150,000)과 같다
    assert cell("은행 Reconciliation", "B7") == -150_000
    assert cell("은행 Reconciliation", "B8").startswith("차이 있음")
    assert cell("은행 Reconciliation", "B13") == cell("은행 Reconciliation", "B7")
    # 시산표의 보통예금 잔액 = 파이썬 원장 잔액
    rc = db.list_anomalies(tid)  # (이상항목에도 같은 숫자가 있어야 함)
    assert any("39,032,000" in a["message"] for a in rc)
    assert cell("은행 Reconciliation", "B6") == 39_182_000


# ---------------------------------------------------------------- API: 체크 항목 관리
def wait_status(client, status, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if client.get("/api/agents/bookkeeping").json()["state"]["status"] == status:
            return
        time.sleep(0.03)
    raise AssertionError(status)


def test_API_체크리스트_완료_PBC_상태_변경(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    with TestClient(app) as client:
        res = client.post("/api/agents/bookkeeping/run", json={"client_id": "ganasangsa", "tax_year": 2026})
        tid = res.json()["id"]
        wait_status(client, "review")
        items = client.get(f"/api/tasks/{tid}/items").json()
        assert len(items["checklist"]) == 9 and len(items["pbc"]) == 16

        ck = items["checklist"][0]
        r = client.put(f"/api/items/{ck['id']}", json={"status": "done", "updated_by": "김담당"})
        assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["updated_by"] == "김담당"
        assert client.put(f"/api/items/{ck['id']}", json={"status": "제출완료"}).status_code == 400  # 종류별 상태값만 허용

        pb = items["pbc"][-1]
        r = client.put(f"/api/items/{pb['id']}", json={"status": "제출완료", "note": "감사인 전달"})
        assert r.json()["status"] == "제출완료" and r.json()["note"] == "감사인 전달"
        assert client.put(f"/api/items/{pb['id']}", json={"status": "done"}).status_code == 400
        assert client.put("/api/items/99999", json={"status": "done"}).status_code == 404

        again = client.get(f"/api/tasks/{tid}/items").json()
        assert next(i for i in again["checklist"] if i["id"] == ck["id"])["status"] == "done"
        assert client.get("/api/tasks/99999/items").status_code == 404
