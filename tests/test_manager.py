"""매니저 테스트 — 신고기한 캘린더(공휴일 순연), 월마감 업무 배분(순서·연동·건너뜀), 진행 현황 매트릭스·엑셀·API."""
import shutil
import time
from datetime import date
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.core import config, deadlines
from app.main import app

D = date


# ---------------------------------------------------------------- 신고기한 캘린더·순연
def test_토요일_일요일은_다음_월요일로_순연():
    assert deadlines.shift_to_business_day(D(2026, 10, 25))[0] == D(2026, 10, 26)   # 일
    assert deadlines.shift_to_business_day(D(2026, 4, 25))[0] == D(2026, 4, 27)     # 토
    d, reason = deadlines.shift_to_business_day(D(2026, 11, 10))                    # 화 — 순연 없음
    assert d == D(2026, 11, 10) and reason == ""


def test_공휴일과_대체공휴일_연속_순연():
    """2026-10-03(토·개천절) → 10/4(일) → 10/5(월·대체공휴일) → 10/6(화)"""
    d, reason = deadlines.shift_to_business_day(D(2026, 10, 3))
    assert d == D(2026, 10, 6) and "개천절" in reason and "대체공휴일" in reason
    # 평일 공휴일: 2026-10-09 한글날(금) → 10/12(월)
    assert deadlines.shift_to_business_day(D(2026, 10, 9))[0] == D(2026, 10, 12)


def test_공휴일_파일이_없는_연도는_주말만_순연():
    assert not deadlines.holiday_known(2027) and deadlines.holiday_known(2026)
    assert deadlines.shift_to_business_day(D(2027, 1, 23))[0] == D(2027, 1, 25)     # 토 → 월


def test_이벤트에_원래_기한과_순연사유와_근거가_담긴다():
    evs = deadlines.events_between(D(2026, 10, 1), D(2026, 10, 31), D(2026, 11, 7))
    vat = next(e for e in evs if e["id"] == "vat_prelim")
    assert (vat["original_date"], vat["date"], vat["shifted"]) == ("2026-10-25", "2026-10-26", True)
    assert "일요일" in vat["shift_reason"] and vat["source"] and vat["verified"] is True and vat["verified_on"] == "2026-11-07" and vat["d_day"] == -12
    wh = next(e for e in evs if e["id"] == "withholding_monthly")                    # 10/10(토) → 10/12(월)
    assert (wh["original_date"], wh["date"]) == ("2026-10-10", "2026-10-12")
    assert [e["date"] for e in evs] == sorted(e["date"] for e in evs)


def test_캘린더에_원천세_부가세_법인세_연말정산_4대보험이_모두_있다():
    evs = deadlines.events_between(D(2026, 1, 1), D(2026, 12, 31), D(2026, 11, 7))
    assert {e["category"] for e in evs} == {"원천세", "부가세", "법인세", "연말정산·지급명세서", "4대보험"}
    assert sum(1 for e in evs if e["id"] == "withholding_monthly") == 12
    assert all(e["source"] for e in evs)                                             # 근거 표시
    assert all(e["verified"] for e in evs)                                           # 2차 검증으로 모든 기한이 확인됨


def test_월말_기한은_말일로_보정되고_주말이면_순연():
    evs = deadlines.events_between(D(2026, 2, 1), D(2026, 3, 5), D(2026, 2, 1))
    sb = next(e for e in evs if e["id"] == "simple_statement_business" and e["original_date"] == "2026-02-28")
    # 2/28(토) → 3/1(일·삼일절) → 3/2(월·대체공휴일) → 3/3(화)
    assert sb["date"] == "2026-03-03" and "삼일절" in sb["shift_reason"]


def test_홈_배지는_home_표시_항목만():
    items = deadlines.upcoming(D(2026, 11, 7), limit=3)
    assert [i["id"] for i in items] == ["withholding_monthly", "vat_final", "corp_tax_dec"]
    assert all("original_date" in i and "shifted" in i for i in items)
    all_items = deadlines.upcoming(D(2026, 11, 7), limit=50, home_only=False)
    assert len(all_items) > len(items) and any(i["category"] == "4대보험" for i in all_items)


def test_당일_순연_기한은_D0():
    """10/25(일) 규칙이 10/26 로 순연 — 오늘이 10/26 이면 D-0 (원래 날짜가 지났어도 순연일 기준)"""
    items = deadlines.upcoming(D(2026, 10, 26), limit=50, home_only=False)
    vat = next(i for i in items if i["id"] == "vat_prelim")
    assert vat["date"] == "2026-10-26" and vat["d_day"] == 0


# ---------------------------------------------------------------- 월마감 업무 배분 (API + 큐)
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    with TestClient(app) as c:
        yield c


def wait(client, agent_id, status, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if client.get(f"/api/agents/{agent_id}").json()["state"]["status"] == status:
            return
        time.sleep(0.03)
    raise AssertionError(f"{agent_id} → {status} 시간 초과")


def start(client, agent="manager", cid="ganasangsa", **options):
    r = client.post(f"/api/agents/{agent}/run", json={"client_id": cid, "tax_year": 2026, "options": options})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def tasks(client, agent):
    return client.get(f"/api/agents/{agent}/tasks").json()


def anomalies(client, tid):
    return client.get(f"/api/tasks/{tid}").json()["anomalies"]


def test_월마감_전체_흐름(client):
    tid = start(client)
    wait(client, "manager", "review")
    t = client.get(f"/api/tasks/{tid}").json()
    assert t["status"] == "review" and "💰검토필요 📒검토필요 🧾검토필요 🏛️검토필요" in t["result"]
    ids = {a: tasks(client, a)[0]["id"] for a in ("payroll", "bookkeeping", "vat", "corptax")}
    assert ids["payroll"] < ids["bookkeeping"] < ids["vat"] < ids["corptax"]            # 순서대로 만들어짐
    assert all(client.get(f"/api/agents/{a}").json()["state"]["status"] == "review" for a in ids)
    # 북키 → 코택 연동: 코택이 북키 작업의 시산표를 이어받았다
    msgs = " ".join(a["message"] for a in anomalies(client, ids["corptax"]))
    assert f"북키 작업 #{ids['bookkeeping']}" in msgs
    msgs = " ".join(a["message"] for a in anomalies(client, tid))
    assert "검토 대기" in msgs and "신고기한" in msgs
    names = load_workbook(BytesIO(client.get(f"/api/tasks/{tid}/download").content)).sheetnames
    assert names == ["월마감 배분 결과", "진행 현황", "신고기한 캘린더", "계산근거"]


def test_옵션이_코택까지_전달된다(client):
    """중소기업 아님·수입금액 24억 → 일반기업 기본한도 12,000,000 + 7,200,000 = 19,200,000"""
    start(client, is_sme=False, revenue=2_400_000_000)
    wait(client, "manager", "review")
    assert "기업업무추진비 한도 19,200,000원" in tasks(client, "corptax")[0]["result"]


def test_북키가_오류면_배트와_코택은_건너뛴다(client):
    folder = config.UPLOAD_DIR / "bookkeeping"
    folder.mkdir(parents=True)
    shutil.copy(config.SAMPLE_DIR / "bookkeeping" / "errors" / "전기말잔액_차대불일치.csv", folder / "전기말잔액_차대불일치.csv")
    tid = start(client)
    wait(client, "manager", "review")
    t = client.get(f"/api/tasks/{tid}").json()
    assert "💰검토필요 📒오류 🧾건너뜀 🏛️건너뜀" in t["result"]
    sev = {a["message"].split("]")[0].lstrip("["): a["severity"] for a in t["anomalies"]}
    assert sev["업무 오류"] == "error" and sev["업무 건너뜀"] == "warning"
    assert tasks(client, "vat") == [] and tasks(client, "corptax") == []               # 시작도 하지 않음


def test_다른_고객사_작업이_진행중이면_그_업무는_건너뛴다(client):
    start(client, "payroll", "daratech")
    wait(client, "payroll", "review")                                                     # 다라테크 급여가 검토 대기 중
    tid = start(client)
    wait(client, "manager", "review")
    t = client.get(f"/api/tasks/{tid}").json()
    assert t["result"].startswith("월마감 업무 배분: 💰건너뜀 📒검토필요")
    assert any("(주)다라테크" in a["message"] for a in t["anomalies"] if a["message"].startswith("[업무 건너뜀]"))


def test_같은_고객사_검토대기_작업은_이어받는다(client):
    bk = start(client, "bookkeeping")
    wait(client, "bookkeeping", "review")
    tid = start(client)
    wait(client, "manager", "review")
    assert len(tasks(client, "bookkeeping")) == 1                                         # 북키를 새로 만들지 않음
    msgs = " ".join(a["message"] for a in anomalies(client, tasks(client, "corptax")[0]["id"]))
    assert f"북키 작업 #{bk}" in msgs
    assert "📒검토필요" in client.get(f"/api/tasks/{tid}").json()["result"]


def test_매니저_자신이_작업중이면_중복_실행_거절(client):
    start(client)
    r = client.post("/api/agents/manager/run", json={"client_id": "daratech", "tax_year": 2026})
    assert r.status_code == 409


def test_고객사_오류와_연도_오류(client):
    assert client.post("/api/agents/manager/run", json={"client_id": "nobody", "tax_year": 2026}).status_code == 400
    assert client.post("/api/agents/manager/run", json={"client_id": "daratech", "tax_year": 1999}).status_code == 400


# ---------------------------------------------------------------- 진행 현황 매트릭스·엑셀·API
def test_진행_현황_매트릭스(client):
    m0 = client.get("/api/progress").json()
    assert [a["id"] for a in m0["agents"]] == ["payroll", "bookkeeping", "vat", "corptax"]
    assert {r["client_id"] for r in m0["rows"]} == {"ganasangsa", "daratech"}
    assert all(c["label"] == "작업 없음" and c["task_id"] is None for r in m0["rows"] for c in r["cells"].values())
    start(client)
    wait(client, "manager", "review")
    rows = {r["client_id"]: r for r in client.get("/api/progress?tax_year=2026").json()["rows"]}
    assert all(c["label"] == "검토필요" for c in rows["ganasangsa"]["cells"].values())
    assert all(c["label"] == "작업 없음" for c in rows["daratech"]["cells"].values())      # 다른 고객사는 그대로


def test_진행_현황_엑셀_API(client):
    start(client)
    wait(client, "manager", "review")
    res = client.get("/api/progress.xlsx?tax_year=2026")
    assert res.status_code == 200 and "spreadsheetml" in res.headers["content-type"]
    wb = load_workbook(BytesIO(res.content))
    assert wb.sheetnames == ["진행 현황", "신고기한 캘린더"]
    ws = wb["진행 현황"]
    assert ws["A4"].value == "(주)가나상사" and str(ws["B4"].value).startswith("검토필요 (#")
    assert ws["B8"].value.startswith("=COUNTIF(") and ws["D4"].value is not None          # 상태별 건수는 수식
    cal = wb["신고기한 캘린더"]
    assert cal["D5"].value.startswith("=A5-$B$2")


def test_매니저_결과_엑셀의_수식과_내용(client):
    tid = start(client)
    wait(client, "manager", "review")
    wb = load_workbook(BytesIO(client.get(f"/api/tasks/{tid}/download").content))
    dist = wb["월마감 배분 결과"]
    for r, icon in zip(range(4, 8), ("💰", "📒", "🧾", "🏛")):
        assert icon in dist.cell(row=r, column=2).value
    assert dist["C9"].value.startswith("=COUNTIF(")
    assert wb["진행 현황"]["B4"].value.startswith("검토필요")


def test_캘린더_API(client):
    r = client.get("/api/calendar?year=2026&month=10").json()
    ids = {e["id"] for e in r["events"]}
    assert {"withholding_monthly", "vat_prelim", "insurance_pay_monthly"} <= ids
    # 10/31(토) 말일 기한은 11/2(월)로 순연돼 11월 캘린더에 나온다 (원래 기한은 10/31 로 표시)
    nov = client.get("/api/calendar?year=2026&month=11").json()["events"]
    sb = next(e for e in nov if e["id"] == "simple_statement_business" and e["original_date"] == "2026-10-31")
    assert sb["date"] == "2026-11-02" and "simple_statement_business" not in ids
    assert [h["name"] for h in r["holidays"]] == ["개천절", "개천절 대체공휴일", "한글날"] and r["holiday_known"] is True
    assert "국세기본법" in r["shift_source"]
    assert client.get("/api/calendar?year=2027&month=1").json()["holiday_known"] is False
    assert client.get("/api/calendar?year=2026&month=13").status_code == 400


def test_홈_D_day_API는_매니저_캘린더_데이터(client):
    items = client.get("/api/deadlines?limit=3").json()
    assert len(items) == 3 and all("original_date" in i and "shifted" in i and i["verified"] is True for i in items)
    assert len(client.get("/api/deadlines?limit=30&all=true").json()) > 3
