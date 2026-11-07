"""API 통합 테스트: 실행 → 검토필요 → 승인 → 대기중 복귀, 활동 로그·요약·업로드."""
import time

from fastapi.testclient import TestClient

from app.core import config
from app.main import app


def wait_status(client, agent_id, status, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"/api/agents/{agent_id}").json()["state"]
        if st["status"] == status:
            return
        time.sleep(0.02)
    raise AssertionError(f"{agent_id} 가 {status} 상태가 되지 않음")


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    return TestClient(app)


def test_run_review_approve_then_idle(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        meta = client.get("/api/meta").json()
        assert 2026 in meta["tax_years"] and len(meta["clients"]) == 2

        body = {"client_id": "ganasangsa", "tax_year": 2026}
        res = client.post("/api/agents/payroll/run", json=body)
        assert res.status_code == 200
        task_id = res.json()["id"]
        assert client.post("/api/agents/payroll/run", json=body).status_code == 409  # 작업 중 중복 실행 방지

        wait_status(client, "payroll", "review")
        detail = client.get("/api/agents/payroll").json()
        assert detail["pending_review_task_id"] == task_id
        assert detail["state"]["client_name"] == "(주)가나상사"
        assert client.post(f"/api/tasks/{task_id}/review",
                           json={"reviewer_name": "김회계", "decision": "reject"}).status_code == 400

        ok = client.post("/api/agents/payroll/approve", json={"reviewer_name": "김회계", "comment": "OK"})
        assert ok.status_code == 200
        wait_status(client, "payroll", "done")
        wait_status(client, "payroll", "idle")  # 승인 후 자동 복귀
        assert client.post("/api/agents/payroll/approve", json={"reviewer_name": "김회계"}).status_code == 409

        assert client.get(f"/api/tasks/{task_id}/download").status_code == 200
        assert client.get("/api/agents/payroll/reviews").json()[0]["reviewer_name"] == "김회계"

        # 활동 로그: 시작 → 검토 대기 → 승인 순서, 메시지에 고객사·작업명 포함
        feed = client.get("/api/activity").json()
        messages = [r["message"] for r in reversed(feed)]
        assert messages[0] == "(주)가나상사 급여 계산 시작"
        assert any("검토 승인 대기" in m for m in messages)
        assert "승인 완료" in messages[-1] and "김회계" in messages[-1]

        s = client.get("/api/summary").json()
        assert (s["processed"], s["review_waiting"], s["errors"]) == (1, 0, 0)
        task = client.get("/api/agents/payroll/tasks").json()[0]
        assert task["started_at"] and task["finished_at"] and task["result"].startswith("승인 완료")


def test_error_counted_in_summary(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        client.post("/api/agents/vat/run", json={"client_id": "daratech", "tax_year": 2026, "simulate_error": True})
        wait_status(client, "vat", "error")
        assert client.get("/api/summary").json()["errors"] == 1
        assert client.get("/api/agents/vat").json()["state"]["error_reason"] == "주민번호 누락 2건"


def test_invalid_inputs_rejected(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        assert client.post("/api/agents/payroll/run", json={"client_id": "ganasangsa", "tax_year": 1999}).status_code == 400
        assert client.post("/api/agents/payroll/run", json={"client_id": "nope", "tax_year": 2026}).status_code == 400
        assert client.post("/api/agents/ghost/run", json={"client_id": "ganasangsa", "tax_year": 2026}).status_code == 404
        assert client.get("/api/agents/ghost").status_code == 404


def test_deadlines_endpoint(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        items = client.get("/api/deadlines").json()
        assert len(items) == 3 and all("d_day" in i for i in items)


def test_upload_validates_and_lists(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        ok = client.post("/api/agents/payroll/upload", files={"file": ("급여대장.csv", b"name,pay\nA,1\n")})
        assert ok.status_code == 200 and ok.json()["name"] == "급여대장.csv"
        assert client.post("/api/agents/payroll/upload", files={"file": ("x.exe", b"MZ")}).status_code == 400
        assert client.post("/api/agents/payroll/upload", files={"file": ("e.csv", b"")}).status_code == 400
        # 경로 조작 파일명은 파일명만 남기고 uploads 폴더 밖으로 나가지 못한다
        evil = client.post("/api/agents/payroll/upload", files={"file": ("../../evil.csv", b"a,b\n")})
        assert evil.json()["name"] == "evil.csv"
        assert not (tmp_path / "evil.csv").exists()
        names = [f["name"] for f in client.get("/api/agents/payroll/uploads").json()]
        assert set(names) == {"급여대장.csv", "evil.csv"}
        assert any("파일 업로드" in r["message"] for r in client.get("/api/activity").json())


def test_기본_홈은_오피스_뷰이고_목록_뷰는_list(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        home = client.get("/")
        assert home.status_code == 200 and 'id="office"' in home.text          # 픽셀아트 오피스 뷰
        lst = client.get("/list")
        assert lst.status_code == 200 and 'id="floor"' in lst.text             # 기존 카드형 목록 뷰
        assert 'href="/list"' in home.text and 'href="/"' in lst.text          # 서로 오가는 링크
        assert client.get("/static/office.html").status_code == 200            # 예전 주소도 계속 동작
