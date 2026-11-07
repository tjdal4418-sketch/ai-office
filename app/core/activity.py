"""활동 로그: DB 저장 + SSE 푸시 + 에이전트의 마지막 메시지 갱신."""
from app.core import db, events, state


def log_activity(agent_id: str, message: str, *, task: dict | None = None, kind: str = "info") -> dict:
    """task 가 있으면 '(주)고객사 작업명' 맥락이 메시지 앞에 붙는다."""
    if task:
        prefix = " ".join(p for p in (task.get("client_name"), task.get("title")) if p)
        message = f"{prefix} {message}".strip()
    row = db.add_activity(agent_id, message, task_id=task["id"] if task else None, kind=kind)
    state.touch_message(agent_id, message)
    events.publish("activity", row)
    return row
