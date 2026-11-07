"""API 라우터."""
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from app.agents import REGISTRY
from app.core import activity, clients, config, db, deadlines, dispatch, events, progress, review, rules_catalog, state

router = APIRouter(prefix="/api")


def _agent_payload(agent) -> dict:
    return {**agent.meta(), "state": state.get_state(agent.agent_id).to_dict()}


def _agents_payload() -> list[dict]:
    return [_agent_payload(a) for a in REGISTRY.values()]


def _tax_years() -> list[int]:
    return dispatch.tax_years()


def _agent_or_404(agent_id: str):
    if agent_id not in REGISTRY:
        raise HTTPException(404, "존재하지 않는 에이전트입니다")
    return REGISTRY[agent_id]


@router.get("/meta")
def meta():
    return {"tax_years": _tax_years(), "clients": clients.load_clients()}


@router.get("/agents")
def agents():
    return _agents_payload()


@router.get("/agents/{agent_id}")
def agent_detail(agent_id: str):
    agent = _agent_or_404(agent_id)
    pending = review.pending_review_task(agent_id)
    return {**_agent_payload(agent), "pending_review_task_id": pending["id"] if pending else None}


@router.get("/events")
async def sse():
    return StreamingResponse(
        events.stream(_agents_payload),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/activity")
def activity_feed(limit: int = 50, agent: str | None = None):
    return db.list_activity(limit=max(1, min(limit, 200)), agent=agent)


@router.get("/summary")
def summary():
    return db.summary(date.today())


@router.get("/deadlines")
def upcoming_deadlines(limit: int = 3, all: bool = False):
    """홈 D-day 배지(기본: home 표시 항목만) — 데이터는 매니저의 신고기한 캘린더(rules/deadlines.yaml, 순연 반영)."""
    return deadlines.upcoming(limit=max(1, min(limit, 30)), home_only=not all)


@router.get("/calendar")
def calendar_month(year: int | None = None, month: int | None = None):
    """매니저의 신고기한 캘린더: 해당 월의 신고기한(순연 반영)과 공휴일."""
    today = date.today()
    y, m = year or today.year, month or today.month
    if not 1 <= m <= 12 or not 2000 <= y <= 2100:
        raise HTTPException(400, "연·월이 올바르지 않습니다")
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    hol = [{"date": d.isoformat(), "name": n} for d, n in sorted(deadlines.load_holidays(y).items()) if start <= d <= end]
    return {"year": y, "month": m, "today": today.isoformat(), "events": deadlines.events_between(start, end, today),
            "holidays": hol, "holiday_known": deadlines.holiday_known(y), "shift_source": deadlines.shift_source(today)}


@router.get("/rules")
def rules(year: int | None = None, needs_check_only: bool = False):
    """rules 폴더의 모든 수치: 항목명(name·label)·값·근거 법령(source)·적용연도(year)·확인일(verified_on)·확인필요 여부(needs_check).
    summary.needs_check 가 '확인필요' 개수. 읽기 전용(법령 자료실·docs/rules_check.md 용)."""
    try:
        return rules_catalog.catalog(year, needs_check_only)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/progress")
def progress_matrix(tax_year: int | None = None):
    """고객사별 업무 상태 매트릭스 (매니저 전체 진행 현황)."""
    return progress.matrix(tax_year)


@router.get("/progress.xlsx")
def progress_excel(tax_year: int | None = None):
    from io import BytesIO

    from app.agents.manager import build_progress_workbook
    buf = BytesIO()
    build_progress_workbook(tax_year).save(buf)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=progress_report.xlsx"})


class RunRequest(BaseModel):
    client_id: str
    tax_year: int
    simulate_error: bool = False  # 1단계 데모용: 오류 상태 시연
    options: dict = {}            # 에이전트별 선택 항목 (코택: is_sme·revenue·months, 테스트: today 등)


@router.post("/agents/{agent_id}/run")
async def run_agent(agent_id: str, body: RunRequest):  # async: 이벤트 루프 위에서 asyncio 큐에 넣어야 안전
    _agent_or_404(agent_id)
    params = {**body.options, "simulate_error": body.simulate_error}
    try:
        return dispatch.start_task(agent_id, body.client_id, body.tax_year, params)
    except dispatch.DispatchError as e:
        raise HTTPException(e.status, e.message)


@router.get("/agents/{agent_id}/tasks")
def agent_tasks(agent_id: str):
    _agent_or_404(agent_id)
    return db.list_tasks(agent_id)


@router.get("/agents/{agent_id}/reviews")
def agent_reviews(agent_id: str):
    _agent_or_404(agent_id)
    return db.list_reviews(agent=agent_id)


class ApproveRequest(BaseModel):
    reviewer_name: str
    comment: str = ""


@router.post("/agents/{agent_id}/approve")
async def approve_agent(agent_id: str, body: ApproveRequest):  # async: 완료→대기중 복귀 타이머 등록
    """에이전트의 검토 대기 작업을 승인한다. (반려는 /tasks/{id}/review)"""
    _agent_or_404(agent_id)
    pending = review.pending_review_task(agent_id)
    if pending is None:
        raise HTTPException(409, "검토 대기 중인 작업이 없습니다")
    try:
        return review.submit_review(pending["id"], body.reviewer_name, "approve", body.comment)
    except review.ReviewError as e:
        raise HTTPException(400, str(e))


@router.get("/tasks/{task_id}")
def task_detail(task_id: int):
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(404, "작업을 찾을 수 없습니다")
    return {**task, "anomalies": db.list_anomalies(task_id), "reviews": db.list_reviews(task_id=task_id)}


@router.get("/tasks/{task_id}/items")
def task_items(task_id: int):
    """결산 체크리스트·PBC 항목 (북키 작업에서 생성)."""
    if db.get_task(task_id) is None:
        raise HTTPException(404, "작업을 찾을 수 없습니다")
    return {"checklist": db.list_work_items(task_id, "checklist"), "pbc": db.list_work_items(task_id, "pbc")}


class WorkItemUpdate(BaseModel):
    status: str | None = None
    note: str | None = None
    updated_by: str = ""


@router.put("/items/{item_id}")
def update_item(item_id: int, body: WorkItemUpdate):
    item = db.get_work_item(item_id)
    if item is None:
        raise HTTPException(404, "항목을 찾을 수 없습니다")
    fields: dict = {"updated_by": body.updated_by.strip()[:50]}
    if body.status is not None:
        if body.status not in db.WORK_STATUSES[item["kind"]]:
            raise HTTPException(400, f"상태값은 {', '.join(db.WORK_STATUSES[item['kind']])} 중 하나여야 합니다")
        fields["status"] = body.status
    if body.note is not None:
        fields["note"] = body.note[:500]
    db.update_work_item(item_id, **fields)
    return db.get_work_item(item_id)


@router.get("/tasks/{task_id}/download")
def download(task_id: int):
    task = db.get_task(task_id)
    if task is None or not task["output_path"]:
        raise HTTPException(404, "결과 파일이 없습니다")
    path = Path(task["output_path"]).resolve()
    if config.OUTPUT_DIR.resolve() not in path.parents or not path.exists():  # outputs/ 밖의 파일은 내려주지 않음
        raise HTTPException(404, "결과 파일을 찾을 수 없습니다")
    return FileResponse(path, filename=path.name)


class ReviewRequest(BaseModel):
    reviewer_name: str
    decision: str  # approve / reject
    comment: str = ""
    reason: str = ""
    on_reject: str = "rework"  # rework / error


@router.post("/tasks/{task_id}/review")
async def review_task(task_id: int, body: ReviewRequest):  # async: 완료→대기중 복귀 타이머 등록
    try:
        return review.submit_review(task_id, body.reviewer_name, body.decision,
                                    body.comment, body.reason, body.on_reject)
    except review.ReviewError as e:
        raise HTTPException(400, str(e))


# ---------- 업로드 (1단계: 저장·목록만. 파싱은 에이전트 구현 단계에서) ----------
def _safe_filename(name: str) -> str:
    name = Path(name or "").name  # 경로 제거
    name = re.sub(r"[^\w.\-가-힣 ()]", "_", name).strip(" .")
    return name or "upload"


@router.get("/agents/{agent_id}/uploads")
def list_uploads(agent_id: str):
    _agent_or_404(agent_id)
    folder = config.UPLOAD_DIR / agent_id
    if not folder.exists():
        return []
    files = sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "size": p.stat().st_size,
             "uploaded_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")} for p in files]


@router.post("/agents/{agent_id}/upload")
async def upload(agent_id: str, file: UploadFile):
    _agent_or_404(agent_id)
    name = _safe_filename(file.filename)
    if not name.lower().endswith(config.UPLOAD_EXTENSIONS):
        raise HTTPException(400, "엑셀(.xlsx, .xls) 또는 CSV 파일만 올릴 수 있어요")
    data = await file.read(config.UPLOAD_MAX_BYTES + 1)
    if len(data) > config.UPLOAD_MAX_BYTES:
        raise HTTPException(400, f"파일이 너무 큽니다 (최대 {config.UPLOAD_MAX_BYTES // 1024 // 1024}MB)")
    if not data:
        raise HTTPException(400, "빈 파일입니다")
    folder = config.UPLOAD_DIR / agent_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    activity.log_activity(agent_id, f"파일 업로드: {name}", kind="upload")
    return {"name": name, "size": len(data)}
