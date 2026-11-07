r"""rules 폴더의 모든 수치를 docs/rules_check.md 로 출력한다 (GET /api/rules 와 같은 내용, app/core/rules_catalog.py 공용).

실행: .venv\Scripts\python.exe scripts\export_rules_check.py [연도]
rules 를 고치거나 법령 MCP 로 검증한 뒤에 다시 실행하면 문서가 갱신된다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import rules_catalog  # noqa: E402


def main() -> int:
    year = int(sys.argv[1]) if len(sys.argv) > 1 else None
    data = rules_catalog.catalog(year)
    out = ROOT / "docs" / "rules_check.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(rules_catalog.to_markdown(data), encoding="utf-8")
    s = data["summary"]
    print(f"{out} 생성: {data['year']}년 전체 {s['total']}개 중 확인필요 {s['needs_check']}개 (값 없음 {s['null_values']}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
