"""전체 진행 현황 — 고객사 × 업무(페이·북키·배트·코택) 상태 매트릭스. 매니저 리포트·화면·엑셀이 같이 쓴다."""
from app.core import clients, db

# 월마감 업무 순서 (매니저가 이 순서대로 배분)
WORK_AGENTS = ["payroll", "bookkeeping", "vat", "corptax"]

STATUS_LABEL = {None: "작업 없음", "queued": "진행중", "working": "진행중", "review": "검토필요", "done": "완료", "error": "오류"}


def matrix(tax_year: int | None = None) -> dict:
    """{"agents": [{id,name,icon}], "rows": [{client_id, client_name, cells: {agent_id: {...}}}]}"""
    from app.agents import REGISTRY  # 순환 import 방지
    latest = db.latest_tasks(WORK_AGENTS, tax_year)
    rows = []
    for c in clients.load_clients():
        cells = {}
        for aid in WORK_AGENTS:
            t = latest.get((aid, c["id"]))
            cells[aid] = {
                "status": t["status"] if t else None,
                "label": STATUS_LABEL[t["status"] if t else None],
                "task_id": t["id"] if t else None,
                "progress": t["progress"] if t else None,
                "step": t["current_step"] if t else "",
                "result": (t["result"] or "") if t else "",
                "error": (t["error_reason"] or "") if t else "",
                "finished_at": t["finished_at"] if t else None,
            }
        rows.append({"client_id": c["id"], "client_name": c["name"], "cells": cells})
    agents = [{"id": a, "name": REGISTRY[a].name, "icon": REGISTRY[a].icon} for a in WORK_AGENTS]
    return {"agents": agents, "rows": rows, "tax_year": tax_year}


def cell_text(cell: dict) -> str:
    """엑셀·화면용 한 줄 표시: '검토필요 (#12)'"""
    return cell["label"] if cell["task_id"] is None else f"{cell['label']} (#{cell['task_id']})"
