"""작업 배분: 에이전트에게 작업을 만들어 큐에 넣는다. API(작업 실행 버튼)와 매니저(월마감 업무 배분)가 같이 쓴다."""
from app.core import clients, config, db, state
from app.core import queue as task_queue


class DispatchError(Exception):
    """작업을 시작할 수 없음. status 는 HTTP 상태 코드로 그대로 쓴다."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def tax_years() -> list[int]:
    """rules/ 아래 숫자 폴더 = 선택 가능한 적용연도 (최신순)."""
    if not config.RULES_DIR.exists():
        return []
    return sorted((int(p.name) for p in config.RULES_DIR.iterdir() if p.is_dir() and p.name.isdigit()), reverse=True)


def start_task(agent_id: str, client_id: str, tax_year: int, params: dict | None = None) -> dict:
    """작업 생성 → 상태 '일하는중' → 큐 등록. 이미 일하는중/검토 대기면 409. 반드시 이벤트 루프 안에서 호출(async 함수에서)."""
    from app.agents import REGISTRY  # 순환 import 방지
    agent = REGISTRY.get(agent_id)
    if agent is None:
        raise DispatchError(404, "존재하지 않는 에이전트입니다")
    if tax_year not in tax_years():
        raise DispatchError(400, "선택할 수 없는 적용연도입니다")
    client = clients.get_client(client_id)
    if client is None:
        raise DispatchError(400, "존재하지 않는 고객사입니다")
    if state.get_state(agent_id).status in ("working", "review"):
        raise DispatchError(409, "이미 작업 중이거나 검토 승인 대기 중입니다")
    task = db.create_task(agent_id, client_id, tax_year, params or {}, client_name=client["name"], title=agent.task_name)
    state.set_status(agent_id, "working", progress=0, current_step="작업 대기 중",
                     task_id=task["id"], client_name=client["name"])
    task_queue.enqueue(agent_id, task["id"])
    return task
