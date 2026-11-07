"""SSE 브로드캐스터. 상태가 바뀌면 접속 중인 모든 브라우저로 밀어준다."""
import asyncio
import json
from typing import AsyncIterator, Callable

_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop() -> None:
    """서버 시작 시 호출 — 다른 스레드에서 publish 해도 안전하게 전달하기 위함."""
    global _loop
    _loop = asyncio.get_running_loop()


def format_sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _deliver(message: str) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(message)
        except asyncio.QueueFull:
            pass  # 느린 클라이언트는 건너뜀(재접속 시 스냅샷으로 복구)


def publish(event: str, data) -> None:
    if not _subscribers:
        return
    message = format_sse(event, data)
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if _loop is None or running is _loop:
        _deliver(message)
    else:
        _loop.call_soon_threadsafe(_deliver, message)


async def stream(snapshot_fn: Callable[[], list]) -> AsyncIterator[str]:
    """접속 직후 전체 스냅샷을 보내고, 이후 변경분을 흘려보낸다."""
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.add(q)
    try:
        yield format_sse("snapshot", snapshot_fn())
        while True:
            try:
                yield await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        _subscribers.discard(q)
