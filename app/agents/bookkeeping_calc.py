"""북키(기장·결산) 계산 로직 — 순수 함수 모음.

흐름: CSV/엑셀 읽기 → 입력 검증 → 분개 추천(키워드 규칙) → 분개장 → 총계정원장 → 합계잔액시산표(차대 검증)
      → 은행 Reconciliation → BS 계정 검토 → 결산 체크리스트·PBC 항목 생성.
AI는 '추천'까지만 한다: 신뢰도 낮음은 '확인필요'로 표시하고 확정은 담당자가 한다.
금액은 원 단위 정수. 상태 변경(set_status)은 에이전트(bookkeeping.py)가 한다.
"""
import calendar
import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml
from openpyxl import load_workbook

from app.agents.payroll_calc import _blank, _norm_header, _to_number, issue

# ---------- 입력 열 이름 (한글 머리글 → 필드) ----------
BANK_COLUMNS = {"거래일자": "date", "거래일": "date", "일자": "date", "적요": "memo", "내용": "memo",
                "거래처": "party", "거래처명": "party", "입금": "inflow", "입금액": "inflow",
                "출금": "outflow", "출금액": "outflow", "잔액": "balance"}
CARD_COLUMNS = {"사용일자": "date", "사용일": "date", "일자": "date", "가맹점": "party", "가맹점명": "party",
                "거래처": "party", "적요": "memo", "용도": "memo", "금액": "amount", "사용금액": "amount",
                "결제일": "settle_date", "결제예정일": "settle_date"}
CHART_COLUMNS = {"계정코드": "code", "코드": "code", "계정과목": "name", "계정과목명": "name",
                 "구분": "kind", "정상잔액": "normal"}
OPENING_COLUMNS = {"계정코드": "code", "코드": "code", "차변": "debit", "대변": "credit", "발생일": "origin_date"}
LAG_COLUMNS = {**BANK_COLUMNS, "구분": "kind"}  # 시차항목: 은행 거래와 같은 열 + 구분(잔액은 없어도 됨)

KINDS = ("자산", "부채", "자본", "수익", "비용")
BS_KINDS = ("자산", "부채", "자본")
DEBIT_KINDS = ("자산", "비용")  # 정상잔액이 차변인 구분
SRC_BANK, SRC_LAG, SRC_CARD = "은행", "시차(장부만 반영)", "법인카드"
STATUS_OK, STATUS_CHECK = "추천", "확인필요"

# 입력 파일 역할 판별용 키워드 (파일 이름에 포함) — 앞에서부터 먼저 맞는 역할
ROLE_KEYWORDS = [("lag", ("시차", "lag", "timing")), ("opening", ("전기", "opening")),
                 ("card", ("카드", "card")), ("chart", ("계정", "chart", "account")), ("bank", ("은행", "bank"))]
ROLE_LABELS = {"bank": "은행 거래내역", "card": "법인카드 사용내역", "chart": "계정과목표",
               "opening": "전기말 잔액", "lag": "시차 항목"}
REQUIRED_ROLES = ("bank", "chart", "opening")


# ======================================================================
# 1) 파일 읽기
# ======================================================================
def detect_role(filename: str) -> str | None:
    low = filename.lower()
    for role, keys in ROLE_KEYWORDS:
        if any(k in low for k in keys):
            return role
    return None


def read_rows(path: Path, columns: dict) -> tuple[list[tuple[int, dict]], list[str]]:
    """CSV(utf-8/cp949) 또는 xlsx(첫 시트)를 [(행번호, {필드: 값})], 누락된 필드 목록으로."""
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        wb = load_workbook(path, data_only=True)
        table = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    else:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp949")
        table = [r for r in csv.reader(text.splitlines())]
    if not table:
        return [], list(dict.fromkeys(columns.values()))
    index = {}
    for i, h in enumerate(table[0]):
        f = columns.get(_norm_header(h))
        if f and f not in index:
            index[f] = i
    missing = [f for f in dict.fromkeys(columns.values()) if f not in index]
    rows = []
    for n, row in enumerate(table[1:], start=2):
        if all(c is None or str(c).strip() == "" for c in row):
            continue
        rows.append((n, {f: (row[i] if i < len(row) else None) for f, i in index.items()}))
    return rows, missing


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        s = value.strip().replace(".", "-").replace("/", "-")
        if re.fullmatch(r"\d{8}", s):
            s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


# ======================================================================
# 2) 입력 검증
# ======================================================================
@dataclass
class BankTx:
    row: int
    date: date
    memo: str
    party: str
    inflow: int
    outflow: int
    balance: int | None
    source: str = SRC_BANK
    kind: str = ""  # 시차 항목의 구분(미결제수표 등)


@dataclass
class CardTx:
    row: int
    date: date
    party: str
    memo: str
    amount: int
    settle_date: date | None


@dataclass
class Account:
    code: str
    name: str
    kind: str
    normal: str  # 차변 / 대변



@dataclass
class Opening:
    row: int
    code: str
    debit: int
    credit: int
    origin_date: date | None


def _code(v) -> str:
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return str(v).strip()


def _amount(v, label: str, ref: str, issues: list, required=False):
    """금액 셀 → int. 빈칸은 0(required 면 오류). 변환 불가/음수/소수는 오류(None)."""
    if _blank(v):
        if required:
            issues.append(issue("error", "필수값 누락", f"{label} 누락", ref))
            return None
        return 0
    num = _to_number(v)
    if num is None:
        issues.append(issue("error", "숫자 형식", f"{label} 숫자 형식 오류 '{v}'", ref))
        return None
    if num != num.to_integral_value():
        issues.append(issue("error", "숫자 형식", f"{label} 정수(원 단위)여야 합니다 ({num})", ref))
        return None
    return int(num)


def validate_chart(rows, missing, required_names: list[str]) -> tuple[list[Account], list[dict]]:
    issues: list[dict] = []
    sheet = "계정과목표"
    for f, label in (("code", "계정코드"), ("name", "계정과목"), ("kind", "구분")):
        if f in missing:
            issues.append(issue("error", "필수값 누락", f"{sheet}에 필수 열이 없습니다: {label}", sheet))
    if issues:
        return [], issues
    accounts, codes, names = [], {}, {}
    for n, r in rows:
        ref = f"{sheet} 행 {n}"
        if any(_blank(r.get(f)) for f in ("code", "name", "kind")):
            issues.append(issue("error", "필수값 누락", "계정코드·계정과목·구분 중 빈 칸이 있습니다", ref))
            continue
        code, name, kind = _code(r["code"]), str(r["name"]).strip(), str(r["kind"]).strip()
        if kind not in KINDS:
            issues.append(issue("error", "형식 오류", f"구분은 {'/'.join(KINDS)} 중 하나여야 합니다 ('{kind}')", ref))
            continue
        normal = str(r.get("normal") or "").strip() or ("차변" if kind in DEBIT_KINDS else "대변")
        if normal not in ("차변", "대변"):
            issues.append(issue("error", "형식 오류", f"정상잔액은 차변/대변이어야 합니다 ('{normal}')", ref))
            continue
        if code in codes:
            issues.append(issue("error", "중복 계정", f"계정코드 {code} 중복 (행 {codes[code]})", ref))
            continue
        if name in names:
            issues.append(issue("error", "중복 계정", f"계정과목 '{name}' 중복 (행 {names[name]})", ref))
            continue
        codes[code], names[name] = n, n
        accounts.append(Account(code, name, kind, normal))
    for name in required_names:
        if name not in names:
            issues.append(issue("error", "필수 계정 없음", f"계정과목표에 '{name}' 계정이 있어야 합니다", sheet))
    return accounts, issues


def validate_opening(rows, missing, accounts: list[Account]) -> tuple[list[Opening], list[dict]]:
    """전기말 잔액: 계정 존재 확인 + 차변·대변 합계 일치(차대 불일치는 오류)."""
    issues: list[dict] = []
    sheet = "전기말 잔액"
    for f, label in (("code", "계정코드"), ("debit", "차변"), ("credit", "대변")):
        if f in missing:
            issues.append(issue("error", "필수값 누락", f"{sheet}에 필수 열이 없습니다: {label}", sheet))
    if issues:
        return [], issues
    known = {a.code for a in accounts}
    out: list[Opening] = []
    for n, r in rows:
        ref = f"{sheet} 행 {n}"
        before = len(issues)
        if _blank(r.get("code")):
            issues.append(issue("error", "필수값 누락", "계정코드 누락", ref))
            continue
        code = _code(r["code"])
        if code not in known:
            issues.append(issue("error", "계정 없음", f"계정코드 {code} 이(가) 계정과목표에 없습니다", ref))
        dr = _amount(r.get("debit"), "차변", ref, issues)
        cr = _amount(r.get("credit"), "대변", ref, issues)
        if dr and cr:
            issues.append(issue("error", "형식 오류", "한 계정에 차변과 대변을 동시에 입력할 수 없습니다(순잔액으로 입력)", ref))
        if (dr or 0) < 0 or (cr or 0) < 0:
            issues.append(issue("error", "숫자 형식", "음수 불가 — 반대편 열에 입력하세요", ref))
        origin = None
        if not _blank(r.get("origin_date")):
            origin = parse_date(r["origin_date"])
            if origin is None:
                issues.append(issue("error", "형식 오류", f"발생일 형식 오류 '{r['origin_date']}'", ref))
        if len(issues) == before:
            out.append(Opening(n, code, dr or 0, cr or 0, origin))
    if not issues:
        tdr, tcr = sum(o.debit for o in out), sum(o.credit for o in out)
        if tdr != tcr:
            issues.append(issue("error", "차대 불일치",
                                f"전기말 잔액의 차변 합계 {tdr:,}원 ≠ 대변 합계 {tcr:,}원 (차이 {tdr - tcr:+,}원)", sheet))
    return out, issues


def validate_bank(rows, missing, source=SRC_BANK, need_balance=True) -> tuple[list[BankTx], list[dict]]:
    issues: list[dict] = []
    sheet = "은행 거래내역" if source == SRC_BANK else "시차 항목"
    need = [("date", "거래일자"), ("inflow", "입금"), ("outflow", "출금")] + ([("balance", "잔액")] if need_balance else [])
    for f, label in need:
        if f in missing:
            issues.append(issue("error", "필수값 누락", f"{sheet}에 필수 열이 없습니다: {label}", sheet))
    if issues:
        return [], issues
    out: list[BankTx] = []
    for n, r in rows:
        ref = f"{sheet} 행 {n}"
        before = len(issues)
        d = parse_date(r.get("date")) if not _blank(r.get("date")) else None
        if d is None:
            issues.append(issue("error", "형식 오류" if not _blank(r.get("date")) else "필수값 누락",
                                f"거래일자 {'형식 오류 ' + repr(r.get('date')) if not _blank(r.get('date')) else '누락'}", ref))
        inflow = _amount(r.get("inflow"), "입금", ref, issues)
        outflow = _amount(r.get("outflow"), "출금", ref, issues)
        balance = _amount(r.get("balance"), "잔액", ref, issues, required=need_balance)
        if (inflow or 0) < 0 or (outflow or 0) < 0:
            issues.append(issue("error", "숫자 형식", "입금·출금은 음수일 수 없습니다", ref))
        if inflow and outflow:
            issues.append(issue("error", "형식 오류", "한 행에 입금과 출금이 동시에 있습니다", ref))
        if not inflow and not outflow and len(issues) == before:
            issues.append(issue("error", "형식 오류", "입금·출금이 모두 0입니다", ref))
        if len(issues) == before:
            out.append(BankTx(n, d, str(r.get("memo") or "").strip(), str(r.get("party") or "").strip(),
                              inflow or 0, outflow or 0, balance, source, str(r.get("kind") or "").strip()))
    if not rows:
        issues.append(issue("error", "필수값 누락", f"{sheet}에 거래가 없습니다", sheet))
    return out, issues


def validate_card(rows, missing) -> tuple[list[CardTx], list[dict]]:
    issues: list[dict] = []
    sheet = "법인카드 사용내역"
    for f, label in (("date", "사용일자"), ("party", "가맹점"), ("amount", "금액")):
        if f in missing:
            issues.append(issue("error", "필수값 누락", f"{sheet}에 필수 열이 없습니다: {label}", sheet))
    if issues:
        return [], issues
    out: list[CardTx] = []
    for n, r in rows:
        ref = f"{sheet} 행 {n}"
        before = len(issues)
        d = parse_date(r.get("date")) if not _blank(r.get("date")) else None
        if d is None:
            issues.append(issue("error", "형식 오류" if not _blank(r.get("date")) else "필수값 누락",
                                f"사용일자 {'형식 오류 ' + repr(r.get('date')) if not _blank(r.get('date')) else '누락'}", ref))
        if _blank(r.get("party")):
            issues.append(issue("error", "필수값 누락", "가맹점 누락", ref))
        amount = _amount(r.get("amount"), "금액", ref, issues, required=True)
        if amount is not None and amount <= 0:
            issues.append(issue("error", "숫자 형식", "금액은 0보다 커야 합니다 (승인 취소 건은 별도 처리 — TODO)", ref))
        settle = None
        if not _blank(r.get("settle_date")):
            settle = parse_date(r["settle_date"])
            if settle is None:
                issues.append(issue("error", "형식 오류", f"결제일 형식 오류 '{r['settle_date']}'", ref))
        if len(issues) == before:
            out.append(CardTx(n, d, str(r["party"]).strip(), str(r.get("memo") or "").strip(), amount, settle))
    return out, issues


def detect_period(txs: list) -> tuple[tuple[int, int], list[dict]]:
    """거래일자의 가장 늦은 달을 귀속월로 보고, 다른 달 거래는 주의로 알린다."""
    months = sorted({(t.date.year, t.date.month) for t in txs})
    period = months[-1]
    notes = []
    for t in txs:
        if (t.date.year, t.date.month) != period:
            notes.append(issue("warning", "기간 외 거래", f"{t.date} 거래는 귀속월({period[0]}-{period[1]:02d}) 밖입니다 — 그대로 분개에 포함됨",
                               f"{t.source if hasattr(t, 'source') else '카드'} 행 {t.row}"))
    return period, notes


def period_end(period: tuple[int, int]) -> date:
    return date(period[0], period[1], calendar.monthrange(*period)[1])


# ======================================================================
# 3) 분개 추천 (키워드 규칙)
# ======================================================================
def load_mapping(rules_dir: Path) -> list[dict]:
    path = Path(rules_dir) / "account_mapping.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    return data.get("rules", [])


def recommend(memo: str, party: str, direction: str, mapping: list[dict], chart_names: set[str],
              fallback_out: str, fallback_in: str) -> dict:
    """적요·거래처 글자에서 키워드를 찾아 계정과목을 추천. direction: out / in."""
    text = f"{memo} {party}".upper()
    for rule in mapping:
        if rule["direction"] not in (direction, "any"):
            continue
        hit = next((k for k in rule["keywords"] if k.upper() in text), None)
        if hit is None:
            continue
        if rule["account"] not in chart_names:  # 계정과목표에 없는 계정 → 확인필요로 두고 가지급금/가수금 사용
            fb = fallback_out if direction == "out" else fallback_in
            return {"account": fb, "confidence": "낮음", "rule_id": rule["id"],
                    "reason": f"키워드 '{hit}' → '{rule['account']}' 이(가) 계정과목표에 없어 {fb}(임시) — 확인 필요"}
        conf = rule["confidence"]
        return {"account": rule["account"], "confidence": "높음" if conf == "high" else "낮음",
                "rule_id": rule["id"], "reason": f"키워드 '{hit}' → {rule['account']}"}
    fb = fallback_out if direction == "out" else fallback_in
    return {"account": fb, "confidence": "낮음", "rule_id": "",
            "reason": f"맞는 키워드 규칙 없음 → {fb}(임시) — 담당자 확인 필요"}


def make_entries(txs: list, accounts: dict[str, Account], mapping: list[dict], bank_name: str,
                 card_payable: str, fb_out: str, fb_in: str) -> list[dict]:
    """거래 → 전표(차변 계정/대변 계정). 정렬·번호 매기기는 finalize_entries. accounts: 계정과목명 → Account."""
    names = set(accounts)
    entries = []
    for t in txs:
        if isinstance(t, CardTx):
            rec = recommend(t.memo, t.party, "out", mapping, names, fb_out, fb_in)
            amount, direction, source = t.amount, "out", SRC_CARD
            contra, memo = card_payable, t.memo or t.party
        else:
            direction = "in" if t.inflow else "out"
            rec = recommend(t.memo, t.party, direction, mapping, names, fb_out, fb_in)
            amount, source = (t.inflow or t.outflow), t.source
            contra, memo = bank_name, t.memo or t.party
        sug = rec["account"]
        # 출금·카드사용: 차) 추천계정 / 대) 보통예금(카드는 미지급금). 입금: 차) 보통예금 / 대) 추천계정
        dr_acc, cr_acc = (sug, contra) if direction == "out" else (contra, sug)
        entries.append({
            "date": t.date, "source": source, "ref_row": t.row, "memo": memo, "party": t.party,
            "amount": amount, "suggested": sug, "confidence": rec["confidence"],
            "status": STATUS_OK if rec["confidence"] == "높음" else STATUS_CHECK,
            "reason": rec["reason"], "lag_kind": getattr(t, "kind", ""),
            "dr": accounts[dr_acc], "cr": accounts[cr_acc],
        })
    return entries


def finalize_entries(entries: list[dict]) -> list[dict]:
    """일자순 정렬 후 전표번호(BK-YYYYMM-0001)를 매긴다."""
    order = {SRC_BANK: 0, SRC_LAG: 1, SRC_CARD: 2}
    entries.sort(key=lambda e: (e["date"], order[e["source"]], e["ref_row"]))
    for n, e in enumerate(entries, 1):
        e["entry_no"] = f"BK-{e['date']:%Y%m}-{n:04d}"
    return entries


def journal_lines(entries: list[dict]) -> list[dict]:
    """전표를 분개장 줄(차변 줄, 대변 줄)로 펼친다."""
    lines = []
    for e in entries:
        base = {k: e[k] for k in ("entry_no", "date", "memo", "party", "source", "confidence", "status", "reason")}
        lines.append({**base, "code": e["dr"].code, "name": e["dr"].name, "debit": e["amount"], "credit": 0})
        lines.append({**base, "code": e["cr"].code, "name": e["cr"].name, "debit": 0, "credit": e["amount"]})
    return lines


# ======================================================================
# 4) 총계정원장 → 합계잔액시산표
# ======================================================================
def build_ledger(chart: list[Account], opening: list[Opening], lines: list[dict]) -> dict:
    led = {a.code: {"account": a, "open_dr": 0, "open_cr": 0, "dr": 0, "cr": 0, "entries": []} for a in chart}
    for o in opening:
        led[o.code]["open_dr"] += o.debit
        led[o.code]["open_cr"] += o.credit
    for ln in lines:
        rec = led[ln["code"]]
        rec["dr"] += ln["debit"]
        rec["cr"] += ln["credit"]
        rec["entries"].append(ln)
    for rec in led.values():
        rec["total_dr"] = rec["open_dr"] + rec["dr"]
        rec["total_cr"] = rec["open_cr"] + rec["cr"]
        rec["bal_dr"] = max(0, rec["total_dr"] - rec["total_cr"])
        rec["bal_cr"] = max(0, rec["total_cr"] - rec["total_dr"])
    return led


def trial_balance(led: dict) -> dict:
    """합계잔액시산표 + 차대 일치 검증. balanced 는 4쌍(전기이월·당기·합계·잔액) 모두 일치해야 True."""
    rows = [led[c] for c in sorted(led)]
    t = {k: sum(r[k] for r in rows) for k in ("open_dr", "open_cr", "dr", "cr", "total_dr", "total_cr", "bal_dr", "bal_cr")}
    balanced = t["open_dr"] == t["open_cr"] and t["dr"] == t["cr"] and t["total_dr"] == t["total_cr"] and t["bal_dr"] == t["bal_cr"]
    return {"rows": rows, "totals": t, "balanced": balanced}


def signed_balance(rec: dict, which: str = "end") -> int:
    """정상잔액 방향 기준 잔액(양수=정상, 음수=반대방향). which: open / end"""
    dr, cr = (rec["open_dr"], rec["open_cr"]) if which == "open" else (rec["total_dr"], rec["total_cr"])
    return dr - cr if rec["account"].normal == "차변" else cr - dr


# ======================================================================
# 5) 은행 Reconciliation
# ======================================================================
def reconcile_bank(bank: list[BankTx], lag: list[BankTx], led: dict, bank_code: str, tolerance: int = 0) -> dict:
    """은행 기말잔액 + 시차 항목 = 조정 후 은행잔액  vs  장부 보통예금 잔액.
    시차 항목은 '장부에는 반영(분개됨), 은행에는 아직 없는' 거래라 은행잔액에 더해(입금)/빼(출금) 맞춘다.
    남는 차이 = 원인불명. 참고로 '기초 잔액 차이'와 '잔액 불연속(은행 내역에 없는 변동)'으로 쪼개 보여준다."""
    rec = led[bank_code]
    book_open = rec["open_dr"] - rec["open_cr"]
    book_end = rec["total_dr"] - rec["total_cr"]
    first = bank[0]
    bank_open = first.balance - first.inflow + first.outflow
    bank_end = bank[-1].balance
    gaps, prev = [], None
    for t in bank:
        if prev is not None:
            expected = prev + t.inflow - t.outflow
            if t.balance != expected:
                gaps.append({"row": t.row, "date": t.date, "memo": t.memo, "expected": expected,
                             "actual": t.balance, "gap": t.balance - expected})
        prev = t.balance
    lag_net = sum(t.inflow - t.outflow for t in lag)
    adjusted = bank_end + lag_net  # 시차: 장부 입금(은행 미입금)은 은행잔액에 더하고, 장부 출금(은행 미출금)은 뺀다
    unexplained = adjusted - book_end
    return {"bank_end": bank_end, "bank_open": bank_open, "book_open": book_open, "book_end": book_end,
            "lag_items": lag, "lag_net": lag_net, "adjusted_bank": adjusted, "unexplained": unexplained,
            "open_diff": bank_open - book_open, "gaps": gaps, "gap_sum": sum(g["gap"] for g in gaps),
            "matched": abs(unexplained) <= tolerance}


def unpaid_cards(cards: list[CardTx], end: date) -> list[CardTx]:
    """기말까지 결제되지 않은(결제일 없음 또는 기말 이후) 카드 사용 — 미지급금 시차 목록."""
    return [c for c in cards if c.settle_date is None or c.settle_date > end]


# ======================================================================
# 6) BS 계정 검토 (가지급금·가수금·미지급비용 등)
# ======================================================================
def review_bs(led: dict, origins: dict[str, date], watch: list[str], stale_days: int, end: date) -> list[dict]:
    """자산·부채 계정의 마이너스(반대방향) 잔액, 주요 계정의 장기 미정리를 점검.
    반환: [{account, open_bal, end_bal, flags:[(severity, category, text)]}] — 잔액·플래그가 있거나 watch 인 계정만."""
    rows = []
    for code in sorted(led):
        rec = led[code]
        a = rec["account"]
        if a.kind not in ("자산", "부채"):
            continue
        ob, eb = signed_balance(rec, "open"), signed_balance(rec, "end")
        flags = []
        if eb < 0:
            flags.append(("warning", "마이너스 잔액", f"기말 잔액이 정상 방향({a.normal})과 반대입니다 ({eb:,}원) — 원인 확인"))
        if a.name in watch:
            origin = origins.get(code)
            if eb != 0 and ob != 0 and eb >= ob:
                flags.append(("warning", "전기 이월 미정리", f"전기말 {ob:,}원이 정리되지 않고 기말 {eb:,}원으로 유지·증가"))
            if eb != 0 and origin is not None and (end - origin).days >= stale_days:
                flags.append(("warning", "장기 미정리", f"발생일 {origin} 이후 {(end - origin).days}일 경과 (기준 {stale_days}일) — 잔액 {eb:,}원"))
            if eb != 0 and not flags:
                flags.append(("info", "잔액 존재", f"기말 잔액 {eb:,}원 — 정리 계획 확인"))
            if a.name == "가지급금" and eb > 0:
                flags.append(("info", "세무 검토", "가지급금 잔액은 세무상 영향이 있을 수 있어 코택(세무조정) 검토 필요"))
        if ob != 0 or eb != 0 or flags or a.name in watch:
            rows.append({"account": a, "open_bal": ob, "end_bal": eb, "flags": flags})
    return rows


# ======================================================================
# 7) 결산 체크리스트·PBC
# ======================================================================
def _load_items(rules_dir: Path, filename: str) -> list[dict]:
    data = yaml.safe_load((Path(rules_dir) / filename).read_text(encoding="utf-8-sig")) or {}
    return data.get("items", [])


def build_checklist(rules_dir: Path, month: int, hints: dict) -> list[dict]:
    """월 결산은 항상, 분기말(3·6·9·12월)이면 분기 항목, 12월이면 연 항목을 더한다."""
    freqs = ["monthly"] + (["quarterly"] if month in (3, 6, 9, 12) else []) + (["annual"] if month == 12 else [])
    label = {"monthly": "월", "quarterly": "분기", "annual": "연"}
    out = []
    for it in _load_items(rules_dir, "closing_checklist.yaml"):
        if it["frequency"] in freqs:
            out.append({"key": it["key"], "group": label[it["frequency"]], "title": it["title"],
                        "detail": it.get("detail", ""), "hint": hints.get(it.get("hint") or "", ""), "status": "open"})
    out.sort(key=lambda x: ["월", "분기", "연"].index(x["group"]))
    return out


def build_pbc(rules_dir: Path, ready: dict) -> list[dict]:
    """PBC 템플릿 → 항목. ready: {auto_ready 키: 준비상태}. 나머지는 미준비."""
    out = []
    for n, it in enumerate(_load_items(rules_dir, "pbc_template.yaml"), 1):
        status = ready.get(it.get("auto_ready") or "", "미준비")
        out.append({"key": it["key"], "group": it["category"], "title": it["title"],
                    "detail": f"PBC-{n:02d} · 담당: {it.get('owner', '')}", "status": status})
    return out
