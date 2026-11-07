"""코택(법인세 세무조정) 계산 로직 — 순수 함수 모음.

입력: 합계잔액시산표(북키 결과물 재사용, 또는 시산표 파일). 처리: 세무조정 항목별 잔액과 '검토 필요 여부',
기업업무추진비 한도·가지급금 인정이자·대손충당금 한도 추정. 최종 세무조정은 담당자가 한다 — 여기서는 '검토 후보'와 추정치만 만든다.
금액은 원 단위 정수(Decimal 로 계산 후 절사), 법령 수치는 rules/corptax.yaml 에서만 읽는다.
"""
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from app.agents.bookkeeping_calc import read_rows
from app.agents.payroll_calc import _blank, issue, trunc
from app.agents.bookkeeping_calc import _amount
from app.core import db
from app.core.rules import Rules

TB_COLUMNS = {"계정코드": "code", "코드": "code", "계정과목": "name", "계정과목명": "name", "구분": "kind",
              "정상잔액": "normal", "차변잔액": "debit", "대변잔액": "credit", "차변": "debit", "대변": "credit"}
KINDS = ("자산", "부채", "자본", "수익", "비용")
STATUS_ADJUST, STATUS_REVIEW, STATUS_NONE = "조정 필요", "검토 필요", "해당 없음"
TB_KEYWORDS = ("시산표", "trial", "tb")


@dataclass
class TbRow:
    code: str
    name: str
    kind: str
    normal: str   # 차변 / 대변
    debit: int    # 잔액 차변
    credit: int   # 잔액 대변

    @property
    def balance(self) -> int:
        """정상잔액 방향 기준 잔액(양수 = 정상 방향)."""
        return self.debit - self.credit if self.normal == "차변" else self.credit - self.debit


# ======================================================================
# 1) 시산표 입력 — 파일 / 북키 결과물
# ======================================================================
def validate_tb(rows, missing) -> tuple[list[TbRow], list[dict]]:
    sheet = "시산표"
    issues: list[dict] = []
    for f, label in (("code", "계정코드"), ("name", "계정과목"), ("kind", "구분"), ("debit", "차변잔액"), ("credit", "대변잔액")):
        if f in missing:
            issues.append(issue("error", "필수값 누락", f"{sheet}에 필수 열이 없습니다: {label}", sheet))
    if issues:
        return [], issues
    out: list[TbRow] = []
    codes, names = {}, {}
    for n, r in rows:
        ref = f"{sheet} 행 {n}"
        before = len(issues)
        if any(_blank(r.get(f)) for f in ("code", "name", "kind")):
            issues.append(issue("error", "필수값 누락", "계정코드·계정과목·구분 중 빈 칸이 있습니다", ref))
            continue
        code, name, kind = str(r["code"]).strip(), str(r["name"]).strip(), str(r["kind"]).strip()
        if kind not in KINDS:
            issues.append(issue("error", "형식 오류", f"구분은 {'/'.join(KINDS)} 중 하나여야 합니다 ('{kind}')", ref))
        normal = str(r.get("normal") or "").strip() or ("차변" if kind in ("자산", "비용") else "대변")
        if normal not in ("차변", "대변"):
            issues.append(issue("error", "형식 오류", f"정상잔액은 차변/대변이어야 합니다 ('{normal}')", ref))
        if code in codes or name in names:
            issues.append(issue("error", "중복 계정", f"계정 '{code} {name}' 이(가) 행 {codes.get(code) or names.get(name)} 과(와) 중복", ref))
        dr = _amount(r.get("debit"), "차변잔액", ref, issues)
        cr = _amount(r.get("credit"), "대변잔액", ref, issues)
        if (dr or 0) < 0 or (cr or 0) < 0:
            issues.append(issue("error", "숫자 형식", "잔액은 음수일 수 없습니다(반대편 열에 입력)", ref))
        codes[code], names[name] = n, n
        if len(issues) == before:
            out.append(TbRow(code, name, kind, normal, dr or 0, cr or 0))
    if not issues:
        tdr, tcr = sum(t.debit for t in out), sum(t.credit for t in out)
        if tdr != tcr:
            issues.append(issue("error", "차대 불일치", f"시산표 차변 합계 {tdr:,}원 ≠ 대변 합계 {tcr:,}원 (차이 {tdr - tcr:+,}원)", sheet))
    if not rows:
        issues.append(issue("error", "필수값 누락", f"{sheet}에 데이터가 없습니다", sheet))
    return out, issues


def read_tb_file(path: Path) -> tuple[list[TbRow], list[dict]]:
    rows, missing = read_rows(path, TB_COLUMNS)
    return validate_tb(rows, missing)


def sidecar_path(output_path: str | Path) -> Path:
    """북키 결과 엑셀 옆에 저장하는 시산표 JSON (엑셀은 수식이라 값을 못 읽으므로 따로 보관)."""
    return Path(output_path).with_suffix(".tb.json")


def tb_from_bookkeeping(data: dict) -> list[TbRow]:
    return [TbRow(r["code"], r["name"], r["kind"], r["normal"], r["bal_dr"], r["bal_cr"]) for r in data["rows"]]


def find_upstream(client_id: str, tax_year: int) -> tuple[dict, dict] | None:
    """같은 고객사·적용연도의 가장 최근 북키 작업(검토 대기·승인 완료) + 시산표 JSON. 없으면 None."""
    for t in db.list_tasks("bookkeeping", limit=50):
        if t["client_id"] != client_id or t["tax_year"] != tax_year or t["status"] not in ("review", "done"):
            continue
        if not t["output_path"]:
            continue
        p = sidecar_path(t["output_path"])
        if p.exists():
            return t, json.loads(p.read_text(encoding="utf-8"))
    return None


def by_name(tb: list[TbRow]) -> dict[str, TbRow]:
    return {t.name: t for t in tb}


def balance_of(tb_by_name: dict[str, TbRow], names: list[str]) -> int:
    return sum(tb_by_name[n].balance for n in names if n in tb_by_name)


def revenue_from_tb(tb: list[TbRow]) -> int:
    """수입금액 추정: 수익 계정 중 이름에 '매출'이 들어간 계정의 잔액 합."""
    return sum(t.balance for t in tb if t.kind == "수익" and "매출" in t.name)


# ======================================================================
# 2) 한도·추정 계산
# ======================================================================
def entertainment_limit(is_sme: bool, months: int, revenue: int, rules: Rules) -> dict:
    """기업업무추진비 한도(법인세법 제25조④) = 기본한도(개월 환산) + 수입금액 구간별 '누적' 적용률. 반환: 구성요소와 합계.

    100억 초과분에는 2구간 적용률, 500억 초과분에는 3구간 적용률만 적용한다
    (예: 수입금액 600억 = 100억×0.3% + 400억×0.2% + 100억×0.03% = 3천만 + 8천만 + 300만)."""
    basic = int(rules.v("corptax.entertainment.basic_limit_sme" if is_sme else "corptax.entertainment.basic_limit_general"))
    basic_prorated = trunc(Decimal(basic) * Decimal(months) / Decimal(12))
    t2, t3 = Decimal(rules.v("corptax.entertainment.tier2_over")), Decimal(rules.v("corptax.entertainment.tier3_over"))
    r1, r2, r3 = (rules.d(f"corptax.entertainment.tier{i}_rate") for i in (1, 2, 3))
    rev = Decimal(revenue)
    part = min(rev, t2) * r1 + max(Decimal(0), min(rev, t3) - t2) * r2 + max(Decimal(0), rev - t3) * r3
    revenue_part = trunc(part)
    return {"basic": basic, "basic_prorated": basic_prorated, "revenue": revenue, "revenue_part": revenue_part,
            "limit": basic_prorated + revenue_part}


def imputed_interest(balance: int, months: int, rules: Rules) -> int:
    """가지급금 잔액 × 이자율 × 개월/12 (수령 이자 차감 전 추정치)."""
    return trunc(Decimal(balance) * rules.d("corptax.imputed_interest.rate") * Decimal(months) / Decimal(12))


def bad_debt_limit(receivables: int, rules: Rules) -> int:
    return trunc(Decimal(receivables) * rules.d("corptax.bad_debt.fixed_rate"))


# ======================================================================
# 3) 세무조정 체크리스트
# ======================================================================
def load_items(rules_dir: Path, filename: str) -> list[dict]:
    data = yaml.safe_load((Path(rules_dir) / filename).read_text(encoding="utf-8-sig")) or {}
    return data.get("items", [])


def build_review(tb: list[TbRow], rules: Rules, items: list[dict], is_sme: bool, months: int, revenue: int) -> dict:
    """항목별 잔액·검토 필요 여부. 반환: {"items": [...], "ent": 기업업무추진비 한도 dict, ...} (계산 결과를 엑셀·테스트가 함께 씀)."""
    names = by_name(tb)
    ent = entertainment_limit(is_sme, months, revenue, rules)
    out = []
    calc = {"ent": ent}
    for it in items:
        amount = balance_of(names, it["primary"])
        related = [(n, names[n].balance) for n in it.get("related", []) if n in names and names[n].balance]
        adjust, summary, status = None, "", STATUS_REVIEW if amount else STATUS_NONE
        kind = it.get("calc", "")
        if kind == "entertainment":
            excess = max(0, amount - ent["limit"])
            adjust = excess
            summary = (f"한도 {ent['limit']:,}원 (기본 {ent['basic_prorated']:,} + 수입금액 {revenue:,}원 기준 {ent['revenue_part']:,}) — "
                       + (f"초과 {excess:,}원 손금불산입 검토" if excess else f"한도 내 (여유 {ent['limit'] - amount:,}원)"))
            status = STATUS_NONE if not amount else (STATUS_ADJUST if excess else STATUS_REVIEW)
        elif kind == "imputed":
            interest = imputed_interest(amount, months, rules)
            calc["imputed"] = interest
            adjust = interest
            summary = (f"가지급금 {amount:,}원 × 연 {rules.d('corptax.imputed_interest.rate') * 100:.1f}% × {months}/12개월 = 인정이자 추정 {interest:,}원 (수령 이자 차감 전)"
                       if amount else "가지급금 잔액 없음")
            status = STATUS_NONE if not amount else STATUS_ADJUST
        elif kind == "bad_debt":
            recv = balance_of(names, ["외상매출금", "미수금"])
            limit = bad_debt_limit(recv, rules)
            excess = max(0, amount - limit)
            calc["bad_debt"] = {"receivables": recv, "limit": limit, "excess": excess}
            adjust = excess
            summary = (f"채권잔액 {recv:,}원 × {rules.d('corptax.bad_debt.fixed_rate') * 100:.1f}% = 한도 {limit:,}원 — 충당금 {amount:,}원 "
                       + (f"(초과 {excess:,}원)" if excess else "(한도 내)"))
            if excess:
                status = STATUS_ADJUST
            elif amount:
                status = STATUS_REVIEW
            elif recv:
                status, summary = STATUS_REVIEW, summary + " — 충당금 미설정, 설정 여부 검토"
            else:
                status = STATUS_NONE
        elif kind == "vehicle":
            vehicle_assets = balance_of(names, ["차량운반구"])
            summary = (f"1대당 감가상각비 한도 {int(rules.v('corptax.vehicle.depreciation_limit')):,}원, 연 비용 {int(rules.v('corptax.vehicle.log_threshold')):,}원 초과 시 운행기록부 필요 "
                       f"(차량유지비 {amount:,}원, 차량운반구 {vehicle_assets:,}원)")
            status = STATUS_REVIEW if (amount or vehicle_assets) else STATUS_NONE
        else:
            summary = it["point"] if amount else "잔액 없음"
            if it["key"] == "retirement":
                summary = (f"충당금 한도 규칙 미확인 — 수기 검토 (잔액 {amount:,}원)" if amount else "잔액 없음")
        out.append({**it, "amount": amount, "related_balances": related, "adjust": adjust, "status": status, "summary": summary})
    return {"items": out, **calc}
