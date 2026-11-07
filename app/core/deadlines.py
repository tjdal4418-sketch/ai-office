"""신고기한 계산(매니저의 신고기한 캘린더 데이터). 기한 규칙은 app/rules/<연도>/deadlines.yaml,
공휴일은 holidays.yaml 에서 읽는다(하드코딩 금지).

기한이 토요일·일요일·공휴일(대체공휴일·노동절 포함)이면 다음 영업일로 순연한다. 순연된 날짜가 '신고기한'이고,
원래 날짜(original_date)와 순연 사유를 함께 돌려줘 화면에서 근거를 보여준다.
공휴일 파일이 없는 연도는 주말만 순연하고 holiday_known=False 로 표시한다.
"""
import calendar
from datetime import date, timedelta

import yaml

from app.core import config

WEEKDAYS = "월화수목금토일"


def _rules_year(today: date) -> int | None:
    """오늘 연도의 rules 폴더를 쓰고, 없으면 가장 최근 연도를 쓴다."""
    years = sorted(int(p.name) for p in config.RULES_DIR.iterdir() if p.is_dir() and p.name.isdigit()) \
        if config.RULES_DIR.exists() else []
    if not years:
        return None
    return today.year if today.year in years else years[-1]


def _load_file(today: date) -> dict:
    year = _rules_year(today)
    if year is None:
        return {}
    path = config.RULES_DIR / str(year) / "deadlines.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}


def _load_rules(today: date) -> list[dict]:
    return _load_file(today).get("deadlines") or []


def shift_source(today: date | None = None) -> str:
    return _load_file(today or date.today()).get("shift_source", "")


def shift_verification(today: date | None = None) -> tuple[bool, str | None]:
    """기한 순연 규칙의 (검증 여부, 확인일)."""
    data = _load_file(today or date.today())
    return bool(data.get("shift_verified", False)), data.get("shift_verified_on")


def load_holidays(year: int) -> dict[date, str]:
    """해당 연도 공휴일 {날짜: 이름}. 파일이 없으면 빈 dict."""
    path = config.RULES_DIR / str(year) / "holidays.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    out = {date.fromisoformat(str(h["date"])): h["name"] for h in data.get("holidays", [])}
    for extra in data.get("annual_extra", []):  # 해마다 같은 날(노동절 등) — 기한 순연 사유
        month, day = (int(x) for x in str(extra["month_day"]).split("-"))
        out.setdefault(date(year, month, day), extra["name"])
    return out


def holiday_known(year: int) -> bool:
    return (config.RULES_DIR / str(year) / "holidays.yaml").exists()


def shift_to_business_day(d: date) -> tuple[date, str]:
    """토·일·공휴일이면 다음 영업일로. 반환: (순연 후 날짜, 사유 — 순연 없으면 빈 문자열)."""
    reasons = []
    cur = d
    while True:
        hol = load_holidays(cur.year).get(cur)
        if cur.weekday() >= 5:
            reasons.append(f"{WEEKDAYS[cur.weekday()]}요일" + (f"·{hol}" if hol else ""))
        elif hol:
            reasons.append(f"공휴일({hol})")
        else:
            return cur, " → ".join(reasons)
        cur += timedelta(days=1)


def _raw_dates(schedule: dict, start: date, end: date) -> list[date]:
    """[start, end] 범위(순연 전 기준 ±여유)의 원래 날짜들."""
    out = []
    kind = schedule.get("type")
    if kind == "monthly":
        day = int(schedule["day"])
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            out.append(date(y, m, min(day, calendar.monthrange(y, m)[1])))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    elif kind == "yearly":
        for y in range(start.year, end.year + 1):
            for md in schedule["dates"]:
                m_text, d_text = str(md).split("-")
                month = int(m_text)
                last = calendar.monthrange(y, month)[1]
                out.append(date(y, month, last if d_text == "말일" else int(d_text)))
    return out


def _event(rule: dict, original: date, today: date) -> dict:
    due, reason = shift_to_business_day(original)
    return {
        "id": rule["id"], "name": rule["name"], "category": rule.get("category", ""),
        "date": due.isoformat(), "original_date": original.isoformat(), "shifted": due != original,
        "shift_reason": reason, "holiday_known": holiday_known(original.year),
        "d_day": (due - today).days, "source": rule.get("source", ""),
        "verified": bool(rule.get("verified", False)), "verified_on": rule.get("verified_on"),
        "home": bool(rule.get("home", False)), "note": rule.get("note", ""),
    }


def events_between(start: date, end: date, today: date | None = None) -> list[dict]:
    """순연 후 날짜가 [start, end] 안에 있는 모든 신고기한(캘린더용), 날짜순."""
    today = today or date.today()
    out = []
    for rule in _load_rules(today):
        schedule = rule.get("schedule") or {}
        try:
            raws = _raw_dates(schedule, start - timedelta(days=10), end)
        except (KeyError, ValueError):
            continue  # 잘못된 규칙은 건너뛴다
        for raw in raws:
            ev = _event(rule, raw, today)
            if start <= date.fromisoformat(ev["date"]) <= end:
                out.append(ev)
    out.sort(key=lambda x: (x["date"], x["name"]))
    return out


def upcoming(today: date | None = None, limit: int = 3, home_only: bool = True) -> list[dict]:
    """규칙마다 오늘 이후 가장 가까운 신고기한 1개씩(순연 반영), 날짜순 상위 limit 개."""
    today = today or date.today()
    items = []
    for rule in _load_rules(today):
        if home_only and not rule.get("home"):
            continue
        schedule = rule.get("schedule") or {}
        try:
            raws = _raw_dates(schedule, today - timedelta(days=10), today + timedelta(days=400))
        except (KeyError, ValueError):
            continue
        evs = [_event(rule, r, today) for r in raws]
        evs = [e for e in evs if e["d_day"] >= 0]
        if evs:
            items.append(min(evs, key=lambda e: e["date"]))
    items.sort(key=lambda x: (x["date"], x["name"]))
    return items[:limit]
