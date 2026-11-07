# AI 오피스 — 프로젝트 규칙

EY한영 통합세무팀(아웃소싱 회계·세무) 지원용 포트폴리오. GitHub 공개 예정.
가상 회계법인 사무실에 "AI 직원" 5명이 근무하며, 담당업무별 반복 업무를 자동화한다.

**핵심 원칙 (Human-in-the-loop)**: AI는 *작성·검증·대사*까지만. 최종 확정은 담당자의 "검토 승인".

## 에이전트

| 아이콘 | 이름 | 담당 | 파일 |
|---|---|---|---|
| 💰 | 페이 (Payroll) | 급여·4대보험·원천세·원천징수이행상황신고서 | agents/payroll.py |
| 📒 | 북키 (BK) | 분개 추천·시산표·계정 Reconciliation·결산·PBC | agents/bookkeeping.py |
| 🧾 | 배트 (VAT) | 세금계산서 대사·불공제 플래그·부가세 요약 | agents/vat.py |
| 🏛️ | 코택 (Corp Tax) | 세무조정 체크리스트·접대비 한도·세무조사 자료목록 | agents/corptax.py |
| 📁 | 매니저 (Office Manager) | 업무 배분·신고기한 캘린더·진행 리포트 | agents/manager.py |

## 상태값 (set_status 로만 변경)

`idle`🟢 대기중 · `working`🔵 일하는중(진행률%, 단계 텍스트) · `review`🟡 검토필요 · `done`✅ 완료(일정 시간 뒤 idle 복귀) · `error`🔴 오류(사유 표시)

## 반드시 지킬 규칙

1. **법령 수치 하드코딩 금지.** 세율·보험요율·간이세액표·신고기한은 전부 `app/rules/<적용연도>/` 설정 파일에(예: rules/2026/insurance.yaml). 작업 실행 시 적용연도를 선택한다. 항목마다 `근거 법령/고시명, 적용연도, 확인일` 주석. 불확실하면 추측 금지 → `# TODO: 확인 필요` 표시 + 사용자에게 목록 보고(법령 MCP로 사용자가 검증).
2. **샘플 데이터는 전부 가상.** 가상 회사·가상 인물. 주민번호 형식은 마스킹(`900101-1******`).
3. **모든 에이전트는 `BaseAgent` 상속.** 상태 변경은 공통 `set_status()` 로만 → 홈페이지 자동 반영.
4. **작업 순서 고정**: 입력 검증 → 계산/대사 → 이상항목 리포트 → 결과파일 생성 → 검토필요.
5. **계산 로직마다 pytest.** 손계산 가능한 예시(예: 월급 300만원 1명)로 검증.
6. 코드 주석·UI 문구는 **한국어**, 변수명은 **영어**.
7. **단계별 진행.** 한 단계 끝나면 "실행 방법 / 확인할 화면 / 다음 단계" 요약 후 멈춘다.
8. 엑셀 결과물은 계산 셀에 **수식이 살아있게** 작성(openpyxl).

## 기술 스택

Python 3.11+ · FastAPI · pandas · openpyxl · SQLite(SQLAlchemy) · SSE(어려우면 2초 폴링) · 단일 HTML + Tailwind CDN + 바닐라 JS(빌드 도구 없음).
작업 처리는 asyncio 큐 우선(외부 인프라 없이 로컬 실행). 실행: `pip install -r requirements.txt` → `uvicorn app.main:app --reload`

## 폴더 구조

```
ai-office/
├─ app/
│  ├─ main.py          # FastAPI 진입점, 라우터 등록
│  ├─ core/            # state.py(상태), queue.py(작업큐), events.py(SSE), db.py
│  ├─ agents/          # base.py + payroll/bookkeeping/vat/corptax/manager
│  ├─ rules/           # 법령 기반 수치 (YAML/CSV) — 출처 주석 필수
│  └─ static/          # index.html(홈), agent.html(상세), css/, js/
├─ sample_data/        # 가상 고객사 샘플
├─ outputs/            # 생성된 결과 파일 (git 제외)
├─ tests/              # pytest
├─ CLAUDE.md
└─ README.md
```

## 진행상황 체크리스트

- [x] 0단계: 설계·폴더 골격·문서 초안
- [x] 1단계: core(상태·SSE·큐·DB) + BaseAgent + 홈 대시보드(평면도·활동 로그·요약·D-day·다크모드) + 상세(업로드·이력) + 검토 승인/반려 (10초 데모 작업, pytest 24개 통과)
- [x] 2단계: 페이 — 급여·4대보험·원천세·사업/기타소득·이행상황신고서 집계·이상항목·엑셀(수식 유지) + 샘플 데이터 + pytest (연말정산·퇴직소득은 "추후 확장" 메뉴만)
- [x] 3단계: 북키 — 규칙 기반 분개 추천(신뢰도·확인필요)·분개장→원장→시산표(차대 검증)·은행 Reconciliation·BS 계정 검토·결산 체크리스트·PBC (체크/상태는 화면에서 관리, pytest 106개 통과)
- [x] 4단계: 배트 — 세금계산서↔장부 대사(일치/금액차이/장부누락/세금계산서누락)·불공제 후보 플래그·신고서 요약·발행 관리(가산세 위험)·신고기한 D-day (pytest 143개 통과)
- [x] 5단계: 코택(세무조정 검토표·접대비 한도·인정이자·대손충당금 추정·요청자료 트래킹) + 매니저(신고기한 캘린더·공휴일 순연·월마감 업무 배분·진행 현황 매트릭스) + 연동(북키 시산표→코택). **페이 급여집계→북키 급여분개 연동은 미구현**
- [x] 6단계: 매니저 — 5단계에서 코택과 함께 구현 (pytest 192개 통과)
- [ ] 5.5단계: 픽셀아트 오피스 뷰(`/static/office.html`, 프론트만 추가 — 백엔드·기존 index/agent 화면은 수정하지 않음). ① 맵·가구 ✅ ② 캐릭터·걷기·A* 길찾기·서로 피하기·대기중 행동(정수기·커피·소파·화분·창밖·책장)·잡담·일하는중 기지개/복합기 ✅ (`agents.js`) ③ 상태별 연출(모니터·말풍선·복합기)·SSE 연결·데모 모드 ✅ (시험 패널은 제거, 데모 버튼만 남김; `?debug=1` 이면 행동 정보 줄 표시, `?demo=1` 이면 데모 자동 시작, 좁은 창은 `?force=1`) ④ 사이드 패널·캘린더·법령 자료실·활동 로그·상단 정보 줄 ✅ (`panels.js`; 패널 승인/반려·업로드·다운로드는 기존 API 재사용, 반려는 사유 필수, ESC·바깥 클릭으로 닫힘). 법령 자료실은 읽기 전용 `GET /api/rules`(app/core/rules_catalog.py)를 읽는다 — 항목명·값·근거·적용연도·확인일·확인필요 여부, `summary.needs_check` 가 미확인 개수. 같은 내용을 `docs/rules_check.md` 로 출력: `.venv\Scripts\python.exe scripts\export_rules_check.py` (rules 를 고치거나 검증한 뒤 다시 실행, `tests/test_rules_api.py` 가 문서가 최신인지 확인). 파일: `app/static/office/{map,sprites,ui,main}.js`(+ 2단계 `agents.js`). 논리 해상도 512×352(타일 16px)를 정수배 확대, 외부 이미지 없이 코드로 픽셀을 그림(`Sprites.draw.*` 만 교체하면 CC0 에셋으로 바꿀 수 있음). 격자·자리 확인은 G 키.
- [x] 5.5단계 마무리: 오피스 뷰를 기본 홈(`/`)으로 전환, 목록 뷰는 `/list`
- [x] 6단계(일부): 월마감 통합 테스트(`tests/test_integration.py`: 가나상사·다라테크), 코드 정리(ruff F 통과, 미사용 코드 제거), .gitignore, 법령 자료실 `/api/rules` + `docs/rules_check.md`
- [x] 7단계: README(맨 위 월마감 GIF·담당업무↔AI 직원 표·스크린샷 4종·수치 검증 과정·한계) + `docs/interview_notes.md`(면접용 핵심 로직 3가지). 이미지는 `docs/images/` (실제 서버+샘플 데이터로 캡처; 결과 엑셀 그림은 엑셀 형식 렌더링). 간이세액표 원본 엑셀은 `docs/sources/`. 개발 DB(`ai_office.db`)는 녹화 전에 비웠고 이전 것은 `outputs/ai_office_dev_backup_20261107.db`

## 확정된 결정 (2026-11 사용자 답변)

- 큐는 asyncio, 실시간은 SSE(끊기면 2초 폴링). 완료·오류·검토 이력은 SQLite에 영속(재시작 시 진행중 작업은 오류로 정리, 검토대기는 복원).
- 적용연도 2026 기준. rules는 `app/rules/<연도>/` 로 분리, 작업 실행 시 적용연도 선택. 불확실한 수치는 전부 `TODO: 확인 필요`.
- 간이세액표는 CSV 분리(헤더 주석에 출처·연도·확인일). 샘플 구간만 넣고 결과 엑셀 '계산근거' 시트에 "전체표 아님" 경고 표시.
- 샘플 고객사 2곳: (주)가나상사(제조, 직원 15명 내외, 사업소득자 1~2명), (주)다라테크(IT, 직원 8명 내외, 기타소득 건 포함). 오류·대사불일치 데이터를 일부러 섞는다.
- 북키 분개 추천은 규칙 기반. LLM은 `BaseAgent.llm_hook` 자리만(1차 미구현).
- 에이전트는 독립 구현하되 입출력 컬럼명(영어 snake_case, 공통 키 `client_id`/`period`/`account_code`/`amount`)을 처음부터 맞춘다.
- 검토 승인/반려: 담당자명·시각·코멘트를 DB(`reviews`)에 기록. 반려 시 사유 필수, 재작업(일하는중 복귀) 또는 오류 처리 선택. 에이전트 상세 페이지에 승인 이력.

## 개발 메모

- 실행: `.venv\Scripts\python.exe -m uvicorn app.main:app --reload` / 테스트: `.venv\Scripts\python.exe -m pytest`
- 큐에 넣거나 asyncio 타이머를 만드는 API 핸들러는 반드시 `async def` (sync def 는 스레드풀에서 돌아 이벤트 루프에 접근 못 함 — 승인 후 대기중 복귀 버그의 원인이었음).
- 1단계 BaseAgent 의 `validate/compute/...` 는 데모 구현(DEMO_SECONDS=10초). 2단계부터 에이전트별로 override. `run()` 은 규칙 4(작업 순서 고정)를 지키는 템플릿 메서드로 두고 단계 메서드만 override 한다.
- 에이전트 코드는 `self.set_status(ctx, ...)` / `self.log(ctx, ...)` 만 사용(→ DB·SSE·활동 로그 자동). 활동 로그 메시지는 자동으로 "(주)고객사 작업명 …" 형태가 된다.
- DB 스키마를 바꾸면 마이그레이션이 없으므로 개발 중에는 `ai_office.db` 를 지우고 다시 시작한다.
- 홈 카드는 구조가 바뀔 때만 다시 그린다(진행률 틱마다 innerHTML 교체하면 CSS 애니메이션이 재시작됨).
- 업로드 파일은 `uploads/<에이전트>/` 에 저장(git 제외). 페이는 최신 .xlsx 중 `정보` 시트의 고객사가 선택한 고객사와 맞는 첫 파일을 입력으로 쓰고, 없으면 `sample_data/payroll/<client_id>.xlsx`.
- 페이 입력 엑셀 시트: `정보`(귀속월·고객사) / `급여대장` / `소득자지급내역` / `전월급여`(선택). 샘플 재생성: `.venv\Scripts\python.exe sample_data\build_sample_payroll.py` (오류 시연 파일은 `sample_data/payroll/errors/` — 업로드하면 검증 단계에서 중단).
- 규칙 값은 `{value, source, year, verified, verified_on}` 형식, 로더는 `app/core/rules.py`(`Rules.v/d`). value 가 null 이면 `RuleMissing` → 작업 오류. 계산은 `Decimal` 로 하고 절사는 `trunc()`.
- 결과 엑셀의 '계산근거' 시트에 적용 규칙이 셀로 들어가고 다른 시트 수식이 그 셀을 참조한다(요율을 고치면 재계산). 소수 요율 절사는 `TRUNC(ROUND(x,6))` 로 감싼다(부동소수 오차 방지). 수식 결과는 `formulas` 라이브러리로 파이썬 계산과 셀 단위 비교 테스트.
- 테스트의 요율은 계산하기 쉬운 '테스트 전용 가정값'(5%, 3% …)이며 법령 값이 아니다. 실제 2026 규칙값은 전부 검증 전.
- 서버를 `--reload` 없이 띄웠다면 코드 수정 후 재시작해야 반영된다(미리보기 서버 포함).

- 북키 입력: 파일 이름에 역할 키워드(은행/카드/계정/전기/시차)가 든 CSV·xlsx 를 업로드(최신 우선, 다른 고객사 이름이 든 파일은 제외), 없으면 `sample_data/bookkeeping/<client_id>/{bank,card,accounts,opening,lag_items}.csv`. 샘플 재생성: `.venv\Scripts\python.exe sample_data\build_sample_bookkeeping.py`, 오류 시연: `sample_data/bookkeeping/errors/전기말잔액_차대불일치.csv` 업로드.
- 북키 상태 구분: 입력 오류·시산표 차대 불일치 → 🔴 오류(결과 파일 없음) / 신뢰도 낮은 분개·은행 원인불명 차이·BS 플래그 → 🟡 검토필요(이상항목에 표시, 원인불명 차이는 severity=error).
- 시차 항목(`lag_items.csv`)은 '장부에는 반영, 은행에는 미반영' 거래이며 분개에 포함되고 은행 잔액 조정에 쓰인다. 원인불명 차이 = 기초 잔액 차이 + 은행 내역 잔액 불연속 합계.
- 결산 체크리스트·PBC 는 DB `work_items` 테이블(kind=checklist/pbc)에 저장하고 `GET /api/tasks/{id}/items`, `PUT /api/items/{id}` 로 관리한다. 템플릿은 `rules/<연도>/closing_checklist.yaml`, `pbc_template.yaml`, 계정 추천 규칙은 `account_mapping.yaml`(법령 아닌 사무실 규칙).
- `formulas` 라이브러리는 시트 이름의 영문자를 대문자로 보관한다(테스트에서 `sheet.upper()` 로 조회).

- 배트 입력: 파일 이름에 역할 키워드(카드/장부/매출/매입 — 이 순서로 판별)가 든 CSV·xlsx 업로드(최신 우선, 다른 고객사 이름이 든 파일 제외), 없으면 `sample_data/vat/<client_id>/{sales_invoices,purchase_invoices,ledger,card_purchases}.csv`(카드는 선택). 샘플 재생성: `.venv\Scripts\python.exe sample_data\build_sample_vat.py`, 오류 시연은 `sample_data/vat/errors/매출세금계산서_오류시연.csv` 업로드. 과세기간은 세금계산서 작성일의 분기로 추정(샘플은 2026년 2기 예정 7~9월).
- 배트 대사: 1차 사업자번호+공급가액+작성일 같음(세액 다르면 금액차이) → 2차 사업자번호+작성일 같고 공급가액만 다름(금액차이) → 남은 세금계산서=장부누락, 남은 장부=세금계산서누락(매출은 미발행 의심, 매입은 미수취). 날짜만 다른 같은 금액은 두 건으로 나오되 비고에 참고 안내.
- 배트 불공제 후보는 키워드 단서(rules/vat.yaml)일 뿐 확정이 아니다. 엑셀 '불공제 후보' 시트의 판단 칸(확인필요/불공제/공제)을 담당자가 바꾸면 신고서 요약이 수식으로 즉시 다시 계산된다(공제가 아닌 후보는 불공제로 계산).
- 가산세 위험액은 공급가액×율(엑셀 ROUND, 파이썬 `penalty_amount` 반올림)이며 발급기한(작성월 다음 달 10일)을 넘긴 건만 0이 아니다. 작업 params 의 `today`(ISO 날짜)로 기준일을 바꿔 테스트할 수 있다.

- 연동: 북키는 결과 엑셀 옆에 시산표 값을 `*.tb.json` 으로 저장하고(엑셀은 수식이라 값을 못 읽음), 코택은 같은 고객사·적용연도의 최근 북키 작업(검토 대기·승인 완료)의 JSON 을 이어받는다. 코택 입력 우선순위: 업로드한 시산표 파일 > 북키 결과 > `sample_data/corptax/<client_id>/trial_balance.csv`. 북키 시산표는 월 단위라 코택이 '월 단위 시산표' 경고를 남긴다.
- 매니저 월마감: `core/dispatch.py start_task()` 로 페이→북키→배트→코택 순서로 작업을 만들고 `review/done/error` 가 될 때까지 지켜본다(`MANAGER_POLL`). 북키가 오류면 배트·코택은 건너뜀. 같은 고객사의 검토 대기 작업은 이어받고, 다른 고객사 작업이 진행 중이면 건너뜀. 하위 작업 승인은 각 에이전트 화면에서 따로 한다. **배트는 아직 북키 결과가 아니라 별도 장부 파일과 대사한다(순서 의존만 연동).**
- 신고기한: `rules/<연도>/deadlines.yaml`(분류·home 표시·근거) + `holidays.yaml`. `core/deadlines.py` 가 토·일·공휴일 순연(연속 공휴일 포함)을 계산하고 홈 D-day 배지(`/api/deadlines`, home: true 항목)·캘린더(`/api/calendar`)·배트 신고기한이 같은 로직을 쓴다. 공휴일 파일이 없는 연도는 주말만 순연.
- 진행 현황: `core/progress.py matrix()` → `/api/progress`, 엑셀 `/api/progress.xlsx`. 홈 옵션 행의 기업 구분·수입금액은 `RunRequest.options` 로 전달돼 작업 params 에 들어간다(코택·매니저가 사용, 테스트에서는 `today` 도 전달 가능).
- 에이전트가 전부 실제 구현이라 BaseAgent 기본 흐름 테스트는 `tests/test_flow.py` 의 DemoAgent 스텁으로 확인한다.
- 코택 샘플 재생성: `.venv\Scripts\python.exe sample_data\build_sample_corptax.py` (오류 시연: `sample_data/corptax/errors/시산표_차대불일치.csv` 를 파일 이름에 '시산표' 가 들어가게 업로드).

- **기본 홈 = 오피스 뷰**: `/` 가 office.html, 기존 카드형 목록은 `/list`(index.html). 두 화면 위쪽의 [오피스 뷰 | 목록 뷰] 버튼으로 오간다. 창 너비 768px 미만이면 오피스 뷰가 `/list` 로 자동 이동(`?force=1` 로 막을 수 있음). `/static/office.html` 주소도 그대로 동작.
- 오피스 뷰는 파일을 직접 열면(file://) 동작하지 않는다 — 반드시 서버로 접속: `run_office.bat` 더블클릭(서버 + 브라우저) 또는 `http://localhost:8000/`. 파일로 열면 안내 배너가 뜬다. 서버는 `--reload` 없이 켰다면 main.py 를 고친 뒤 재시작해야 반영된다.

## rules 검증 현황 (2026-11-07 법령 MCP 검증 반영 — `docs/process/03_rules_verification_1st.md`)

전체 90개 중 69개 `verified: true`, **미확인 21개**(아래 TODO). 반영한 변경:
- 국민연금 기준소득월액 상·하한은 적용기간별(`insurance.national_pension.limits.h1_2026/h2_2026`, `applies: "YYYY-MM~YYYY-MM"`). 급여 계산·엑셀 수식·이상항목은 귀속월이 속한 기간 값을 쓴다(`payroll_calc.pension_limits`; 기간에 없는 월은 `RuleMissing` → 작업 오류). 2026.7월 이후 하한은 TODO(임시로 40만 유지).
- 장기요양은 건강보험료 × 비율이 아니라 **보수 × 0.4724%**(근로자·사업주 각각, 합계 0.9448%). 엑셀 수식도 보수월액(C열) 기준.
- 신고기한: 근로소득·기타소득(인적용역) 간이지급명세서는 매월 다음 달 말일, 기타소득 지급명세서는 다음 연도 2월 말일(`"02-말일"` 표기 지원). 순연은 토·일·공휴일·대체공휴일 + 노동절(5/1, `holidays.yaml > annual_extra`).
- 접대비 → **기업업무추진비**(화면·설명). 코드 식별자(`corptax.entertainment.*`, 항목 key)는 그대로. 시산표 계정은 `기업업무추진비`·`접대비` 두 이름을 모두 합산한다. 한도의 수입금액 구간은 누적 계산(테스트로 확인).
- 기타소득 과세최저한은 지급액이 아니라 기타소득금액(지급액 − 필요경비) 기준(코드·테스트 확인). 필요경비 60%는 일부 소득에만 해당하는데 코드는 기타소득 전부에 적용 → 엑셀 '계산근거'에 안내.
- 사무실 기준(대사 허용오차, BS 감시 계정, 장기 미정리 180일, 불공제 키워드, 템플릿 5종)은 `verified: true`, 근거 "사무실 내부 기준".

### 2차 검증 (2026-11-07, `docs/process/04_rules_verification_2nd.md`)

**당시 미확인(빨강) 0 / 외부 자료 반입 필요(주황) 9 / 확인됨 81 (전체 90)**. 반영: 국민연금 하한 2026.7~ = 410,000, 절사 단위 전부 확인(국민연금 기준소득월액 1,000원·보험료 10원, 지방소득세 10원), **사업·기타소득 원천징수 절사 1원 → 10원**(국고금 관리법 제47조①), 부가세 10%, 4대보험 납부기한(다음 달 10일), 보수총액신고(2026년 지급분 2027-03-15; 2027년부터 월 보수 신고로 변경 → rules/2027 만들 때 반영, deadlines.yaml `note`).
- 상태 분류: yaml 항목에 `external: "받을 곳 안내"` 가 있고 아직 verified 가 아니면 '외부 자료 반입 필요(주황)'. API `summary.external`, 항목의 `external`/`where`. 법령 MCP로 확인 불가였던 9개 중 간이세액표·자녀 공제 5개는 아래 반입으로 해소. 값을 받아 넣으면 `external` 줄을 지우고 `verified: true`.

### 간이세액표 반입 (2026-11-07)

홈택스 `docs/sources/근로소득 간이세액표_2026.03.01.xlsx`(원본 보관) → `scripts/import_simple_tax_table.py` 로 `rules/2026/simple_tax_table.csv` 변환(647개 구간, 월급여 770천원 이상 ~ 정확히 10,000천원, 헤더에 출처·시행일·반입일). 자녀 세액공제는 표 안내문 3 기준 20,830 / 45,830 / +33,330. 11명 초과 가족은 안내문 4 계산식으로 조회(`TaxTable.lookup`). **월급여 1천만원 초과는 표가 아니라 계산식이라 CSV에 없음 → '간이세액표 구간 없음' 이상항목**(미구현). 2026.03.01 시행분이라 귀속월이 2026-02 이전이면 다른 표가 적용된다(미처리). 자녀 공제는 8세 이상 20세 이하만 해당하지만 입력은 '20세 이하 자녀수' 전체를 쓴다(기존 한계).

현재 **미확인(빨강) 0 / 외부 자료 반입 필요(주황) 4 / 확인됨 86 (전체 90)**.

## 미해결 TODO (외부 자료 반입 대기)

주황 4개: 원천징수이행상황신고서 소득 코드 3개(국세청 작성요령 코드표), 2026 공휴일 목록(천문연구원 월력요항/공공데이터포털 특일 정보). 목록은 `docs/rules_check.md`.

(아래는 2단계 이전 임시값 설명 — 이미 확인된 항목은 위 현황이 우선한다.)
2단계에서 넣은 임시값(검증 필요): `insurance.yaml`(국민연금 요율·기준소득월액 상하한·절사단위, 건강보험·장기요양·고용보험 요율),
`payroll.yaml`(식대·자가운전보조금 비과세 한도, 최저임금·월 환산시간), `withholding.yaml`(지방소득세율, 자녀 세액공제, 사업소득·기타소득 세율·필요경비율·과세최저한),
`withholding.yaml > report_codes`(원천징수이행상황신고서 소득 코드 — null).
북키(`bookkeeping.yaml`): 장기 미정리 기준일(180일)·은행 대사 허용오차·감시 계정 목록은 임시 기준(verified: false). `account_mapping.yaml` 키워드와 체크리스트·PBC 템플릿은 샘플이라 사무실 절차에 맞게 보정 필요.
배트(`vat.yaml`): 세율, 발급기한(익월 10일), 가산세율(지연 1%·미발급 2%), 불공제 후보 키워드 4종, 금액 허용오차는 모두 임시값(verified: false). 신고기한은 deadlines.yaml(검증 전, 주말·공휴일 순연 미반영).
코택(`corptax.yaml`): 접대비 기본한도(중소 3,600만·일반 1,200만)·수입금액 구간 적용률, 인정이자율, 대손 설정률, 업무용승용차 한도는 임시값. `tax_adjustment_items.yaml` 근거 조문·확인 포인트, `audit_request_template.yaml` 도 샘플.
매니저(`deadlines.yaml`·`holidays.yaml`): 신고기한 13종(원천세·부가세·법인세·연말정산·지급명세서·4대보험)과 2026 공휴일 목록(대체공휴일 포함)은 기억 기반 임시값 — 법령 MCP·관공서 공휴일 규정으로 검증 필요.
`simple_tax_table.csv` 는 2026-11-07 홈택스 공식 표로 교체됨(위 "간이세액표 반입" 참고). 이전 시연용 임의값은 사라졌다.

예외: `rules/2026/deadlines.yaml` 은 홈 D-day 배지를 위해 일반적으로 알려진 기한(원천세 매월 10일, 부가세 확정 1/25·7/25, 예정 4/25·10/25, 법인세 12월결산 3/31)을 임시로 넣었다. 전부 `verified: false` 이며 화면에 "검증 전"으로 표시된다. 검증 후 `verified: true` + `verified_on` 기입. 주말·공휴일 순연은 코드에 미반영.

