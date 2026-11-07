"""rules 폴더의 모든 수치를 '항목명·값·근거 법령·적용연도·확인일·확인필요 여부'로 돌려주는 카탈로그.

GET /api/rules (오피스 뷰 '법령 자료실')와 docs/rules_check.md 가 같은 함수를 쓴다 — 읽기 전용이며 계산에는 관여하지 않는다.
'확인필요' = verified 가 true 가 아니거나 값이 비어(null) 있는 항목 (화면에서 빨간 'TODO: 확인 필요').
'외부 자료 반입 필요' = 위와 같은 상태지만 yaml 에 external 안내가 있는 항목 — 법령 MCP 로 확인할 수 없고 홈택스 표 등을 받아 넣어야 한다 (주황, 따로 센다).
"""
import re
from datetime import datetime
from pathlib import Path

import yaml

from app.core import config

# 값 항목({value, source, year, verified, verified_on})으로 이루어진 파일
LEAF_FILES = {
    "insurance": "4대보험 요율·한도", "payroll": "급여 일반(비과세 한도·최저임금)", "withholding": "원천세(세율·공제·신고 코드)",
    "vat": "부가세(세율·가산세·불공제 키워드)", "corptax": "법인세(기업업무추진비 한도·인정이자 등)", "bookkeeping": "북키 점검 기준",
}
# 사무실 업무 양식·규칙 (법령 수치 아님, 샘플이라 사무실 절차에 맞게 보정 필요)
TEMPLATE_FILES = {
    "account_mapping": "계정과목 추천 키워드 규칙", "closing_checklist": "결산 체크리스트 템플릿", "pbc_template": "감사 PBC 요청자료 템플릿",
    "tax_adjustment_items": "세무조정 항목 정의", "audit_request_template": "세무조사·감사 요청자료 템플릿",
}
# 항목명에 자주 나오는 영어 키 → 한글 (YAML 주석이 없을 때 쓰는 보조 이름)
KEY_KO = {
    "employee_rate": "근로자 요율", "employer_rate": "사업주 요율", "rate": "요율", "truncate_unit": "절사 단위", "floor": "하한",
    "cap": "상한", "applies": "적용기간", "base_truncate_unit": "기준 절사 단위", "total_rate": "합계 요율", "meal_monthly_limit": "식대 월 한도",
    "own_car_monthly_limit": "자가운전보조금 월 한도", "hourly": "시급", "monthly_hours": "월 환산 시간", "salary_change_ratio": "급여 변동 기준",
    "basic_limit_sme": "기본한도(중소기업)", "basic_limit_general": "기본한도(일반기업)", "tier1_rate": "1구간 적용률", "tier2_over": "2구간 시작금액",
    "tier2_rate": "2구간 적용률", "tier3_over": "3구간 시작금액", "tier3_rate": "3구간 적용률", "fixed_rate": "고정 설정률",
    "depreciation_limit": "감가상각비 한도(1대당)", "log_threshold": "운행기록부 기준", "required_expense_rate": "필요경비율",
    "minimum_taxable_income": "과세최저한", "one_child": "자녀 1명", "two_children": "자녀 2명", "extra_per_child": "3명째부터 1명당",
    "wage_simple": "근로소득(간이세액) 코드", "business": "사업소득 코드", "other": "기타소득 코드", "file": "파일", "tolerance": "허용오차",
    "watch_accounts": "감시 계정", "stale_days": "장기 미정리 기준일", "standard_rate": "세율", "amount_tolerance": "금액 허용오차",
    "issue_deadline_day": "발급기한(익월 N일)", "late_issue_rate": "지연발급 가산세율", "unissued_rate": "미발급 가산세율",
    "entertainment": "기업업무추진비 키워드", "vehicle": "승용차 키워드", "exempt": "면세 키워드", "personal": "사업무관 키워드",
    "bank": "은행 계정", "card_payable": "카드 미지급금 계정", "unclassified_out": "미분류 출금 계정", "unclassified_in": "미분류 입금 계정",
}


def rule_years() -> list[int]:
    if not config.RULES_DIR.exists():
        return []
    return sorted(int(p.name) for p in config.RULES_DIR.iterdir() if p.is_dir() and p.name.isdigit())


def _fmt_value(key: str, v) -> str:
    if v is None:
        return "(값 없음)"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, float) and v < 1 and "rate" in key:
        return f"{v} ({v * 100:.4g}%)"
    if isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) >= 10000:
        return f"{v:,}"
    return str(v)


def _is_leaf(node) -> bool:
    return isinstance(node, dict) and "value" in node


def _walk(node, prefix=""):
    for k, v in node.items():
        path = f"{prefix}.{k}" if prefix else str(k)
        if _is_leaf(v):
            yield path, k, v
        elif isinstance(v, dict):
            yield from _walk(v, path)


def _key_comments(text: str) -> dict[str, str]:
    """YAML 본문에서 '키:   # 설명' 줄의 설명을 경로(점으로 이은 키)별로 모은다 — 항목 이름을 사람이 읽기 좋게 하려는 용도."""
    comments, stack = {}, []
    for line in text.splitlines():
        m = re.match(r"^(\s*)([A-Za-z_]\w*):\s*(?:#\s*(.*))?$", line)
        if not m:
            continue
        indent, key, comment = len(m.group(1)), m.group(2), (m.group(3) or "").strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        if comment:
            comments[".".join(k for _, k in stack)] = comment
    return comments


def _label(path: str, key: str, comments: dict[str, str]) -> str:
    """'국민연금 — 기준소득월액 하한' 처럼: 가장 가까운 그룹 설명 + (항목 설명 | 한글 키 이름 | 영어 키)."""
    parts = path.split(".")
    own = comments.get(path) or KEY_KO.get(key) or key
    group = ""
    for i in range(len(parts) - 1, 1, -1):  # parts[:i] = 파일명 + 상위 그룹들 (가까운 그룹부터)
        c = comments.get(".".join(parts[:i]))
        if c:
            group = c
            break
    if not group and len(parts) > 2:
        group = KEY_KO.get(parts[-2], parts[-2])
    return f"{group} — {own}" if group else own


def _item(path, label, value, source, verified, verified_on, year, external=None) -> dict:
    """external: 법령 MCP로 확인할 수 없어 외부 자료(홈택스 표 등)를 받아 넣어야 하는 항목의 안내 한 줄.
    external 항목은 '확인필요(빨강)'가 아니라 '외부 자료 반입 필요(주황)'로 따로 센다."""
    pending = verified is not True or value is None
    ext = bool(external) and pending
    needs = pending and not ext
    if ext:
        reason = "외부 자료 반입 필요" + ("(값 없음)" if value is None else "(임시값)")
    else:
        reason = "" if not needs else ("값 없음(null)" if value is None else "검증 전(verified: false)")
    return {"name": path, "label": label, "value": value if isinstance(value, str) else _fmt_value(path.rsplit(".", 1)[-1], value),
            "source": source or "", "year": year, "verified": verified is True, "verified_on": str(verified_on) if verified_on else "",
            "needs_check": needs, "external": ext, "where": str(external) if ext else "", "reason": reason}


def _schedule_text(s: dict) -> str:
    if s.get("type") == "monthly":
        return "매월 말일" if int(s["day"]) >= 31 else f"매월 {s['day']}일"
    return "매년 " + ", ".join(str(d) for d in s.get("dates", []))


def _load(folder: Path, name: str):
    p = folder / f"{name}.yaml"
    text = p.read_text(encoding="utf-8-sig")
    return yaml.safe_load(text) or {}, text


def _year_files(folder: Path, year: int) -> list[dict]:
    files = []
    for name, title in LEAF_FILES.items():
        if (folder / f"{name}.yaml").exists():
            data, text = _load(folder, name)
            comments = {f"{name}.{k}": v for k, v in _key_comments(text).items()}
            items = []
            for path, key, leaf in _walk(data, name):
                items.append(_item(path, _label(path, key, comments), leaf["value"], leaf.get("source"), leaf.get("verified"),
                                   leaf.get("verified_on"), leaf.get("year", year), leaf.get("external")))
            files.append({"file": f"{name}.yaml", "title": title, "items": items})
    if (folder / "deadlines.yaml").exists():
        d, _ = _load(folder, "deadlines")
        items = [_item(f"deadlines.{r['id']}", r["name"], f"{r['name']} — {_schedule_text(r['schedule'])}",
                       (r.get("source") or "") + (f" / 참고: {r['note']}" if r.get("note") else ""),
                       r.get("verified"), r.get("verified_on"), year, r.get("external")) for r in d.get("deadlines", [])]
        items.append(_item("deadlines.shift_source", "기한 순연 규칙", "기한이 토·일·공휴일·대체공휴일·노동절(5/1)이면 다음 영업일로 순연",
                           d.get("shift_source"), d.get("shift_verified", False), d.get("shift_verified_on"), year))
        files.append({"file": "deadlines.yaml", "title": "신고기한", "items": items})
    if (folder / "holidays.yaml").exists():
        h, _ = _load(folder, "holidays")
        files.append({"file": "holidays.yaml", "title": "공휴일(순연용)", "items": [
            _item("holidays", "공휴일 목록", f"{len(h.get('holidays', []))}일 (대체공휴일 포함) + 매년 {', '.join(e['name'] for e in h.get('annual_extra', [])) or '없음'}", h.get("source"), h.get("verified"), h.get("verified_on"), year, h.get("external"))]})
    tpl = []
    for name, title in TEMPLATE_FILES.items():
        if (folder / f"{name}.yaml").exists():
            d, _ = _load(folder, name)
            meta = d.get("meta", {})
            n = len(d.get("rules") or d.get("items") or [])
            tpl.append(_item(name, title, f"{title} · {n}개 항목", f"사무실 내부 기준 — {meta.get('kind', '사무실 규칙')}",
                             meta.get("verified"), meta.get("verified_on"), year))
    if tpl:
        files.append({"file": "(템플릿)", "title": "사무실 업무 양식·규칙 (법령 아님)", "items": tpl})
    csv = folder / "simple_tax_table.csv"
    if csv.exists():
        lines = [ln.lstrip("# ").strip() for ln in csv.read_text(encoding="utf-8-sig").splitlines() if ln.startswith("#")]
        imported = next((m.group(1) for ln in lines if (m := re.search(r"반입일:\s*(\d{4}-\d{2}-\d{2})", ln))), None)
        n_rows = sum(1 for ln in csv.read_text(encoding="utf-8-sig").splitlines() if ln[:1].isdigit())
        if imported:  # 공식 표를 반입한 경우 — 헤더에 반입일이 있다
            files.append({"file": "simple_tax_table.csv", "title": "근로소득 간이세액표", "items": [
                _item("simple_tax_table", "근로소득 간이세액표", f"홈택스 공식 표 · {n_rows}개 구간", " / ".join(lines[1:3]), True, imported, year)]})
        else:
            files.append({"file": "simple_tax_table.csv", "title": "근로소득 간이세액표", "items": [
                _item("simple_tax_table", "근로소득 간이세액표", "시연용 임의값 — 실제 간이세액표 아님(샘플 구간만)", " / ".join(lines[:3]), False, None, year,
                      "홈택스 → 근로소득 간이세액표(2026년 적용분) 엑셀을 받아 scripts/import_simple_tax_table.py 로 반입")]})
    return files


def catalog(year: int | None = None, needs_check_only: bool = False) -> dict:
    """rules/<year>/ 전체 항목. year 를 안 주면 가장 최근 연도. 없는 연도는 ValueError."""
    years = rule_years()
    if not years:
        raise ValueError("rules 폴더에 적용연도가 없습니다")
    year = year or years[-1]
    if year not in years:
        raise ValueError(f"{year}년 rules 폴더가 없습니다 (있는 연도: {', '.join(map(str, years))})")
    files = _year_files(config.RULES_DIR / str(year), year)
    all_items = [i for f in files for i in f["items"]]
    summary = {"total": len(all_items), "needs_check": sum(1 for i in all_items if i["needs_check"]),
               "external": sum(1 for i in all_items if i["external"]),
               "verified": sum(1 for i in all_items if i["verified"]), "null_values": sum(1 for i in all_items if "값 없음" in i["reason"])}
    if needs_check_only:
        files = [{**f, "items": [i for i in f["items"] if i["needs_check"]]} for f in files]
        files = [f for f in files if f["items"]]
    return {"year": year, "years": years, "generated_at": datetime.now().isoformat(timespec="seconds"), "summary": summary, "files": files}


# ---------- docs/rules_check.md ----------
def _cell(text) -> str:
    return str(text if text not in (None, "") else "-").replace("|", "\\|").replace("\n", " ")


def to_markdown(data: dict) -> str:
    """같은 내용을 사람이 읽는 표(markdown)로. 확인필요 항목은 🔴, 확인된 항목은 ✔."""
    s = data["summary"]
    out = [f"# 법령 수치 확인표 (rules/{data['year']})", "",
           f"- 생성: {data['generated_at'].replace('T', ' ')} (app/core/rules_catalog.py — `GET /api/rules` 와 같은 내용)",
           f"- 🔴 **확인필요 {s['needs_check']}개** / 전체 {s['total']}개 (확인 완료 {s['verified']}개, 값이 비어 있는 항목 {s['null_values']}개)",
           f"- 🟠 **외부 자료 반입 필요 {s['external']}개** — 법령 MCP 로는 확인할 수 없는 항목(간이세액표·신고서 코드·공휴일 등). 항목마다 어디서 받는지 안내가 있습니다.",
           "- 확인필요 = `verified: true` 가 아니거나 값이 `null` 인 항목 중 외부 자료 항목이 아닌 것입니다. 법령 MCP 로 검증한 뒤 해당 yaml 에 `verified: true` 와 `verified_on` 을 적고,",
           "  `.venv\\Scripts\\python.exe scripts\\export_rules_check.py` 로 이 문서를 다시 만드세요.",
           "- ⚠ 샘플 간이세액표·사무실 업무 양식은 법령 수치가 아니라 시연·보정용입니다.", "",
           "## 파일별 요약", "", "| 파일 | 항목 | 확인필요 |", "|---|---:|---:|"]
    for f in data["files"]:
        todo = sum(1 for i in f["items"] if i["needs_check"])
        ext = sum(1 for i in f["items"] if i["external"])
        mark = " ".join(x for x in (f"🔴 {todo}" if todo else "", f"🟠 {ext}" if ext else "") if x) or "✔ 0"
        out.append(f"| {_cell(f['title'])} (`{f['file']}`) | {len(f['items'])} | {mark} |")
    for f in data["files"]:
        out += ["", f"## {f['title']} (`{f['file']}`)", "", "| 항목명 | 값 | 근거 법령·고시 | 적용연도 | 확인일 | 확인필요 |", "|---|---|---|---:|---|---|"]
        for i in f["items"]:
            if i["external"]:
                flag = f"🟠 외부 자료 반입 필요 ({i['reason']}) — {i['where']}"
            else:
                flag = f"🔴 TODO: 확인 필요 ({i['reason']})" if i["needs_check"] else "✔ 확인됨"
            out.append(f"| {_cell(i['label'])}<br>`{i['name']}` | {_cell(i['value'])} | {_cell(i['source'])} | {i['year']} | {_cell(i['verified_on'])} | {flag} |")
    return "\n".join(out) + "\n"
