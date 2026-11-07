"""검토 승인/반려 (Human-in-the-loop). 이력은 DB 에 담당자·시각·코멘트와 함께 남긴다."""
import asyncio
from datetime import datetime

from app.core import activity, config, db, state
from app.core import queue as task_queue

_background: set[asyncio.Task] = set()


class ReviewError(ValueError):
    """검토 요청이 올바르지 않을 때(화면에 그대로 보여줄 한국어 메시지)."""


async def _revert_to_idle(agent_id: str, task_id: int) -> None:
    await asyncio.sleep(config.DONE_HOLD_SECONDS)
    st = state.get_state(agent_id)
    if st.status == "done" and st.task_id == task_id:  # 그 사이 새 작업이 시작됐다면 건드리지 않음
        state.set_status(agent_id, "idle")


def pending_review_task(agent_id: str) -> dict | None:
    return db.latest_task_with_status(agent_id, "review")


def submit_review(task_id: int, reviewer_name: str, decision: str, comment: str = "",
                  reason: str = "", on_reject: str = "rework") -> dict:
    """decision: approve / reject. 반려 시 reason 필수, on_reject: rework(재작업) / error(오류 처리)."""
    reviewer_name = (reviewer_name or "").strip()
    reason = (reason or "").strip()
    if not reviewer_name:
        raise ReviewError("담당자 이름을 입력해 주세요")
    if decision not in ("approve", "reject"):
        raise ReviewError("승인 또는 반려를 선택해 주세요")
    if decision == "reject" and not reason:
        raise ReviewError("반려 사유를 입력해 주세요")
    if on_reject not in ("rework", "error"):
        raise ReviewError("반려 후 처리 방식이 올바르지 않습니다")

    task = db.get_task(task_id)
    if task is None:
        raise ReviewError("작업을 찾을 수 없습니다")
    if task["status"] != "review":
        raise ReviewError("검토 대기 중인 작업이 아닙니다")

    agent_id, client = task["agent"], task["client_name"]
    review = db.add_review(task_id, agent_id, reviewer_name, decision, comment.strip(), reason)

    if decision == "approve":
        result = f"승인 완료 ({reviewer_name})"
        db.update_task(task_id, status="done", finished_at=datetime.now(), current_step="승인 완료", result=result)
        state.set_status(agent_id, "done", current_step=f"{reviewer_name} 승인 완료",
                         task_id=task_id, client_name=client)
        activity.log_activity(agent_id, f"승인 완료 — {reviewer_name}", task=task, kind="done")
        t = asyncio.get_running_loop().create_task(_revert_to_idle(agent_id, task_id))
        _background.add(t)
        t.add_done_callback(_background.discard)
    elif on_reject == "rework":
        db.update_task(task_id, status="queued", progress=0, current_step="반려로 재작업 대기",
                       result=f"반려({reviewer_name}) → 재작업")
        state.set_status(agent_id, "working", progress=0, current_step="반려로 재작업 대기 중",
                         task_id=task_id, client_name=client)
        activity.log_activity(agent_id, f"반려 → 재작업 — {reviewer_name}: {reason}", task=task, kind="reject")
        task_queue.enqueue(agent_id, task_id)
    else:
        msg = f"반려: {reason}"
        db.update_task(task_id, status="error", error_reason=msg, result=f"반려({reviewer_name})",
                       finished_at=datetime.now())
        state.set_status(agent_id, "error", error_reason=msg, current_step="검토 반려",
                         task_id=task_id, client_name=client)
        activity.log_activity(agent_id, f"반려 — {reviewer_name}: {reason}", task=task, kind="reject")
    return review
