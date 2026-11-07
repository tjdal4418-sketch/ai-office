"""전역 설정. 다른 모듈은 `config.X` 형태로 호출 시점에 읽는다(테스트에서 값 교체 가능)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR = BASE_DIR / "uploads"
RULES_DIR = BASE_DIR / "app" / "rules"
SAMPLE_DIR = BASE_DIR / "sample_data"
STATIC_DIR = BASE_DIR / "app" / "static"
DB_URL = os.getenv("AI_OFFICE_DB_URL", f"sqlite:///{(BASE_DIR / 'ai_office.db').as_posix()}")

# 승인 후 '완료' 상태를 보여주는 시간(초). 지나면 대기중으로 복귀
DONE_HOLD_SECONDS = float(os.getenv("AI_OFFICE_DONE_HOLD", "10"))
# 데모 작업 소요 시간(초): 진행률이 이 시간 동안 0→100% 로 올라간다
DEMO_SECONDS = float(os.getenv("AI_OFFICE_DEMO_SECONDS", "10"))

# 실제 계산 에이전트가 한 명(건) 처리할 때마다 쉬는 시간(초) — 진행률이 화면에서 보이도록. 테스트에서는 0
CALC_DELAY = float(os.getenv("AI_OFFICE_CALC_DELAY", "0.25"))

# 매니저가 하위 작업(페이·북키·배트·코택)의 진행을 확인하는 주기(초)와 단계별 최대 대기(초). 테스트에서는 짧게
MANAGER_POLL = float(os.getenv("AI_OFFICE_MANAGER_POLL", "0.3"))
MANAGER_STEP_TIMEOUT = float(os.getenv("AI_OFFICE_MANAGER_TIMEOUT", "300"))

UPLOAD_MAX_BYTES = 10 * 1024 * 1024
UPLOAD_EXTENSIONS = (".xlsx", ".xls", ".csv")
