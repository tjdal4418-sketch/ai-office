"""📁 매니저 — 월마감 업무 배분·신고기한 캘린더·전체 진행 현황 에이전트.

'월마감 시작'을 받으면 선택한 고객사의 업무를 페이 → 북키 → 배트 → 코택 순서로 나눠 준다.
앞 작업이 '검토필요'(작성 완료)가 되면 다음 작업을 시작하고, 북키가 만든 시산표는 코택이 이어받는다.
선행(북키)이 오류면 배트·코택은 건너뛰고 이유를 보고한다. 각 작업의 최종 확정은 해당 담당자의 검토 승인이며,
매니저는 배분과 현황 리포트까지만 한다(리포트 확인이 매니저의 검토 승인).
작업 순서(BaseAgent.run 이 고정): 입력 검증 → 업무 배분(계산) → 이상항목 리포트 → 결과 엑셀 → 검토필요.
"""
import asyncio
import time
from datetime import date, datetime, timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.agents.base import BaseAgent, ValidationError
from app.agents.payroll_calc import issue
from app.core import clients, config, db, deadlines, dispatch, progress, review, state

SHEET_DIST, SHEET_MATRIX, SHEET_CAL, SHEET_BASIS = "월마감 배분 결과", "진행 현황", "신고기한 캘린더", "계산근거"
HEAD_FILL = PatternFill("solid", fgColor="DDEBF7")
WARN_FILL = PatternFill("solid", fgColor="FFF2CC")
ERR_FILL = PatternFill("solid", fgColor="F8CBAD")
OK_FILL = PatternFill("solid", fgColor="E2EFDA")
STATUS_FILL = {"검토필요": WARN_FILL, "오류": ERR_FILL, "완료": OK_FILL, "진행중": PatternFill("solid", fgColor="DDEBF7")}

STEPS = progress.WORK_AGENTS
# 선행 작업이 오류·건너뜀이면 이 작업도 건너뛴다 (북키 시산표를 코택이 이어받고, 배트도 북키 마감 이후에 진행)
DEPENDS = {"vat": ["bookkeeping"], "corptax": ["bookkeeping"]}
CALENDAR_DAYS = 90

NOT_REFLECTED = [
    "배트는 아직 북키 결과가 아닌 별도 장부 매출·매입 파일(sample_data/vat 또는 업로드)과 대사합니다 — 북키 완료 후 시작하는 순서 의존만 연동",
    "페이 급여 집계 → 북키 급여 분개 연동은 이번에 구현하지 않음",
    "신고기한은 법령 검증 전 값이며 공휴일 목록도 임시입니다 (rules/<연도>/deadlines.yaml, holidays.yaml)",
    "각 작업의 승인은 해당 에이전트 화면에서 담당자가 따로 합니다 (매니저 승인 ≠ 각 업무 승인)",
]


class ManagerAgent(BaseAgent):
    agent_id = "manager"
    name = "매니저"
    icon = "📁"
    role = "Office Manager"
    summary = "월마감 업무 배분, 신고기한 캘린더, 전체 진행 현황 리포트"
    task_name = "월마감 업무 배분"

    # ------------------------------------------------------------------
    # 1) 입력 검증
    # ------------------------------------------------------------------
    async def validate(self, ctx: dict) -> None:
        await super().validate(ctx)  # 오류 상태 시연(simulate_error)
        if clients.get_client(ctx["client_id"]) is None:
            raise ValidationError("존재하지 않는 고객사입니다")
        if ctx["tax_year"] not in dispatch.tax_years():
            raise ValidationError(f"{ctx['tax_year']}년 규칙 폴더가 없습니다")
        raw = ctx["params"].get("today")
        ctx["today"] = date.fromisoformat(raw) if raw else date.today()
        # 하위 작업에 넘길 옵션: 시연 오류 플래그는 매니저에게만 적용
        ctx["sub_params"] = {k: v for k, v in ctx["params"].items() if k != "simulate_error"}
        self.progress(ctx, 3, "고객사·적용연도 확인 완료")

    # ------------------------------------------------------------------
    # 2) 업무 배분 (순서대로 시작하고 완료까지 지켜봄)
    # ------------------------------------------------------------------
    async def compute(self, ctx: dict) -> None:
        from app.agents import REGISTRY  # 순환 import 방지
        results: dict[str, dict] = {}
        n = len(STEPS)
        for i, agent_id in enumerate(STEPS):
            agent = REGISTRY[agent_id]
            lo, hi = 5 + int(85 * i / n), 5 + int(85 * (i + 1) / n)
            label = f"{agent.icon} {agent.name}"
            self.progress(ctx, lo, f"{label} 업무 배분 중 ({i + 1}/{n})")
            bad = [d for d in DEPENDS.get(agent_id, []) if results[d]["status"] in ("error", "skipped")]
            if bad:
                names = ", ".join(REGISTRY[d].name for d in bad)
                results[agent_id] = self._step(agent_id, "skipped", None, f"선행 작업({names})이 오류·건너뜀이라 시작하지 않음")
                self.log(ctx, f"{label} 건너뜀 — 선행 작업({names}) 오류", kind="info")
                continue
            results[agent_id] = await self._run_step(ctx, agent_id, label, lo, hi)
        ctx["steps"] = results

    @staticmethod
    def _step(agent_id: str, status: str, task: dict | None, note: str = "", reused: bool = False) -> dict:
        return {"agent_id": agent_id, "status": status, "task_id": task["id"] if task else None,
                "result": (task["result"] if task else "") or "", "error": (task["error_reason"] if task else "") or "",
                "note": note, "reused": reused}

    async def _run_step(self, ctx: dict, agent_id: str, label: str, lo: int, hi: int) -> dict:
        st = state.get_state(agent_id)
        if st.status in ("working", "review"):
            pending = review.pending_review_task(agent_id)
            if pending and pending["client_id"] == ctx["client_id"] and pending["tax_year"] == ctx["tax_year"]:
                self.log(ctx, f"{label}: 이미 검토 대기 중인 작업 #{pending['id']} 를 이어받음")
                return self._step(agent_id, pending["status"], pending, "이미 검토 대기 중인 작업을 사용함", reused=True)
            self.log(ctx, f"{label} 건너뜀 — 다른 작업 진행 중", kind="info")
            return self._step(agent_id, "skipped", None, f"{st.client_name or '다른 고객사'} 작업이 진행·검토 대기 중이라 시작하지 않음")
        try:
            sub = dispatch.start_task(agent_id, ctx["client_id"], ctx["tax_year"], dict(ctx["sub_params"]))
        except dispatch.DispatchError as e:
            return self._step(agent_id, "skipped", None, f"시작하지 못함: {e.message}")
        self.log(ctx, f"{label} 업무 배분 → 작업 #{sub['id']}")
        deadline = time.monotonic() + config.MANAGER_STEP_TIMEOUT
        while True:
            t = db.get_task(sub["id"])
            if t["status"] in ("review", "done", "error"):
                break
            if time.monotonic() > deadline:
                return self._step(agent_id, "error", t, f"{config.MANAGER_STEP_TIMEOUT:.0f}초 안에 끝나지 않음")
            cur = state.get_state(agent_id)
            pct = lo + int((hi - lo) * 0.9 * (cur.progress or 0) / 100)
            self.progress(ctx, min(pct, hi - 1), f"{label} 작업 중 — {cur.current_step}")
            await asyncio.sleep(config.MANAGER_POLL)
        note = "작성 완료 — 담당자 검토 승인 대기" if t["status"] == "review" else ""
        return self._step(agent_id, t["status"], t, note)

    # ------------------------------------------------------------------
    # 3) 이상항목(배분 결과·임박한 신고기한) 리포트
    # ------------------------------------------------------------------
    async def report_anomalies(self, ctx: dict) -> None:
        from app.agents import REGISTRY
        self.progress(ctx, 93, "진행 현황·신고기한 점검 중")
        found: list[dict] = []
        for agent_id in STEPS:
            s, a = ctx["steps"][agent_id], REGISTRY[agent_id]
            ref = f"{a.name} 작업 #{s['task_id']}" if s["task_id"] else a.name
            label = f"{a.icon} {a.name}"
            if s["status"] == "error":
                found.append(issue("error", "업무 오류", f"{label}: {s['error'] or s['note'] or '오류'}", ref))
            elif s["status"] == "skipped":
                found.append(issue("warning", "업무 건너뜀", f"{label}: {s['note']}", ref))
            elif s["status"] == "review":
                found.append(issue("info", "검토 대기", f"{label}: {s['result'] or '작성 완료'} — 담당자가 해당 화면에서 검토 승인", ref))
            else:
                found.append(issue("info", "업무 완료", f"{label}: {s['result']}", ref))
        today = ctx["today"]
        events = deadlines.events_between(today, today + timedelta(days=CALENDAR_DAYS), today)
        ctx["events"] = events
        soon = [e for e in events if e["d_day"] <= 14]
        for e in soon[:6]:
            sev = "warning" if e["d_day"] <= 3 else "info"
            tag = "" if e["verified"] else " (기한 규칙 검증 전)"
            shift = f" · 원래 {e['original_date'][5:]} → {e['shift_reason']}로 순연" if e["shifted"] else ""
            found.append(issue(sev, "신고기한", f"{e['name']} {e['date']} (D-{e['d_day']}){shift}{tag}", "신고기한 캘린더"))
        unverified = sum(1 for e in events if not e["verified"])
        found.append(issue("info", "기한 규칙 검증 전", f"향후 {CALENDAR_DAYS}일 신고기한 {unverified}건이 verified: false — 법령 MCP로 검증 필요", "app/rules/2026/deadlines.yaml"))
        ctx["anomalies"] = found
        db.add_anomalies(ctx["task_id"], found)

    def rules_warnings(self, ctx: dict) -> list[str]:
        n = sum(1 for e in ctx.get("events", []) if not e["verified"])
        return [f"신고기한 {n}건이 검증 전(verified: false)이며 공휴일 목록은 임시입니다 — 법령 MCP로 확인 필요",
                "월마감 배분은 페이→북키→배트→코택 순서이며, 각 작업의 승인은 담당자가 각 화면에서 합니다."]

    async def request_review(self, ctx: dict) -> None:
        await super().request_review(ctx)
        from app.agents import REGISTRY
        parts = []
        for agent_id in STEPS:
            s = ctx["steps"][agent_id]
            label = "건너뜀" if s["status"] == "skipped" else progress.STATUS_LABEL.get(s["status"], s["status"])
            parts.append(f"{REGISTRY[agent_id].icon}{label}")
        db.update_task(ctx["task_id"], result=f"월마감 업무 배분: {' '.join(parts)}")

    # ------------------------------------------------------------------
    # 4) 결과 엑셀 (진행 현황·신고기한 캘린더 — 계산 셀은 수식)
    # ------------------------------------------------------------------
    def build_workbook(self, ctx: dict) -> Workbook:
        wb = Workbook()
        ws_d = wb.active
        ws_d.title = SHEET_DIST
        ws_m = wb.create_sheet(SHEET_MATRIX)
        ws_c = wb.create_sheet(SHEET_CAL)
        ws_b = wb.create_sheet(SHEET_BASIS)
        self._write_dist(ws_d, ctx)
        write_matrix_sheet(ws_m, progress.matrix(ctx["tax_year"]), ctx["today"])
        write_calendar_sheet(ws_c, ctx["events"], ctx["today"])
        self._write_basis(ws_b, ctx)
        return wb

    def _write_dist(self, ws, ctx: dict) -> None:
        from app.agents import REGISTRY
        ws.cell(row=1, column=1, value=f"월마감 업무 배분 — {ctx['client_name']} · {ctx['tax_year']}년 (기준일 {ctx['today']})").font = Font(bold=True, size=12)
        _head(ws, 3, ["순서", "업무", "작업번호", "상태", "결과 요약", "비고"])
        for i, agent_id in enumerate(STEPS, 1):
            s, a = ctx["steps"][agent_id], REGISTRY[agent_id]
            label = "건너뜀" if s["status"] == "skipped" else progress.STATUS_LABEL.get(s["status"], s["status"])
            ws.append([i, f"{a.icon} {a.name} ({a.task_name})", s["task_id"], label, s["result"] or s["error"], s["note"] + (" (기존 작업 이어받음)" if s["reused"] else "")])
            r = ws.max_row
            ws.cell(row=r, column=4).fill = STATUS_FILL.get(label, WARN_FILL)
            for c in (5, 6):
                ws.cell(row=r, column=c).alignment = Alignment(wrap_text=True, vertical="top")
        last = 3 + len(STEPS)
        ws.cell(row=last + 2, column=2, value="검토필요").font = Font(bold=True)
        ws.cell(row=last + 2, column=3, value=f'=COUNTIF(D4:D{last},B{last + 2})')
        ws.cell(row=last + 3, column=2, value="오류·건너뜀").font = Font(bold=True)
        ws.cell(row=last + 3, column=3, value=f'=COUNTIF(D4:D{last},"오류")+COUNTIF(D4:D{last},"건너뜀")')
        ws.cell(row=last + 5, column=1, value="※ 북키가 만든 시산표는 코택이 이어받습니다. 각 업무의 승인은 해당 담당자가 각 에이전트 화면에서 합니다.")
        _widths(ws, [6, 34, 10, 12, 80, 50])

    def _write_basis(self, ws, ctx: dict) -> None:
        meta = [("에이전트", f"{self.icon} {self.name}"), ("고객사", ctx["client_name"]), ("적용연도(기준연도)", ctx["tax_year"]),
                ("기준일", ctx["today"].isoformat()), ("배분 순서", " → ".join(STEPS)),
                ("순연 근거", deadlines.shift_source(ctx["today"])), ("생성시각", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                ("상태", "AI 작성본 — 담당자의 검토 승인 전")]
        for k, v in meta:
            ws.append([k, v])
            ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        for w in self.rules_warnings(ctx):
            ws.append(["경고", w])
            for c in (1, 2):
                ws.cell(row=ws.max_row, column=c).fill = WARN_FILL
        ws.append([])
        ws.append(["이번 단계에서 반영하지 않은 항목"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        for t in NOT_REFLECTED:
            ws.append(["-", t])
        _widths(ws, [24, 110])


# ----------------------------------------------------------------------
# 공용 시트 작성 (월마감 결과 엑셀과 /api/progress.xlsx 가 같이 씀)
# ----------------------------------------------------------------------
def _head(ws, row: int, labels: list[str]) -> None:
    for c, label in enumerate(labels, 1):
        cell = ws.cell(row=row, column=c, value=label)
        cell.font, cell.fill = Font(bold=True), HEAD_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _widths(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def write_matrix_sheet(ws, matrix: dict, today: date) -> None:
    """고객사 × 업무 상태 매트릭스 + 상태별 건수(수식)."""
    year = f"{matrix['tax_year']}년 " if matrix.get("tax_year") else ""
    ws.cell(row=1, column=1, value=f"전체 진행 현황 — {year}고객사별 업무 상태 (기준일 {today})").font = Font(bold=True, size=12)
    _head(ws, 3, ["고객사"] + [f"{a['icon']} {a['name']}" for a in matrix["agents"]])
    for row in matrix["rows"]:
        ws.append([row["client_name"]] + [progress.cell_text(row["cells"][a["id"]]) for a in matrix["agents"]])
        r = ws.max_row
        for c, a in enumerate(matrix["agents"], 2):
            cell = row["cells"][a["id"]]
            fill = STATUS_FILL.get(cell["label"])
            if fill:
                ws.cell(row=r, column=c).fill = fill
    first, last = 4, 3 + len(matrix["rows"])
    last_col = get_column_letter(1 + len(matrix["agents"]))
    rng = f"B{first}:{last_col}{last}"
    start = last + 2
    ws.cell(row=start, column=1, value="상태별 건수").font = Font(bold=True)
    for k, label in enumerate(["작업 없음", "진행중", "검토필요", "완료", "오류"]):
        ws.cell(row=start + 1 + k, column=1, value=label)
        ws.cell(row=start + 1 + k, column=2, value=f'=COUNTIF({rng},A{start + 1 + k}&"*")')
    ws.cell(row=start + 7, column=1, value="※ 각 칸은 고객사·업무별 가장 최근 작업의 상태입니다. (#번호 = 작업 번호)")
    _widths(ws, [24] + [22] * len(matrix["agents"]))


def write_calendar_sheet(ws, events: list[dict], today: date) -> None:
    ws.cell(row=1, column=1, value="신고기한 캘린더 (토·일·공휴일은 다음 영업일로 순연)").font = Font(bold=True, size=12)
    ws.cell(row=2, column=1, value="기준일")
    ws.cell(row=2, column=2, value=today).number_format = "yyyy-mm-dd"
    _head(ws, 4, ["신고기한(순연 후)", "원래 기한", "순연 사유", "D-day", "업무", "분류", "근거", "검증"])
    for k, e in enumerate(events):
        r = 5 + k
        ws.cell(row=r, column=1, value=date.fromisoformat(e["date"])).number_format = "yyyy-mm-dd"
        ws.cell(row=r, column=2, value=date.fromisoformat(e["original_date"])).number_format = "yyyy-mm-dd"
        ws.cell(row=r, column=3, value=e["shift_reason"] + ("" if e["holiday_known"] or not e["shifted"] else " (공휴일 정보 없음)"))
        ws.cell(row=r, column=4, value=f"=A{r}-$B$2").number_format = "0"
        ws.cell(row=r, column=5, value=e["name"])
        ws.cell(row=r, column=6, value=e["category"])
        ws.cell(row=r, column=7, value=e["source"])
        ws.cell(row=r, column=8, value="검증됨" if e["verified"] else "검증 전")
        if not e["verified"]:
            ws.cell(row=r, column=8).fill = WARN_FILL
        if e["shifted"]:
            ws.cell(row=r, column=3).fill = WARN_FILL
    if not events:
        ws.cell(row=5, column=1, value="(기간 내 신고기한 없음)")
    _widths(ws, [16, 14, 30, 8, 36, 20, 70, 10])
    ws.freeze_panes = "A5"


def build_progress_workbook(tax_year: int | None, today: date | None = None) -> Workbook:
    """작업과 무관하게 지금 시점의 진행 현황·신고기한 캘린더 엑셀을 만든다 (/api/progress.xlsx)."""
    today = today or date.today()
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_MATRIX
    write_matrix_sheet(ws, progress.matrix(tax_year), today)
    write_calendar_sheet(wb.create_sheet(SHEET_CAL), deadlines.events_between(today, today + timedelta(days=CALENDAR_DAYS), today), today)
    return wb
