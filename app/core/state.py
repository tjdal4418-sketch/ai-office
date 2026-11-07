"""에이전트 상태 관리. 상태 변경은 반드시 set_status() 로만 한다(→ SSE·DB 자동 반영)."""
from dataclasses import asdict, dataclass
from datetime import datetime

from app.core import db, events

STATUSES = ("idle", "working", "review", "done", "error")

STATUS_LABELS = {
    "idle": "대기중",
    "working": "일하는중",
    "review": "검토필요",
    "done": "완료",
    "error": "오류",
}


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class AgentState:
    agent_id: str
    status: str = "idle"
    progress: int = 0
    current_step: str = ""
    client_name: str = ""
    error_reason: str | None = None
    task_id: int | None = None
    last_message: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["label"] = STATUS_LABELS[self.status]
        return d


_states: dict[str, AgentState] = {}


def register(agent_id: str) -> None:
    _states[agent_id] = AgentState(agent_id=agent_id, updated_at=_stamp())


def reset() -> None:
    _states.clear()


def get_state(agent_id: str) -> AgentState:
    return _states[agent_id]


def set_status(
    agent_id: str,
    status: str,
    *,
    progress: int | None = None,
    current_step: str = "",
    error_reason: str | None = None,
    task_id: int | None = None,
    client_name: str | None = None,
    log: bool = True,
) -> AgentState:
    """에이전트 상태를 바꾸는 유일한 통로. 상태가 바뀐 순간은 DB 이력에 남기고 SSE 로 알린다."""
    if status not in STATUSES:
        raise ValueError(f"알 수 없는 상태값: {status}")
    st = _states[agent_id]
    changed = st.status != status

    st.status = status
    if progress is not None:
        st.progress = max(0, min(100, int(progress)))
    elif status == "idle":
        st.progress = 0
    elif status in ("review", "done"):
        st.progress = 100
    st.current_step = current_step
    st.error_reason = error_reason if status == "error" else None
    if task_id is not None:
        st.task_id = task_id
    elif status == "idle":
        st.task_id = None
    if client_name is not None:
        st.client_name = client_name
    elif status == "idle":
        st.client_name = ""
    st.updated_at = _stamp()

    if log and changed:
        db.add_status_log(agent_id, status, current_step, st.task_id)
    events.publish("status", st.to_dict())
    return st


def touch_message(agent_id: str, message: str) -> None:
    """마지막 활동 메시지만 갱신한다(상태값은 바꾸지 않음)."""
    st = _states.get(agent_id)
    if st is None:
        return
    st.last_message = message
    st.updated_at = _stamp()
    events.publish("status", st.to_dict())
