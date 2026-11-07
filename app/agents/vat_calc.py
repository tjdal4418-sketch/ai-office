"""배트(부가세) 계산 로직 — 순수 함수 모음.

흐름: 읽기·검증 → 세금계산서 vs 장부 대사(4분류) → 불공제 후보 플래그 → 신고서 요약 → 발행 관리 → 신고기한.
AI 는 '후보'를 찾고 대사 결과를 정리할 뿐, 불공제 여부·가산세 부과 여부의 최종 판단은 담당자가 한다.
금액은 원 단위 정수, 법령 수치(세율·가산세율·키워드·기한일)는 rules 에서 읽는다.
"""
import calendar
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from app.agents.bookkeeping_calc import _amount, parse_date
from app.agents.payroll_calc import _blank, issue
from app.core import deadlines
from app.core.rules import Rules

# ---------- 입력 열 이름 (홈택스 다운로드 형식과 비슷하게) ----------
INVOICE_COLUMNS = {
    "작성일자": "write_date", "작성일": "write_date", "발급일자": "issue_date", "발행일자": "issue_date",
    "전송일자": "issue_date", "승인번호": "approval_no",
    "공급자사업자등록번호": "supplier_biz", "공급자사업자번호": "supplier_biz", "공급자상호": "supplier_name",
    "공급받는자사업자등록번호": "buyer_biz", "공급받는자사업자번호": "buyer_biz", "공급받는자상호": "buyer_name",
    "사업자번호": "partner_biz", "거래처사업자번호": "partner_biz", "거래처": "partner_name", "상호": "partner_name",
    "공급가액": "supply", "세액": "tax", "합계금액": "total", "합계": "total",
    "품목명": "item", "품목": "item", "비고": "note",
}
LEDGER_COLUMNS = {"일자": "date", "작성일자": "date", "구분": "kind", "거래처": "name", "거래처명": "name",
                  "사업자번호": "biz", "사업자등록번호": "biz", "계정과목": "account", "적요": "memo",
                  "공급가액": "supply", "세액": "tax"}
CARD_COLUMNS = {"사용일자": "date", "사용일": "date", "일자": "date", "가맹점": "name", "가맹점명": "name",
                "사업자번호": "biz", "가맹점사업자번호": "biz", "공급가액": "supply", "세액": "tax",
                "합계금액": "total", "합계": "total", "적요": "memo", "업종": "memo"}

SALES, PURCHASE = "매출", "매입"
MATCH, DIFF, NO_LEDGER, NO_INVOICE = "일치", "금액차이", "장부누락", "세금계산서누락"
STATUSES = (MATCH, DIFF, NO_LEDGER, NO_INVOICE)

ROLE_KEYWORDS = [("card", ("카드", "card")), ("ledger", ("장부", "ledger")),
                 ("sales", ("매출", "sales")), ("purchase", ("매입", "purchase"))]
ROLE_LABELS = {"sales": "매출 세금계산서", "purchase": "매입 세금계산서", "ledger": "장부 매출·매입", "card": "카드매입 내역"}
REQUIRED_ROLES = ("sales", "purchase", "ledger")

ND_ORDER = ("entertainment", "vehicle", "exempt", "personal")
ND_LABELS = {"entertainment": "기업업무추진비 관련", "vehicle": "비영업용 소형승용차 관련",
             "exempt": "면세사업 관련", "personal": "사업 무관(개인 지출 의심)"}


def detect_role(filename: str) -> str | None:
    low = filename.lower()
    for role, keys in ROLE_KEYWORDS:
        if any(k in low for k in keys):
            return role
    return None


# ======================================================================
# 1) 입력 검증
# ======================================================================
def norm_biz(v) -> str | None:
    """사업자등록번호 → 숫자 10자리(없거나 형식 오류면 None)."""
    s = re.sub(r"\D", "", str(v if v is not None else ""))
    return s if len(s) == 10 else None


def fmt_biz(s: str) -> str:
    return f"{s[:3]}-{s[3:5]}-{s[5:]}" if s and len(s) == 10 else (s or "")


@dataclass
class Invoice:
    row: int
    kind: str  # 매출 / 매입
    write_date: date
    issue_date: date | None
    approval_no: str
    biz: str   # 거래 상대방 사업자번호(숫자 10자리). 매출=공급받는자, 매입=공급자
    name: str
    supply: int
    tax: int
    total: int
    item: str
    note: str


@dataclass
class LedgerEntry:
    row: int
    date: date
    kind: str
    name: str
    biz: str
    account: str
    memo: str
    supply: int
    tax: int


@dataclass
class CardPurchase:
    row: int
    date: date
    name: str
    biz: str  # 없으면 ""
    supply: int
    tax: int
    total: int
    memo: str


def _date_cell(v, label: str, ref: str, issues: list, required=True):
    if _blank(v):
        if required:
            issues.append(issue("error", "필수값 누락", f"{label} 누락", ref))
        return None
    d = parse_date(v)
    if d is None:
        issues.append(issue("error", "형식 오류", f"{label} 형식 오류 '{v}' (예: 2026-07-05)", ref))
    return d


def _biz_cell(v, label: str, ref: str, issues: list, required=True) -> str:
    if _blank(v):
        if required:
            issues.append(issue("error", "필수값 누락", f"{label} 누락", ref))
        return ""
    b = norm_biz(v)
    if b is None:
        issues.append(issue("error", "사업자번호", f"{label} 형식 오류 '{v}' (숫자 10자리, 예: 123-45-67890)", ref))
        return ""
    return b


def validate_invoices(rows, missing, kind: str, rate) -> tuple[list[Invoice], list[dict]]:
    """홈택스형 세금계산서 목록 검증. kind: 매출/매입. rate: 세율(Decimal/float, 세액 점검용)."""
    sheet = f"{kind} 세금계산서"
    issues: list[dict] = []
    partner = "buyer" if kind == SALES else "supplier"
    biz_field = f"{partner}_biz" if f"{partner}_biz" not in missing else "partner_biz"
    name_field = f"{partner}_name" if f"{partner}_name" not in missing else "partner_name"
    need = [("write_date", "작성일자"), (biz_field, "사업자등록번호"), ("supply", "공급가액"), ("tax", "세액")]
    for f, label in need:
        if f in missing:
            issues.append(issue("error", "필수값 누락", f"{sheet}에 필수 열이 없습니다: {label}", sheet))
    if issues:
        return [], issues
    out: list[Invoice] = []
    seen: dict[str, int] = {}
    for n, r in rows:
        ref = f"{sheet} 행 {n}"
        before = len(issues)
        wd = _date_cell(r.get("write_date"), "작성일자", ref, issues)
        idate = _date_cell(r.get("issue_date"), "발급일자", ref, issues, required=False)
        biz = _biz_cell(r.get(biz_field), "사업자등록번호", ref, issues)
        supply = _amount(r.get("supply"), "공급가액", ref, issues, required=True)
        tax = _amount(r.get("tax"), "세액", ref, issues)
        total = _amount(r.get("total"), "합계금액", ref, issues)
        appr = str(r.get("approval_no") or "").strip()
        if appr:
            if appr in seen:
                issues.append(issue("error", "중복 승인번호", f"승인번호 {appr} 이(가) 행 {seen[appr]} 과(와) 중복", ref))
            else:
                seen[appr] = n
        if len(issues) == before and supply is not None and tax is not None:
            if total and total != supply + tax:
                issues.append(issue("error", "합계 불일치", f"합계금액 {total:,}원 ≠ 공급가액 {supply:,}원 + 세액 {tax:,}원", ref))
            else:
                expected = int(supply * rate)  # 원 미만 절사(부호 유지)
                if supply < 0 or tax < 0:
                    issues.append(issue("info", "수정세금계산서", "음수 금액(수정세금계산서) — 대사 시 원 거래와 함께 확인", ref))
                elif tax == 0 and supply != 0:
                    issues.append(issue("info", "세액 0원", "세액이 0원입니다 — 영세율·면세 여부 확인", ref))
                elif tax != expected:
                    issues.append(issue("warning", "세액 점검", f"세액 {tax:,}원 ≠ 공급가액×세율 {expected:,}원 — 세율·단수처리 확인", ref))
                out.append(Invoice(n, kind, wd, idate, appr, biz, str(r.get(name_field) or "").strip(), supply, tax,
                                   total or supply + tax, str(r.get("item") or "").strip(), str(r.get("note") or "").strip()))
    if not rows:
        issues.append(issue("error", "필수값 누락", f"{sheet}에 데이터가 없습니다", sheet))
    return out, issues


def validate_ledger(rows, missing) -> tuple[list[LedgerEntry], list[dict]]:
    sheet = "장부 매출·매입"
    issues: list[dict] = []
    for f, label in (("date", "일자"), ("kind", "구분"), ("biz", "사업자번호"), ("supply", "공급가액")):
        if f in missing:
            issues.append(issue("error", "필수값 누락", f"{sheet}에 필수 열이 없습니다: {label}", sheet))
    if issues:
        return [], issues
    out: list[LedgerEntry] = []
    for n, r in rows:
        ref = f"{sheet} 행 {n}"
        before = len(issues)
        d = _date_cell(r.get("date"), "일자", ref, issues)
        kind = str(r.get("kind") or "").strip()
        if kind not in (SALES, PURCHASE):
            issues.append(issue("error", "형식 오류", f"구분은 '매출' 또는 '매입'이어야 합니다 ('{kind}')", ref))
        biz = _biz_cell(r.get("biz"), "사업자번호", ref, issues)
        supply = _amount(r.get("supply"), "공급가액", ref, issues, required=True)
        tax = _amount(r.get("tax"), "세액", ref, issues)
        if len(issues) == before:
            out.append(LedgerEntry(n, d, kind, str(r.get("name") or "").strip(), biz, str(r.get("account") or "").strip(),
                                   str(r.get("memo") or "").strip(), supply, tax or 0))
    if not rows:
        issues.append(issue("error", "필수값 누락", f"{sheet}에 데이터가 없습니다", sheet))
    return out, issues


def validate_cards(rows, missing) -> tuple[list[CardPurchase], list[dict]]:
    sheet = "카드매입 내역"
    issues: list[dict] = []
    for f, label in (("date", "사용일자"), ("name", "가맹점"), ("supply", "공급가액"), ("tax", "세액")):
        if f in missing:
            issues.append(issue("error", "필수값 누락", f"{sheet}에 필수 열이 없습니다: {label}", sheet))
    if issues:
        return [], issues
    out: list[CardPurchase] = []
    for n, r in rows:
        ref = f"{sheet} 행 {n}"
        before = len(issues)
        d = _date_cell(r.get("date"), "사용일자", ref, issues)
        if _blank(r.get("name")):
            issues.append(issue("error", "필수값 누락", "가맹점 누락", ref))
        biz = _biz_cell(r.get("biz"), "가맹점 사업자번호", ref, issues, required=False)
        supply = _amount(r.get("supply"), "공급가액", ref, issues, required=True)
        tax = _amount(r.get("tax"), "세액", ref, issues)
        total = _amount(r.get("total"), "합계금액", ref, issues)
        if len(issues) == before:
            if total and total != supply + (tax or 0):
                issues.append(issue("error", "합계 불일치", f"합계금액 {total:,}원 ≠ 공급가액+세액 {supply + (tax or 0):,}원", ref))
            else:
                out.append(CardPurchase(n, d, str(r["name"]).strip(), biz, supply, tax or 0, total or supply + (tax or 0),
                                        str(r.get("memo") or "").strip()))
    return out, issues


# ======================================================================
# 2) 기간·신고기한
# ======================================================================
def detect_period(dates: list[date]) -> tuple[int, int]:
    """가장 늦은 거래일의 분기 → (연도, 분기 1~4)."""
    d = max(dates)
    return d.year, (d.month - 1) // 3 + 1


def period_label(period: tuple[int, int]) -> str:
    y, q = period
    return {1: f"{y}년 1기 예정신고 (1~3월)", 2: f"{y}년 1기 확정신고 (4~6월)",
            3: f"{y}년 2기 예정신고 (7~9월)", 4: f"{y}년 2기 확정신고 (10~12월)"}[q]


def period_bounds(period: tuple[int, int]) -> tuple[date, date]:
    y, q = period
    start = date(y, 3 * q - 2, 1)
    end = date(y, 3 * q, {3: 31, 6: 30, 9: 30, 12: 31}[3 * q])
    return start, end


def period_deadline(rules_dir: Path, period: tuple[int, int], today: date) -> dict | None:
    """분기에 해당하는 신고기한(deadlines.yaml: 예정 vat_prelim / 확정 vat_final). 토·일·공휴일 순연 반영(core.deadlines)."""
    y, q = period
    data = yaml.safe_load((Path(rules_dir) / "deadlines.yaml").read_text(encoding="utf-8-sig")) or {}
    by_id = {d["id"]: d for d in data.get("deadlines", [])}
    # (규칙 id, 그 규칙의 날짜 목록에서 몇 번째, 연도 보정)
    pick = {1: ("vat_prelim", 0, 0), 2: ("vat_final", 1, 0), 3: ("vat_prelim", 1, 0), 4: ("vat_final", 0, 1)}[q]
    rule = by_id.get(pick[0])
    if not rule:
        return None
    month, day = (int(x) for x in rule["schedule"]["dates"][pick[1]].split("-"))
    original = date(y + pick[2], month, day)
    due, reason = deadlines.shift_to_business_day(original)  # 매니저의 순연 로직(토·일·공휴일)을 그대로 쓴다
    return {"id": pick[0], "name": rule["name"], "date": due, "original_date": original, "shift_reason": reason,
            "d_day": (due - today).days, "verified": bool(rule.get("verified")), "source": rule.get("source", "")}


# ======================================================================
# 3) 세금계산서 vs 장부 대사 — 사업자번호 + 공급가액 + 작성일
# ======================================================================
def reconcile(invoices: list[Invoice], ledger: list[LedgerEntry], kind: str, tolerance: int = 0) -> list[dict]:
    """한 방향(매출 또는 매입) 대사. 반환 레코드: {kind, status, invoice, ledger, note}.
    1차) 사업자번호+공급가액+작성일 같음 → 일치(세액이 다르면 금액차이)
    2차) 사업자번호+작성일 같고 공급가액만 다름 → 금액차이
    남은 세금계산서 → 장부누락 / 남은 장부 → 세금계산서누락"""
    inv = [i for i in invoices if i.kind == kind]
    led = [l for l in ledger if l.kind == kind]
    used: set[int] = set()
    results: list[dict] = []
    leftover: list[Invoice] = []

    def take(pred):
        for k, l in enumerate(led):
            if k not in used and pred(l):
                used.add(k)
                return l
        return None

    second: list[Invoice] = []
    for i in inv:
        l = take(lambda l: l.biz == i.biz and l.date == i.write_date and abs(l.supply - i.supply) <= tolerance)
        if l is None:
            second.append(i)
        elif l.tax != i.tax:
            results.append({"kind": kind, "status": DIFF, "invoice": i, "ledger": l,
                            "note": f"세액 차이 (세금계산서 {i.tax:,}원 / 장부 {l.tax:,}원)"})
        else:
            results.append({"kind": kind, "status": MATCH, "invoice": i, "ledger": l, "note": ""})
    for i in second:
        l = take(lambda l: l.biz == i.biz and l.date == i.write_date)
        if l is None:
            leftover.append(i)
        else:
            results.append({"kind": kind, "status": DIFF, "invoice": i, "ledger": l,
                            "note": f"공급가액 차이 {i.supply - l.supply:+,}원 (세금계산서 {i.supply:,}원 / 장부 {l.supply:,}원)"})
    for i in leftover:
        hint = next((l for k, l in enumerate(led) if k not in used and l.biz == i.biz and l.supply == i.supply), None)
        note = "장부에 같은 거래가 없음 — 매출/매입 미계상 여부 확인"
        if hint:
            note += f" (참고: 같은 사업자·금액의 장부 건이 {hint.date} 로 날짜만 다름)"
        results.append({"kind": kind, "status": NO_LEDGER, "invoice": i, "ledger": None, "note": note})
    for k, l in enumerate(led):
        if k in used:
            continue
        hint = next((i for i in inv if i in leftover and i.biz == l.biz and i.supply == l.supply), None)
        base = ("세금계산서 미발행 의심 — 발행 대상 여부(카드·현금영수증·면세 등) 확인" if kind == SALES
                else "세금계산서 미수취 — 매입세액 공제 불가, 수취 여부 확인")
        results.append({"kind": kind, "status": NO_INVOICE, "invoice": None, "ledger": l,
                        "note": base + (f" (참고: 같은 사업자·금액의 세금계산서가 {hint.write_date} 로 날짜만 다름)" if hint else "")})
    order = {s: n for n, s in enumerate(STATUSES)}
    results.sort(key=lambda r: (order[r["status"]], rec_date(r)))
    return results


def rec_date(r: dict) -> date:
    return r["invoice"].write_date if r["invoice"] else r["ledger"].date


# ======================================================================
# 4) 불공제 매입세액 후보
# ======================================================================
def find_keywords(text: str, rules: Rules) -> list[tuple[str, str]]:
    """text 안에서 걸린 (유형, 키워드). 유형은 ND_ORDER 순서."""
    up = text.upper()
    hits = []
    for cat in ND_ORDER:
        for kw in rules.v(f"vat.non_deductible.{cat}"):
            if kw.upper() in up:
                hits.append((cat, kw))
    return hits


def _candidate(source, d, name, biz, item, account, supply, tax, hits, ref) -> dict:
    cats = list(dict.fromkeys(c for c, _ in hits))
    return {"source": source, "date": d, "name": name, "biz": biz, "item": item, "account": account,
            "supply": supply, "tax": tax, "types": [ND_LABELS[c] for c in cats],
            "keywords": list(dict.fromkeys(k for _, k in hits)), "ref": ref}


def find_candidates(purchase_results: list[dict], cards: list[CardPurchase], rules: Rules) -> list[dict]:
    """매입 세금계산서(장부 계정과목·적요를 함께 봄)와 카드매입에서 불공제 후보를 찾는다.
    세금계산서가 없는 장부 매입은 애초에 공제가 안 되므로 후보에 넣지 않는다(대사 결과에서 안내)."""
    out = []
    for r in purchase_results:
        i, l = r["invoice"], r["ledger"]
        if i is None or i.tax == 0:
            continue
        text = " ".join([i.item, i.name, i.note] + ([l.account, l.memo, l.name] if l else []))
        hits = find_keywords(text, rules)
        if hits:
            out.append(_candidate("세금계산서", i.write_date, i.name, fmt_biz(i.biz), i.item or (l.memo if l else ""),
                                  l.account if l else "", i.supply, i.tax, hits, f"매입 세금계산서 행 {i.row}"))
    for c in cards:
        if not c.biz or c.tax <= 0:
            continue
        hits = find_keywords(f"{c.name} {c.memo}", rules)
        if hits:
            out.append(_candidate("카드", c.date, c.name, fmt_biz(c.biz), c.memo, "", c.supply, c.tax, hits, f"카드매입 행 {c.row}"))
    out.sort(key=lambda x: (x["source"], x["date"]))
    return out


def card_eligible(c: CardPurchase) -> bool:
    """카드매입 세액을 공제 대상으로 볼 수 있는 최소 요건: 가맹점 사업자번호가 있고 세액이 있음."""
    return bool(c.biz) and c.tax > 0


# ======================================================================
# 5) 신고서 요약
# ======================================================================
def summarize(results: list[dict], candidates: list[dict], cards: list[CardPurchase]) -> dict:
    """후보는 사람이 '공제'로 바꾸기 전까지 불공제로 본다(보수적). alt_payable 은 후보를 모두 공제로 본 경우."""
    inv_s = [r["invoice"] for r in results if r["kind"] == SALES and r["invoice"]]
    inv_p = [r["invoice"] for r in results if r["kind"] == PURCHASE and r["invoice"]]
    led_s = [r["ledger"] for r in results if r["kind"] == SALES and r["ledger"]]
    led_p = [r["ledger"] for r in results if r["kind"] == PURCHASE and r["ledger"]]
    s = {
        "sales_supply": sum(i.supply for i in inv_s), "sales_tax": sum(i.tax for i in inv_s),
        "ledger_sales_supply": sum(l.supply for l in led_s), "ledger_sales_tax": sum(l.tax for l in led_s),
        "purchase_supply": sum(i.supply for i in inv_p), "purchase_tax": sum(i.tax for i in inv_p),
        "ledger_purchase_supply": sum(l.supply for l in led_p), "ledger_purchase_tax": sum(l.tax for l in led_p),
        "nd_invoice_tax": sum(c["tax"] for c in candidates if c["source"] == "세금계산서"),
        "card_tax": sum(c.tax for c in cards if card_eligible(c)),
        "card_nd_tax": sum(c["tax"] for c in candidates if c["source"] == "카드"),
    }
    s["deductible_invoice_tax"] = s["purchase_tax"] - s["nd_invoice_tax"]
    s["card_deductible"] = s["card_tax"] - s["card_nd_tax"]
    s["payable"] = s["sales_tax"] - s["deductible_invoice_tax"] - s["card_deductible"]
    s["alt_payable"] = s["payable"] - s["nd_invoice_tax"] - s["card_nd_tax"]
    return s


# ======================================================================
# 6) 세금계산서 발행 관리 (미발행·지연발행 의심, 가산세 위험)
# ======================================================================
def next_month_day(d: date, day: int) -> date:
    """작성일이 속한 달의 다음 달 day 일 (그 달에 없는 날이면 말일). 주말·공휴일 순연은 미반영."""
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def penalty_amount(supply: int, rate) -> int:
    """공급가액 × 가산세율, 원 단위 반올림(엑셀 ROUND 와 같게)."""
    return int((Decimal(supply) * Decimal(str(rate))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def issuance_review(sales_results: list[dict], rules: Rules, today: date) -> list[dict]:
    """매출 건 중 지연발행·미발행 의심. 발급기한 = 공급시기(작성일) 다음 달 N일.
    가산세 위험액은 엑셀에서 공급가액×율 수식으로 계산하고, 여기서는 같은 값을 확인용으로 함께 담는다."""
    day = int(rules.v("vat.invoice.issue_deadline_day"))
    late_rate, unissued_rate = rules.d("vat.penalty.late_issue_rate"), rules.d("vat.penalty.unissued_rate")
    out = []
    for r in sales_results:
        if r["kind"] != SALES:
            continue
        i, l = r["invoice"], r["ledger"]
        if i and i.issue_date:
            due = next_month_day(i.write_date, day)
            if i.issue_date > due:
                out.append({"type": "지연발행 의심", "date": i.write_date, "name": i.name, "biz": fmt_biz(i.biz),
                            "supply": i.supply, "tax": i.tax, "due": due, "issue_date": i.issue_date,
                            "days": (i.issue_date - due).days, "rate": late_rate, "penalty": penalty_amount(i.supply, late_rate),
                            "note": f"발급일 {i.issue_date} — 기한({due}) {(i.issue_date - due).days}일 경과", "ref": f"매출 세금계산서 행 {i.row}"})
        elif i is None and l is not None and r["status"] == NO_INVOICE:
            due = next_month_day(l.date, day)
            late = today > due
            out.append({"type": "미발행 의심", "date": l.date, "name": l.name, "biz": fmt_biz(l.biz),
                        "supply": l.supply, "tax": l.tax, "due": due, "issue_date": None,
                        "days": (today - due).days, "rate": unissued_rate,
                        "penalty": penalty_amount(l.supply, unissued_rate) if late else 0,
                        "note": (f"세금계산서 미발행 의심 — 발급기한({due}) 경과 {(today - due).days}일, 미발급 가산세 위험 (발행 대상 여부·카드/현금영수증/면세 확인)" if late
                                 else f"세금계산서 미발행 의심 — 발급기한({due})까지 D-{(due - today).days}, 발행 대상이면 기한 내 발급"),
                        "ref": f"장부 행 {l.row}"})
    out.sort(key=lambda x: (x["type"], x["date"]))
    return out
