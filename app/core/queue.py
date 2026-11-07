"""작업 큐. 에이전트마다 큐·워커 1개 — 같은 에이전트는 한 번에 한 작업, 에이전트끼리는 동시 진행."""
import asyncio
import logging

logger = logging.getLogger("ai_office.queue")

_queues: dict[str, asyncio.Queue] = {}
_workers: list[asyncio.Task] = []


async def start(registry: dict) -> None:
    for agent_id, agent in registry.items():
        _queues[agent_id] = asyncio.Queue()
        _workers.append(asyncio.create_task(_worker(agent_id, agent)))


async def stop() -> None:
    for w in _workers:
        w.cancel()
    await asyncio.gather(*_workers, return_exceptions=True)
    _workers.clear()
    _queues.clear()


def enqueue(agent_id: str, task_id: int) -> None:
    if agent_id not in _queues:
        raise RuntimeError("작업 큐가 시작되지 않았습니다")
    _queues[agent_id].put_nowait(task_id)


async def _worker(agent_id: str, agent) -> None:
    q = _queues[agent_id]
    while True:
        task_id = await q.get()
        try:
            await agent.run(task_id)
        except Exception:  # run() 안에서 대부분 처리하지만, 워커는 절대 죽지 않게 한다
            logger.exception("작업 처리 중 예기치 못한 오류 (agent=%s, task=%s)", agent_id, task_id)
        finally:
            q.task_done()
