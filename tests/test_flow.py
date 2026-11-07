"""BaseAgent 작업 흐름(검증→계산→리포트→파일→검토필요)과 검토 승인/반려."""
import asyncio

import pytest
from openpyxl import load_workbook

from app.agents import REGISTRY
from app.agents.base import BaseAgent
from app.core import db, review, state
from app.core import queue as task_queue


def make_task(agent="payroll", **params):
    return db.create_task(agent, "ganasangsa", 2026, params, client_name="(주)가나상사", title="급여 계산")["id"]


def run(coro):
    return asyncio.run(coro)


class DemoAgent(BaseAgent):
    """모든 실제 에이전트가 단계 메서드를 override 하므로, BaseAgent 의 기본(데모) 흐름은 이 스텁으로 확인한다."""
    agent_id = "demo"
    name, icon, role, summary = "데모", "🧪", "Demo", "BaseAgent 기본 흐름 확인용"
    demo_steps = ["불러오는 중", "계산 중"]


def test_run_reaches_review_with_formula_excel():
    state.register("demo")
    tid = make_task("demo")
    run(DemoAgent().run(tid))
    task = db.get_task(tid)
    assert task["status"] == "review"
    assert state.get_state("demo").status == "review"
    wb = load_workbook(task["output_path"])  # data_only=False → 수식이 그대로 남아 있어야 함
    assert wb["결과"]["B5"].value == "=SUM(B2:B4)"
    assert any("데모" in str(c.value) for row in wb["계산근거"].iter_rows() for c in row)


def test_validation_error_sets_error_state():
    tid = make_task(simulate_error=True)
    run(REGISTRY["payroll"].run(tid))
    assert db.get_task(tid)["status"] == "error"
    st = state.get_state("payroll")
    assert (st.status, st.error_reason) == ("error", "주민번호 누락 2건")
    assert len(db.list_anomalies(tid)) == 2


def test_approve_records_history_and_returns_to_idle():
    async def scenario():
        tid = make_task()
        await REGISTRY["payroll"].run(tid)
        review.submit_review(tid, "홍길동", "approve", comment="이상 없음")
        assert state.get_state("payroll").status == "done"
        await asyncio.sleep(0.2)  # DONE_HOLD_SECONDS(0.05) 경과
        return tid

    tid = run(scenario())
    assert state.get_state("payroll").status == "idle"
    r = db.list_reviews(agent="payroll")[0]
    assert (r["reviewer_name"], r["decision"], r["comment"]) == ("홍길동", "approve", "이상 없음")
    assert db.get_task(tid)["status"] == "done"


def test_reject_requires_reason_and_name():
    tid = make_task()
    run(REGISTRY["payroll"].run(tid))
    with pytest.raises(review.ReviewError):
        review.submit_review(tid, "홍길동", "reject")
    with pytest.raises(review.ReviewError):
        review.submit_review(tid, "  ", "approve")
    assert db.get_task(tid)["status"] == "review"  # 실패한 검토는 상태를 바꾸지 않음


def test_reject_to_error():
    async def scenario():
        tid = make_task()
        await REGISTRY["payroll"].run(tid)
        review.submit_review(tid, "홍길동", "reject", reason="공제 누락", on_reject="error")
        return tid

    tid = run(scenario())
    st = state.get_state("payroll")
    assert (st.status, st.error_reason) == ("error", "반려: 공제 누락")
    assert db.list_reviews(task_id=tid)[0]["reason"] == "공제 누락"


def test_reject_rework_runs_again_then_back_to_review():
    async def scenario():
        await task_queue.start(REGISTRY)
        try:
            tid = make_task()
            await REGISTRY["payroll"].run(tid)
            review.submit_review(tid, "홍길동", "reject", reason="재계산 필요", on_reject="rework")
            assert state.get_state("payroll").status == "working"
            for _ in range(100):  # 워커가 재작업을 끝낼 때까지 대기
                if db.get_task(tid)["status"] == "review":
                    break
                await asyncio.sleep(0.02)
            return tid
        finally:
            await task_queue.stop()

    tid = run(scenario())
    assert db.get_task(tid)["status"] == "review"
    assert state.get_state("payroll").status == "review"


def test_cannot_review_task_not_in_review():
    tid = make_task()
    with pytest.raises(review.ReviewError):
        review.submit_review(tid, "홍길동", "approve")


def test_recover_interrupted_marks_error():
    tid = make_task()
    db.update_task(tid, status="working")
    assert db.recover_interrupted() == 1
    assert db.get_task(tid)["status"] == "error"
