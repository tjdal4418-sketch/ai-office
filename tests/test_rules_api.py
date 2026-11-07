"""법령 자료실: GET /api/rules 와 docs/rules_check.md (app/core/rules_catalog.py 공용)."""
import pytest
import yaml
from fastapi.testclient import TestClient

from app.core import config, rules_catalog
from app.main import app

FIELDS = {"name", "label", "value", "source", "year", "verified", "verified_on", "needs_check", "reason"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    with TestClient(app) as c:
        yield c


def items(data):
    return [i for f in data["files"] for i in f["items"]]


def test_API가_항목별_필수_필드를_돌려준다(client):
    d = client.get("/api/rules").json()
    assert d["year"] == 2026 and d["years"] == [2026]
    all_items = items(d)
    assert all_items and all(FIELDS <= set(i) for i in all_items)
    names = [i["name"] for i in all_items]
    assert len(names) == len(set(names))                                       # 항목명은 중복 없음
    for needed in ("insurance.national_pension.employee_rate", "corptax.entertainment.basic_limit_sme",
                   "vat.penalty.late_issue_rate", "withholding.business_income.rate", "deadlines.vat_prelim"):
        assert needed in names


def test_요약_개수는_항목과_일치한다(client):
    d = client.get("/api/rules").json()
    s, all_items = d["summary"], items(d)
    assert s["total"] == len(all_items)
    assert s["needs_check"] == sum(1 for i in all_items if i["needs_check"])
    assert s["verified"] == sum(1 for i in all_items if i["verified"])
    assert s["external"] == sum(1 for i in all_items if i["external"])
    assert s["needs_check"] + s["external"] + s["verified"] == s["total"]      # 빨강 + 주황 + 확인됨 = 전체 (겹치지 않는다)


def test_모든_rules_yaml_수치가_빠짐없이_들어있다(client):
    """rules 폴더의 값 항목(value 가 있는 모든 leaf)이 카탈로그에 하나도 빠지지 않는다."""
    expected = set()
    for name in rules_catalog.LEAF_FILES:
        data = yaml.safe_load((config.RULES_DIR / "2026" / f"{name}.yaml").read_text(encoding="utf-8-sig"))
        expected |= {path for path, _, _ in rules_catalog._walk(data, name)}
    got = {i["name"] for i in items(client.get("/api/rules").json())}
    assert expected and expected <= got


def test_확인필요_판정_규칙(client):
    by = {i["name"]: i for i in items(client.get("/api/rules").json())}
    fixed = by["insurance.national_pension.employee_rate"]                                 # 2026-11-07 법령 MCP 확인 완료
    assert fixed["needs_check"] is False and fixed["verified_on"] == "2026-11-07" and "부칙" in fixed["source"]
    assert by["insurance.health_insurance.truncate_unit"]["verified"] is True              # 2차 검증: 국고금 관리법 제47조③ 준용
    assert "국고금 관리법" in by["withholding.business_income.truncate_unit"]["source"]
    # 외부 자료 반입 필요(주황): 빨강(확인필요)이 아니고, 받을 곳 안내가 있다
    null_item = by["withholding.report_codes.wage_simple"]                                  # 값이 null
    assert null_item["external"] is True and null_item["needs_check"] is False and null_item["value"] == "(값 없음)"
    assert null_item["reason"].startswith("외부 자료 반입 필요") and "작성요령" in null_item["where"]
    # 홈택스 간이세액표를 반입한 뒤: 표와 자녀 공제는 확인됨(external 표시 없음)
    for name in ("withholding.simple_table.file", "withholding.child_credit.one_child", "simple_tax_table"):
        done = by[name]
        assert done["verified"] and not done["external"] and not done["needs_check"] and done["verified_on"] == "2026-11-07", name
    assert by["withholding.child_credit.one_child"]["value"] == "20,830" and "별표2" in by["withholding.child_credit.one_child"]["source"]
    assert by["holidays"]["external"] and by["holidays"]["where"]                           # 공휴일은 아직 외부 자료 필요
    ok = by["payroll.anomaly.salary_change_ratio"]                                          # 검증 완료(사무실 기준)
    assert ok["needs_check"] is False and ok["verified"] is True and ok["verified_on"] == "2026-11-07" and ok["reason"] == ""


def test_항목_이름과_값_표시(client):
    by = {i["name"]: i for i in items(client.get("/api/rules").json())}
    floor = by["insurance.national_pension.limits.h1_2026.floor"]
    assert "하한" in floor["label"] and "국민연금" in floor["label"] and floor["value"] == "400,000"
    assert by["insurance.national_pension.employee_rate"]["value"] == "0.0475 (4.75%)"
    assert by["insurance.national_pension.employee_rate"]["source"] and by["insurance.national_pension.employee_rate"]["year"] == 2026
    assert "원천세" in by["deadlines.withholding_monthly"]["label"] and "매월 10일" in by["deadlines.withholding_monthly"]["value"]


def test_확인필요만_보기와_연도_오류(client):
    d = client.get("/api/rules?needs_check_only=true").json()
    assert all(i["needs_check"] for i in items(d))
    assert d["summary"]["total"] == client.get("/api/rules").json()["summary"]["total"]   # 요약은 전체 기준 그대로
    assert client.get("/api/rules?year=1999").status_code == 404


def test_markdown은_API와_같은_내용(client, tmp_path):
    d = client.get("/api/rules").json()
    md = rules_catalog.to_markdown(d)
    s = d["summary"]
    assert f"확인필요 {s['needs_check']}개** / 전체 {s['total']}개" in md
    for i in items(d):
        assert f"`{i['name']}`" in md, i["name"]                                          # 모든 항목이 표에 있다
    assert md.count("🔴 TODO: 확인 필요") == s["needs_check"]
    assert md.count("✔ 확인됨") == s["verified"]
    assert md.count("🟠 외부 자료 반입 필요 (") == s["external"]                          # 주황 항목마다 받을 곳 안내가 붙는다
    assert f"외부 자료 반입 필요 {s['external']}개" in md
    # 표 한 줄에 '|' 가 들어 있는 값(키워드 목록 등)이 표를 깨뜨리지 않는다
    for line in md.splitlines():
        if line.startswith("| ") and "<br>`" in line:  # 항목 상세 표의 줄만
            assert line.replace("\\|", "").count("|") == 7, line


def test_docs_rules_check_md가_최신이다():
    """docs/rules_check.md 의 개수가 현재 rules 와 같다 (rules 를 고쳤으면 scripts/export_rules_check.py 를 다시 실행)."""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "docs" / "rules_check.md").read_text(encoding="utf-8")
    s = rules_catalog.catalog()["summary"]
    assert f"확인필요 {s['needs_check']}개** / 전체 {s['total']}개" in text
    assert f"외부 자료 반입 필요 {s['external']}개" in text
