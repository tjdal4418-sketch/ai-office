"""모든 에이전트의 공통 부모.

작업 순서(고정): 입력 검증 → 계산/대사 → 이상항목 리포트 → 결과파일 생성 → 검토필요
상태 변경은 항상 core.state.set_status() 를 거친다(BaseAgent.set_status 가 감싸서 호출).
1단계 기본 구현은 '데모 작업'(10초 동안 진행률 0→100%, 샘플 엑셀 생성)이며,
실제 에이전트는 validate/compute/... 단계 메서드를 override 한다.
"""
import asyncio
from datetime import datetime

from openpyxl import Workbook

from app.core import activity, config, db, state

TICK_SECONDS = 0.5  # 데모 진행률 갱신 주기


class ValidationError(Exception):
    """입력 검증 실패 — 작업을 오류 상태로 돌린다."""

    def __init__(self, reason: str, anomalies: list[dict] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.anomalies = anomalies or []


class BaseAgent:
    agent_id: str = ""
    name: str = ""
    icon: str = ""
    role: str = ""
    summary: str = ""
    task_name: str = "작업"  # 활동 로그·이력에 쓰는 작업명 (예: 급여 계산)
    demo_steps: list[str] = []
    coming_soon: list[str] = []  # 아직 구현하지 않은 '추후 확장' 메뉴 이름(화면에 비활성으로 표시)

    # LLM 연동 훅 — 선택 기능. 1차에는 구현하지 않고 자리만 둔다.
    llm_hook = None

    def meta(self) -> dict:
        return {"id": self.agent_id, "name": self.name, "icon": self.icon,
                "role": self.role, "summary": self.summary, "task_name": self.task_name,
                "coming_soon": self.coming_soon}

    # ---------- 상태·로그 (공통 함수) ----------
    def set_status(self, ctx: dict, status: str, *, progress: int | None = None,
                   current_step: str = "", error_reason: str | None = None) -> None:
        """작업 컨텍스트를 붙여 core.state.set_status 를 호출한다."""
        state.set_status(self.agent_id, status, progress=progress, current_step=current_step,
                         error_reason=error_reason, task_id=ctx["task_id"], client_name=ctx["client_name"])

    def log(self, ctx: dict, message: str, kind: str = "info") -> None:
        """활동 로그 피드에 한 줄 남긴다(예: '(주)가나상사 급여 계산 시작')."""
        activity.log_activity(self.agent_id, message, task=ctx["task"], kind=kind)

    def progress(self, ctx: dict, pct: int, text: str) -> None:
        db.update_task(ctx["task_id"], status="working", progress=pct, current_step=text)
        self.set_status(ctx, "working", progress=pct, current_step=text)

    def fail(self, ctx: dict, reason: str, anomalies: list[dict] | None = None) -> None:
        if anomalies:
            db.add_anomalies(ctx["task_id"], anomalies)
        db.update_task(ctx["task_id"], status="error", error_reason=reason,
                       result=f"오류: {reason}", finished_at=datetime.now())
        self.set_status(ctx, "error", error_reason=reason, current_step="작업 중단")
        self.log(ctx, f"오류 — {reason}", kind="error")

    # ---------- 실행 골격 ----------
    async def run(self, task_id: int) -> None:
        task = db.get_task(task_id)
        ctx = {"task_id": task_id, "task": task, "client_id": task["client_id"],
               "client_name": task["client_name"], "tax_year": task["tax_year"],
               "params": task["params"] or {}, "anomalies": [], "output_path": None}
        db.clear_anomalies(task_id)  # 반려 후 재작업 시 이전 리포트 정리
        db.update_task(task_id, status="working", progress=0, error_reason=None, result="",
                       output_path=None, started_at=datetime.now(), finished_at=None)
        self.log(ctx, "시작", kind="start")
        try:
            self.progress(ctx, 0, "입력 검증 중")
            await self.validate(ctx)
            await self.compute(ctx)
            await self.report_anomalies(ctx)
            await self.write_output(ctx)
            await self.request_review(ctx)
        except ValidationError as e:
            self.fail(ctx, e.reason, e.anomalies)
        except Exception as e:  # 예기치 못한 오류도 조용히 삼키지 않고 화면에 사유를 보여준다
            self.fail(ctx, f"처리 중 오류: {e}")

    # ---------- 단계 (하위 클래스가 override) ----------
    async def validate(self, ctx: dict) -> None:
        if ctx["params"].get("simulate_error"):
            await asyncio.sleep(config.DEMO_SECONDS * 0.2)
            raise ValidationError(
                "주민번호 누락 2건",
                [{"severity": "error", "message": "주민번호 누락", "row_ref": "행 7"},
                 {"severity": "error", "message": "주민번호 누락", "row_ref": "행 12"}],
            )

    async def compute(self, ctx: dict) -> None:
        """데모: DEMO_SECONDS 동안 진행률을 올린다(0.5초마다 갱신)."""
        steps = self.demo_steps or ["계산 중"]
        total = config.DEMO_SECONDS
        ticks = max(1, round(total / TICK_SECONDS)) if total > 0 else 1
        for i in range(1, ticks + 1):
            pct = int(95 * i / ticks)  # 마지막 5%는 리포트·파일 생성 단계
            self.progress(ctx, pct, steps[min(len(steps) - 1, (i - 1) * len(steps) // ticks)])
            if total > 0:
                await asyncio.sleep(total / ticks)

    async def report_anomalies(self, ctx: dict) -> None:
        self.progress(ctx, 96, "이상항목 점검 중")
        ctx["anomalies"] = [{"severity": "warning", "message": "[데모] 검토가 필요한 항목 예시", "row_ref": "행 3"}]
        db.add_anomalies(ctx["task_id"], ctx["anomalies"])

    async def write_output(self, ctx: dict) -> None:
        self.progress(ctx, 98, "결과 파일 생성 중")
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = config.OUTPUT_DIR / f"{self.agent_id}_{ctx['task_id']}_{stamp}.xlsx"
        self.build_workbook(ctx).save(path)
        ctx["output_path"] = str(path)

    async def request_review(self, ctx: dict) -> None:
        db.update_task(ctx["task_id"], status="review", progress=100, current_step="사람의 검토 승인 대기",
                       result="검토 승인 대기", output_path=ctx["output_path"])
        self.set_status(ctx, "review", current_step="검토 승인 대기 중")
        self.log(ctx, "작업 완료 — 검토 승인 대기", kind="review")

    # ---------- 엑셀 ----------
    def rules_warnings(self, ctx: dict) -> list[str]:
        """'계산근거' 시트에 표시할 경고. (예: 간이세액표 전체표 아님) 하위 클래스에서 추가."""
        return ["※ 1단계 데모 결과입니다. 실제 계산이 아니며 법령 수치(rules)도 아직 비어 있습니다."]

    def build_workbook(self, ctx: dict) -> Workbook:
        wb = Workbook()
        ws = wb.active
        ws.title = "결과"
        ws.append(["항목", "금액"])
        for label, amount in (("항목 A", 1_000_000), ("항목 B", 2_000_000), ("항목 C", 500_000)):
            ws.append([label, amount])
        ws.append(["합계", "=SUM(B2:B4)"])  # 계산 셀은 엑셀 수식으로 유지

        basis = wb.create_sheet("계산근거")
        basis.append(["에이전트", f"{self.icon} {self.name}"])
        basis.append(["고객사", ctx["client_name"]])
        basis.append(["적용연도", ctx["tax_year"]])
        basis.append(["생성시각", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        basis.append(["상태", "AI 작성본 — 담당자의 검토 승인 전"])
        for w in self.rules_warnings(ctx):
            basis.append(["경고", w])
        return wb
