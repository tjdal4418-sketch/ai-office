"""통합 테스트 — (주)가나상사·(주)다라테크 샘플로 월마감 전체 실행.

매니저 '월마감 시작' → 페이 → 북키 → 배트 → 코택 순서 배분 → 모두 검토필요 → 담당자 승인 → 완료 → 대기중.
각 에이전트의 상태 전환 이력(오피스 뷰 캐릭터가 따라가는 상태 흐름), 결과 엑셀, 연동(북키 시산표 → 코택)을 함께 확인한다.
"""
import time
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.core import config, db
from app.main import app

WORKERS = ["payroll", "bookkeeping", "vat", "corptax"]
ALL = ["manager", *WORKERS]
SHEETS = {
    "payroll": ["급여명세", "4대보험", "원천세 집계", "이상항목", "계산근거"],
    "bookkeeping": ["분개장", "시산표", "은행 Reconciliation", "BS 계정 검토", "결산 체크리스트", "PBC 리스트", "계산근거"],
    "vat": ["대사결과", "불일치 상세", "불공제 후보", "신고서 요약", "계산근거"],
    "corptax": ["세무조정 검토표", "한도 계산", "요청자료 목록", "시산표(입력)", "계산근거"],
    "manager": ["월마감 배분 결과", "진행 현황", "신고기한 캘린더", "계산근거"],
}
# 클라이언트별로 기대하는 결과 문구 (샘플 데이터의 의도된 값)
EXPECT = {
    "ganasangsa": {"payroll": "16명", "bookkeeping": "원인불명 차이 -150,000원", "vat": "금액차이 1"},
    "daratech": {"payroll": "8명", "bookkeeping": "은행 대사 일치", "vat": "일치 9"},
}
NAMES = {"ganasangsa": "(주)가나상사", "daratech": "(주)다라테크"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{(tmp_path / 'it.db').as_posix()}")
    with TestClient(app) as c:
        yield c


def wait(client, agent, status, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        if client.get(f"/api/agents/{agent}").json()["state"]["status"] == status:
            return
        time.sleep(0.03)
    raise AssertionError(f"{agent} → {status} 시간 초과 (현재 {client.get(f'/api/agents/{agent}').json()['state']['status']})")


def transitions(agent: str, since_id: int = 0) -> list[str]:
    """상태 이력을 시간순으로, 연속 중복을 뺀 상태 이름 목록으로."""
    rows = [r for r in reversed(db.list_status_log(agent, limit=500)) if r["id"] > since_id]
    out: list[str] = []
    for r in rows:
        if not out or out[-1] != r["status"]:
            out.append(r["status"])
    return out


def last_log_id() -> int:
    rows = db.list_status_log(limit=1)
    return rows[0]["id"] if rows else 0


def month_close(client, cid: str):
    """한 고객사 월마감을 끝까지 실행하고 승인까지 마친다. 반환: {agent: task}"""
    mark = last_log_id()
    r = client.post("/api/agents/manager/run", json={"client_id": cid, "tax_year": 2026})
    assert r.status_code == 200, r.text
    manager_task = r.json()["id"]
    wait(client, "manager", "review")

    # 1) 전원 검토필요 + 이 고객사의 작업이 하나씩 생김
    tasks = {a: next(t for t in client.get(f"/api/agents/{a}/tasks").json() if t["client_id"] == cid) for a in WORKERS}
    tasks["manager"] = client.get(f"/api/tasks/{manager_task}").json()
    for a in ALL:
        assert client.get(f"/api/agents/{a}").json()["state"]["status"] == "review", a
        assert tasks[a]["status"] == "review" and tasks[a]["client_name"] == NAMES[cid]
    ids = [tasks[a]["id"] for a in WORKERS]
    assert ids == sorted(ids)  # 페이 → 북키 → 배트 → 코택 순서로 배분

    # 2) 결과 문구·엑셀 시트·다운로드
    for a, text in EXPECT[cid].items():
        assert text in tasks[a]["result"], f"{cid} {a}: {tasks[a]['result']}"
    for a in ALL:
        res = client.get(f"/api/tasks/{tasks[a]['id']}/download")
        assert res.status_code == 200 and "spreadsheetml" in res.headers["content-type"], a
        assert load_workbook(BytesIO(res.content)).sheetnames == SHEETS[a], a
    # 3) 연동: 코택은 같은 고객사 북키 작업의 시산표를 이어받음
    msgs = " ".join(x["message"] for x in client.get(f"/api/tasks/{tasks['corptax']['id']}").json()["anomalies"])
    assert f"북키 작업 #{tasks['bookkeeping']['id']}" in msgs
    # 4) 이상항목은 '검토필요'로 올라온 것일 뿐 오류가 아니다
    assert all(client.get(f"/api/tasks/{tasks[a]['id']}").json()["error_reason"] is None for a in ALL)

    # 5) 담당자 승인 → 완료 → 대기중 (캐릭터가 복합기를 거쳐 라운지로 돌아가는 구간)
    for a in ALL:
        res = client.post(f"/api/agents/{a}/approve", json={"reviewer_name": "통합테스트", "comment": f"{cid} 월마감 확인"})
        assert res.status_code == 200, (a, res.text)
    for a in ALL:
        wait(client, a, "idle")
    for a in ALL:  # 오피스 뷰 캐릭터가 따라가는 상태 전환 순서
        assert transitions(a, mark) == ["working", "review", "done", "idle"], (cid, a, transitions(a, mark))
    return tasks


@pytest.mark.parametrize("cid", ["ganasangsa", "daratech"])
def test_월마감_전체_실행(client, cid):
    tasks = month_close(client, cid)
    # 승인 후 작업 상태는 '완료(done)'로 매트릭스에 반영
    row = next(r for r in client.get("/api/progress?tax_year=2026").json()["rows"] if r["client_id"] == cid)
    assert {a: row["cells"][a]["label"] for a in WORKERS} == {a: "완료" for a in WORKERS}
    assert all(tasks[a]["output_path"] for a in ALL)


def test_두_고객사_연달아_월마감(client):
    """가나상사를 끝낸 뒤 바로 다라테크 — 에이전트 상태가 깨끗이 초기화돼 다음 고객사로 이어진다."""
    first = month_close(client, "ganasangsa")
    second = month_close(client, "daratech")
    assert min(second[a]["id"] for a in ALL) > max(first[a]["id"] for a in ALL)
    m = client.get("/api/progress?tax_year=2026").json()
    for row in m["rows"]:
        assert {row["cells"][a]["label"] for a in WORKERS} == {"완료"}, row["client_name"]
    # 활동 로그에 두 고객사의 월마감이 모두 남는다
    msgs = " ".join(x["message"] for x in client.get("/api/activity?limit=200").json())
    assert "(주)가나상사" in msgs and "(주)다라테크" in msgs
    # 요약: 오류 0건, 검토 대기 0건
    s = client.get("/api/summary").json()
    assert s["errors"] == 0 and s["review_waiting"] == 0
