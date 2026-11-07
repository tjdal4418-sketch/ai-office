"""홈택스 '근로소득 간이세액표' 엑셀 → app/rules/<연도>/simple_tax_table.csv 변환.

원본 엑셀은 docs/sources/ 에 보관한다(출처 자료). 사용:
  .venv\\Scripts\\python.exe scripts\\import_simple_tax_table.py        # 기본: docs/sources/근로소득 간이세액표_2026.03.01.xlsx, 2026년, 시행일 2026.03.01
  .venv\\Scripts\\python.exe scripts\\import_simple_tax_table.py "<엑셀 경로>" 2026 [적용시작일]

엑셀 '근로소득간이세액표' 시트의 숫자 구간(월급여액 천원 단위, 이상~미만)만 읽어 원 단위 구간으로 바꾼다.
'-' 는 세액 0원. '10,000천원' 한 줄은 월급여 정확히 1천만원인 경우라 [10,000,000, 10,000,001) 로 둔다.
'10,000천원 초과' 계산식 행은 표가 아니라 공식이므로 CSV에 넣지 않는다(구간 없음으로 이상항목 표시).
공제대상가족 1~11명 열(C~M) → f1~f11. 자녀 세액공제 금액은 CSV가 아니라 rules/withholding.yaml 에 적는다.
"""
import csv
import sys
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
DATA_SHEET = "근로소득간이세액표"
DEFAULT_SOURCE = ROOT / "docs" / "sources" / "근로소득 간이세액표_2026.03.01.xlsx"


def _won(v) -> int:
    """'-' 는 0, 숫자는 그대로(원)."""
    return 0 if v in (None, "-") else int(v)


def read_rows(xlsx: Path) -> list[tuple[int, int, list[int]]]:
    ws = load_workbook(xlsx, data_only=True)[DATA_SHEET]
    rows = []
    for r in ws.iter_rows(values_only=True):
        a, b = r[0], r[1]
        if isinstance(a, int) and isinstance(b, int):
            rows.append((a * 1000, b * 1000, [_won(v) for v in r[2:13]]))
        elif isinstance(a, str) and a.replace(",", "").strip() == "10000천원" and isinstance(r[2], int):
            rows.append((10_000_000, 10_000_001, [_won(v) for v in r[2:13]]))  # 월급여 정확히 10,000천원
    return rows


def check(rows) -> None:
    assert len(rows) > 100 and all(len(t) == 11 for _, _, t in rows), "행/열 수가 이상합니다"
    for (lo, hi, _), (lo2, _, _) in zip(rows, rows[1:]):
        assert hi == lo2, f"구간이 이어지지 않습니다: {hi} → {lo2}"
        assert lo < hi


def write_csv(rows, out: Path, source_name: str, year: int, effective: str) -> None:
    head = [
        f"# 근로소득 간이세액표 — 적용연도 {year}",
        "# 출처: 국세청 홈택스 '근로소득 간이세액표' (소득세법 시행령 별표2, 제189조제1항 관련)",
        f"# 원본 파일: {source_name} (시행일 {effective})",
        f"# 적용연도: {year}   반입일: {date.today().isoformat()}",
        "# 구간 [salary_from, salary_to) 은 월급여액(비과세·학자금 제외, 원). f1~f11 = 공제대상가족 수(본인 포함) 1~11명의 월 소득세(원). 원본의 '-' 는 0.",
        f"# 범위: {rows[0][0]:,}원 이상 ~ 월급여 정확히 10,000,000원 (원본 표의 숫자 구간 전체). 10,000천원 초과 구간은 계산식이라 표에 없음 → 구간 없음으로 이상항목 표시.",
        "# 11명 초과 가족·8세 이상 20세 이하 자녀 공제는 표가 아니라 별도 규칙(자녀 공제 금액은 withholding.yaml child_credit).",
    ]
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        f.write("\n".join(head) + "\n")
        w = csv.writer(f)
        w.writerow(["salary_from", "salary_to"] + [f"f{i}" for i in range(1, 12)])
        for lo, hi, taxes in rows:
            w.writerow([lo, hi] + taxes)


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    year = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    effective = sys.argv[3] if len(sys.argv) > 3 else ("2026.03.01" if src == DEFAULT_SOURCE else "")
    rows = read_rows(src)
    check(rows)
    out = ROOT / "app" / "rules" / str(year) / "simple_tax_table.csv"
    write_csv(rows, out, src.name, year, effective)
    print(f"{out} 생성: {len(rows)}개 구간, {rows[0][0]:,}원 ~ {rows[-1][0]:,}원")
