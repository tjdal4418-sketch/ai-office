"""AI 오피스 진입점. 실행: uvicorn app.main:app --reload"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agents import REGISTRY
from app.core import config, db, events, state
from app.core import queue as task_queue
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.recover_interrupted()  # 재시작 전에 돌던 작업은 오류로 정리
    events.bind_loop()
    state.reset()
    for agent_id in REGISTRY:
        state.register(agent_id)
        pending = db.latest_task_with_status(agent_id, "review")
        if pending:  # 검토 승인 대기 중이던 건은 화면에 그대로 복원
            state.set_status(agent_id, "review", current_step="검토 승인 대기 중", task_id=pending["id"],
                             client_name=pending["client_name"], log=False)
    await task_queue.start(REGISTRY)
    yield
    await task_queue.stop()


app = FastAPI(title="AI 오피스", lifespan=lifespan)
app.include_router(router)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def home():
    """기본 홈 = 픽셀아트 오피스 뷰 (좁은 화면에서는 페이지가 스스로 /list 로 이동)."""
    return FileResponse(config.STATIC_DIR / "office.html")


@app.get("/list", include_in_schema=False)
def home_list():
    """기존 카드형 목록 뷰."""
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/agent", include_in_schema=False)
def agent_page():
    return FileResponse(config.STATIC_DIR / "agent.html")
