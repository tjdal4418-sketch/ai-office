"""SQLite(SQLAlchemy) 모델과 데이터 접근 함수. 결과는 dict 로 돌려준다."""
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine, func, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.core import config

_engine = None
_Session = None


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


class Base(DeclarativeBase):
    pass


class Task(Base):
    """작업. status: queued / working / review / done / error"""
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent: Mapped[str] = mapped_column(String(32), index=True)
    client_id: Mapped[str] = mapped_column(String(32))
    client_name: Mapped[str] = mapped_column(String(100), default="")
    title: Mapped[str] = mapped_column(String(100), default="")  # 무슨 작업(예: 급여 계산)
    tax_year: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    current_step: Mapped[str] = mapped_column(String(200), default="")
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str] = mapped_column(String(200), default="")  # 결과 한 줄 요약
    output_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Activity(Base):
    """실시간 활동 로그 피드. kind: start / review / done / reject / error / upload / info"""
    __tablename__ = "activity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    agent: Mapped[str] = mapped_column(String(32), index=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)


class Anomaly(Base):
    """이상항목 리포트. severity: error / warning / info"""
    __tablename__ = "anomalies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    row_ref: Mapped[str] = mapped_column(String(100), default="")


class WorkItem(Base):
    """작업에 딸린 체크 항목. kind: checklist(결산 체크리스트) / pbc(감사 PBC 요청자료)"""
    __tablename__ = "work_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(64), default="")
    group: Mapped[str] = mapped_column(String(32), default="")  # 주기(월/분기/연) 또는 PBC 분류
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text, default="")
    hint: Mapped[str] = mapped_column(Text, default="")        # 자동 점검 힌트
    status: Mapped[str] = mapped_column(String(16), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(50), default="")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# kind 별로 허용하는 상태값
WORK_STATUSES = {"checklist": ("open", "done"), "pbc": ("미준비", "준비중", "준비완료", "제출완료")}


class Review(Base):
    """검토 승인/반려 이력. decision: approve / reject"""
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    agent: Mapped[str] = mapped_column(String(32), index=True)
    reviewer_name: Mapped[str] = mapped_column(String(50))
    decision: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class StatusLog(Base):
    """에이전트 상태 전환 이력"""
    __tablename__ = "status_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16))
    current_step: Mapped[str] = mapped_column(String(200), default="")
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=_now)


def init_db(url: str | None = None) -> None:
    """엔진을 만들고 테이블을 생성한다. 테스트에서는 임시 DB URL 을 넘긴다."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(url or config.DB_URL, connect_args={"check_same_thread": False})
    _Session = sessionmaker(_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)


@contextmanager
def session_scope():
    if _Session is None:
        init_db()
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _row(obj) -> dict[str, Any]:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


# ---------- 작업 ----------
def create_task(agent: str, client_id: str, tax_year: int, params: dict | None = None,
                client_name: str = "", title: str = "") -> dict:
    with session_scope() as s:
        task = Task(agent=agent, client_id=client_id, client_name=client_name or client_id,
                    title=title, tax_year=tax_year, params=params or {})
        s.add(task)
        s.flush()
        return _row(task)


def get_task(task_id: int) -> dict | None:
    with session_scope() as s:
        task = s.get(Task, task_id)
        return _row(task) if task else None


def update_task(task_id: int, **fields) -> None:
    with session_scope() as s:
        s.execute(update(Task).where(Task.id == task_id).values(**fields))


def list_tasks(agent: str, limit: int = 50) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(Task).where(Task.agent == agent).order_by(Task.id.desc()).limit(limit))
        return [_row(r) for r in rows]


def latest_tasks(agent_ids: list[str], tax_year: int | None = None, limit: int = 500) -> dict[tuple[str, str], dict]:
    """(에이전트, 고객사) 별 가장 최근 작업 — 진행 현황 매트릭스용."""
    with session_scope() as s:
        q = select(Task).where(Task.agent.in_(agent_ids)).order_by(Task.id.desc()).limit(limit)
        if tax_year is not None:
            q = select(Task).where(Task.agent.in_(agent_ids), Task.tax_year == tax_year).order_by(Task.id.desc()).limit(limit)
        out: dict[tuple[str, str], dict] = {}
        for r in s.scalars(q):
            out.setdefault((r.agent, r.client_id), _row(r))
        return out


def latest_task_with_status(agent: str, status: str) -> dict | None:
    with session_scope() as s:
        row = s.scalars(
            select(Task).where(Task.agent == agent, Task.status == status).order_by(Task.id.desc()).limit(1)
        ).first()
        return _row(row) if row else None


def recover_interrupted() -> int:
    """서버 재시작 시 작업 중이던 건을 오류로 정리하고 건수를 돌려준다."""
    with session_scope() as s:
        result = s.execute(
            update(Task)
            .where(Task.status.in_(["queued", "working"]))
            .values(status="error", error_reason="서버 재시작으로 작업이 중단되었습니다",
                    result="오류: 서버 재시작으로 중단", finished_at=_now())
        )
        return result.rowcount or 0


# ---------- 이상항목 ----------
def clear_anomalies(task_id: int) -> None:
    with session_scope() as s:
        for row in s.scalars(select(Anomaly).where(Anomaly.task_id == task_id)):
            s.delete(row)


def add_anomalies(task_id: int, rows: list[dict]) -> None:
    with session_scope() as s:
        for r in rows:
            s.add(Anomaly(task_id=task_id, severity=r.get("severity", "warning"),
                          message=r["message"], row_ref=r.get("row_ref", "")))


def list_anomalies(task_id: int) -> list[dict]:
    with session_scope() as s:
        return [_row(r) for r in s.scalars(select(Anomaly).where(Anomaly.task_id == task_id).order_by(Anomaly.id))]


# ---------- 체크 항목 (결산 체크리스트·PBC) ----------
def replace_work_items(task_id: int, kind: str, items: list[dict]) -> None:
    """작업 재실행 시 기존 항목을 지우고 새로 만든다."""
    with session_scope() as s:
        for row in s.scalars(select(WorkItem).where(WorkItem.task_id == task_id, WorkItem.kind == kind)):
            s.delete(row)
        for it in items:
            s.add(WorkItem(task_id=task_id, kind=kind, key=it.get("key", ""), group=it.get("group", ""),
                           title=it["title"], detail=it.get("detail", ""), hint=it.get("hint", ""),
                           status=it["status"], note=it.get("note", "")))


def list_work_items(task_id: int, kind: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(WorkItem).where(WorkItem.task_id == task_id).order_by(WorkItem.id)
        if kind:
            q = q.where(WorkItem.kind == kind)
        return [_row(r) for r in s.scalars(q)]


def get_work_item(item_id: int) -> dict | None:
    with session_scope() as s:
        row = s.get(WorkItem, item_id)
        return _row(row) if row else None


def update_work_item(item_id: int, **fields) -> None:
    with session_scope() as s:
        s.execute(update(WorkItem).where(WorkItem.id == item_id).values(updated_at=_now(), **fields))


# ---------- 검토 이력 ----------
def add_review(task_id: int, agent: str, reviewer_name: str, decision: str,
               comment: str = "", reason: str = "") -> dict:
    with session_scope() as s:
        review = Review(task_id=task_id, agent=agent, reviewer_name=reviewer_name,
                        decision=decision, comment=comment, reason=reason)
        s.add(review)
        s.flush()
        return _row(review)


def list_reviews(agent: str | None = None, task_id: int | None = None, limit: int = 100) -> list[dict]:
    with session_scope() as s:
        q = select(Review).order_by(Review.id.desc()).limit(limit)
        if agent:
            q = q.where(Review.agent == agent)
        if task_id:
            q = q.where(Review.task_id == task_id)
        return [_row(r) for r in s.scalars(q)]


# ---------- 상태 이력 ----------
def add_status_log(agent: str, status: str, current_step: str = "", task_id: int | None = None) -> None:
    with session_scope() as s:
        s.add(StatusLog(agent=agent, status=status, current_step=current_step, task_id=task_id))


# ---------- 활동 로그 ----------
def add_activity(agent: str, message: str, task_id: int | None = None, kind: str = "info") -> dict:
    with session_scope() as s:
        row = Activity(agent=agent, message=message, task_id=task_id, kind=kind)
        s.add(row)
        s.flush()
        return _row(row)


def list_activity(limit: int = 50, agent: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(Activity).order_by(Activity.id.desc()).limit(limit)
        if agent:
            q = q.where(Activity.agent == agent)
        return [_row(r) for r in s.scalars(q)]


def summary(day: date) -> dict:
    """홈 하단 요약. processed: 오늘 만든 작업 중 AI 처리가 끝난 건(검토필요·완료),
    review_waiting: 현재 검토 대기 전체, errors: 오늘 만든 작업 중 오류 건."""
    start = datetime.combine(day, datetime.min.time())
    with session_scope() as s:
        def count(*conds) -> int:
            return s.scalar(select(func.count()).select_from(Task).where(*conds)) or 0
        return {
            "processed": count(Task.created_at >= start, Task.status.in_(["review", "done"])),
            "review_waiting": count(Task.status == "review"),
            "errors": count(Task.created_at >= start, Task.status == "error"),
        }


def list_status_log(agent: str | None = None, limit: int = 100) -> list[dict]:
    with session_scope() as s:
        q = select(StatusLog).order_by(StatusLog.id.desc()).limit(limit)
        if agent:
            q = q.where(StatusLog.agent == agent)
        return [_row(r) for r in s.scalars(q)]
