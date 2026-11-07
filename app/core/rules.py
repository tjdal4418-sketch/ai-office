"""적용연도별 법령 수치(rules) 로더.

규칙 1: 법령 수치는 코드에 넣지 않고 app/rules/<연도>/*.yaml 에서 읽는다.
각 항목(leaf)은 {value, source, year, verified, verified_on} 형태이며,
value 가 null 이면 '확인 필요' 상태라 계산에 쓰면 RuleMissing 오류를 낸다.
경로는 '<파일명>.<키>.<키>' 형식(예: insurance.national_pension.employee_rate).
"""
from decimal import Decimal
from pathlib import Path

import yaml

from app.core import config


# 급여 계산(페이)에서 읽는 규칙 파일. vat/corptax/deadlines 는 다른 단계에서 사용한다.
PAYROLL_RULE_FILES = ("insurance", "payroll", "withholding")


class RuleMissing(Exception):
    """규칙 값이 비어 있음(TODO: 확인 필요)."""


def _is_leaf(node) -> bool:
    return isinstance(node, dict) and "value" in node


class Rules:
    def __init__(self, data: dict, year: int | None = None, base_dir: Path | None = None):
        self.data = data
        self.year = year
        self.base_dir = base_dir  # 간이세액표 CSV 등 부속 파일 위치

    # ---------- 조회 ----------
    def item(self, path: str) -> dict:
        node = self.data
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                raise RuleMissing(f"규칙 '{path}' 항목이 없습니다")
            node = node[key]
        if not _is_leaf(node):
            raise RuleMissing(f"규칙 '{path}' 은(는) 값 항목이 아닙니다")
        return node

    def v(self, path: str):
        """값을 반환. 비어 있으면 RuleMissing."""
        value = self.item(path)["value"]
        if value is None:
            raise RuleMissing(f"규칙 '{path}' 값이 비어 있습니다 (TODO: 확인 필요)")
        return value

    def d(self, path: str) -> Decimal:
        """금액·요율 계산용 Decimal (float 오차 방지)."""
        return Decimal(str(self.v(path)))

    # ---------- 목록 (계산근거 시트용) ----------
    def items(self):
        """(경로, leaf) 를 정의 순서대로."""
        def walk(node, prefix):
            for key, val in node.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                if _is_leaf(val):
                    yield path, val
                elif isinstance(val, dict):
                    yield from walk(val, path)
        yield from walk(self.data, "")

    def unverified(self) -> list[str]:
        return [p for p, it in self.items() if not it.get("verified")]


def load_rules(year: int, files: tuple[str, ...] = PAYROLL_RULE_FILES) -> Rules:
    folder = config.RULES_DIR / str(year)
    data: dict = {}
    for name in files:
        f = folder / f"{name}.yaml"
        if f.exists():
            data[name] = yaml.safe_load(f.read_text(encoding="utf-8-sig")) or {}
    return Rules(data, year=year, base_dir=folder)
