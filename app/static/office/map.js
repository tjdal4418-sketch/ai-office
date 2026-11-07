// 오피스 뷰 — 맵과 가구 배치 (타일 좌표). 그리는 일은 sprites.js, 움직임은 agents.js 가 맡는다.
// 논리 해상도: 타일 16px × 32×22 = 512×352. 화면에는 정수배(보통 2배)로 확대해서 보여준다.
const TILE = 16, COLS = 32, ROWS = 22;
const FLOOR = { x0: 1, y0: 3, x1: 30, y1: 19 }; // 바닥(걸을 수 있는 영역) 타일 범위, 양끝 포함. 위쪽 벽 3줄, 아래쪽 벽 2줄

// 구역 (타일 단위) — 캐릭터가 어디서 쉬는지·모이는지 정할 때 쓴다
const ZONES = {
  pantry: { x: 24, y: 3, w: 7, h: 6, label: "탕비실" },
  lounge: { x: 17, y: 10, w: 14, h: 10, label: "라운지" },
  meeting: { x: 1, y: 4, w: 9, h: 7, label: "회의 테이블" },
};

// 에이전트 표시 정보 (이름·아이콘·머리색·옷색은 2단계 캐릭터에서 사용)
const AGENT_VIEW = {
  manager:     { name: "매니저", icon: "folder",   role: "월마감 배분·신고기한·진행 현황", hair: "#2b2b3a", outfit: "#e0a526", skin: "#f3c9a5", style: "short" },
  payroll:     { name: "페이",   icon: "coin",     role: "급여·4대보험·원천세",            hair: "#6b3f1d", outfit: "#e8607a", skin: "#f1c27d", style: "long" },
  bookkeeping: { name: "북키",   icon: "notebook", role: "분개·시산표·은행 대사·결산",     hair: "#1f1f1f", outfit: "#3d8bd9", skin: "#f3c9a5", style: "short" },
  vat:         { name: "배트",   icon: "receipt",  role: "세금계산서 대사·불공제 후보",    hair: "#c77d2e", outfit: "#47b86b", skin: "#e9b98f", style: "bob" },
  corptax:     { name: "코택",   icon: "building", role: "세무조정·기업업무추진비 한도",           hair: "#7c5cd6", outfit: "#8d96a8", skin: "#f3c9a5", style: "spiky" },
};
const AGENT_IDS = ["manager", "payroll", "bookkeeping", "vat", "corptax"];

// 가구. kind 는 sprites.js 의 그리기 함수 이름. wall:true 는 벽에 붙은 장식.
// solid:true 면 전체 칸이 막힘, block:[{x,y,w,h}] 면 그 칸만 막힘. action 은 3~4단계에서 연결할 클릭 동작.
const SHELF_TIP = "책장 — 클릭하면 법령 자료실(세율·요율 근거 rules 목록)";
const SOFA_TIP = "소파 — 대기 중인 직원이 쉬어요";
const FURNITURE = [
  // ---- 위쪽 벽 장식
  { id: "window", kind: "window", x: 1, y: 1, w: 4, h: 2, wall: true, tip: "창문 — 실제 시각에 따라 낮·저녁·밤 풍경이 바뀌어요" },
  { id: "whiteboard", kind: "whiteboard", x: 6, y: 1, w: 6, h: 2, wall: true, action: "calendar", tip: "화이트보드 — 클릭하면 신고기한 D-day 캘린더" },
  { id: "clock", kind: "clock", x: 15, y: 0, w: 1, h: 1, wall: true, tip: "시계" },
  { id: "shelf1", kind: "bookshelf", x: 19, y: 0, w: 2, h: 3, wall: true, action: "rules", tip: SHELF_TIP },
  { id: "shelf2", kind: "bookshelf", x: 21, y: 0, w: 2, h: 3, wall: true, action: "rules", tip: SHELF_TIP },
  { id: "shelf3", kind: "bookshelf", x: 23, y: 0, w: 2, h: 3, wall: true, action: "rules", tip: SHELF_TIP },
  { id: "tv", kind: "tv", x: 26, y: 1, w: 4, h: 2, wall: true, action: "log", tip: "TV — 클릭하면 실시간 활동 로그" },
  { id: "plant_top", kind: "plant", x: 11, y: 2, w: 1, h: 2, block: [{ x: 11, y: 3, w: 1, h: 1 }], tip: "화분" },

  // ---- 위쪽 가운데: 매니저 데스크 (유리 칸막이는 overlay 로 그린다)
  { id: "desk_manager", kind: "manager_desk", x: 13, y: 4, w: 6, h: 2, solid: true, agent: "manager", action: "agent", tip: "📁 매니저 자리 — 월마감 업무 배분·신고기한·진행 현황" },
  { id: "chair_manager", kind: "chair", x: 15, y: 3, w: 1, h: 1, facing: "down" },

  // ---- 왼쪽 위: 회의 테이블 + 의자 6개
  { id: "meeting_table", kind: "meeting_table", x: 2, y: 6, w: 7, h: 3, solid: true, tip: "회의 테이블 — 월마감을 시작하면 모두 여기로 모여요" },
  ...[3, 5, 7].map((x) => ({ id: `mchair_t${x}`, kind: "chair", x, y: 5, w: 1, h: 1, facing: "down" })),
  ...[3, 5, 7].map((x) => ({ id: `mchair_b${x}`, kind: "chair", x, y: 9, w: 1, h: 1, facing: "up" })),

  // ---- 왼쪽 중간~아래: 에이전트 책상 4개 (모니터 포함) + 의자
  { id: "desk_payroll", kind: "desk", x: 3, y: 11, w: 3, h: 2, solid: true, agent: "payroll", action: "agent", tip: "💰 페이 자리 — 급여·4대보험·원천세" },
  { id: "desk_bookkeeping", kind: "desk", x: 10, y: 11, w: 3, h: 2, solid: true, agent: "bookkeeping", action: "agent", tip: "📒 북키 자리 — 분개·시산표·은행 대사·결산" },
  { id: "desk_vat", kind: "desk", x: 3, y: 16, w: 3, h: 2, solid: true, agent: "vat", action: "agent", tip: "🧾 배트 자리 — 세금계산서 대사·불공제 후보" },
  { id: "desk_corptax", kind: "desk", x: 10, y: 16, w: 3, h: 2, solid: true, agent: "corptax", action: "agent", tip: "🏛️ 코택 자리 — 세무조정·기업업무추진비 한도" },
  { id: "chair_payroll", kind: "chair", x: 4, y: 13, w: 1, h: 1, facing: "up" },
  { id: "chair_bookkeeping", kind: "chair", x: 11, y: 13, w: 1, h: 1, facing: "up" },
  { id: "chair_vat", kind: "chair", x: 4, y: 18, w: 1, h: 1, facing: "up" },
  { id: "chair_corptax", kind: "chair", x: 11, y: 18, w: 1, h: 1, facing: "up" },

  // ---- 오른쪽 위: 탕비실(흰 타일) + 복합기
  { id: "coffee", kind: "coffee_counter", x: 25, y: 3, w: 2, h: 2, solid: true, tip: "커피머신 — 대기 중인 직원이 가끔 들러요 ☕" },
  { id: "water", kind: "water_cooler", x: 28, y: 3, w: 1, h: 2, solid: true, tip: "정수기" },
  { id: "copier", kind: "copier", x: 29, y: 3, w: 2, h: 2, solid: true, tip: "복합기 — 결과 파일이 나오면 종이가 출력돼요" },
  { id: "trash", kind: "trash", x: 30, y: 6, w: 1, h: 1, solid: true, tip: "휴지통" },

  // ---- 오른쪽 아래: 라운지(카펫, 소파, 원형 테이블, 화분)
  { id: "sofa_top", kind: "sofa", x: 19, y: 11, w: 6, h: 2, solid: true, facing: "down", tip: SOFA_TIP },
  { id: "sofa_bottom", kind: "sofa", x: 19, y: 17, w: 6, h: 2, solid: true, facing: "up", tip: SOFA_TIP },
  { id: "sofa_right", kind: "sofa", x: 27, y: 13, w: 2, h: 5, solid: true, facing: "left", tip: SOFA_TIP },
  { id: "glass_table", kind: "glass_table", x: 21, y: 14, w: 2, h: 2, solid: true, tip: "라운지 테이블" },
  { id: "plant_lounge1", kind: "plant", x: 29, y: 10, w: 1, h: 2, block: [{ x: 29, y: 11, w: 1, h: 1 }], tip: "화분" },
  { id: "plant_lounge2", kind: "plant", x: 29, y: 18, w: 1, h: 2, block: [{ x: 29, y: 19, w: 1, h: 1 }], tip: "화분" },

  // ---- 아래쪽: 출입문 + 매트 + 옷걸이 (장식)
  { id: "door", kind: "door", x: 14, y: 20, w: 4, h: 2, wall: true, tip: "출입문" },
  { id: "mat", kind: "mat", x: 14, y: 19, w: 4, h: 1, tip: "현관 매트" },
  { id: "coat_rack", kind: "coat_rack", x: 18, y: 18, w: 1, h: 2, block: [{ x: 18, y: 19, w: 1, h: 1 }], tip: "옷걸이" },
];

// 자리·집결지 (타일 좌표). sit:true 는 가구 위에 앉는 자리라 길찾기 목표로만 허용한다.
const SEATS = {
  desk: {            // 일할 때 앉는 자리 (책상 앞 의자, 책상 쪽을 본다)
    manager:     { x: 15, y: 3, facing: "down" },
    payroll:     { x: 4, y: 13, facing: "up" },
    bookkeeping: { x: 11, y: 13, facing: "up" },
    vat:         { x: 4, y: 18, facing: "up" },
    corptax:     { x: 11, y: 18, facing: "up" },
  },
  meeting: [         // 월마감 때 모이는 회의 테이블 의자 6개
    { x: 3, y: 5, facing: "down" }, { x: 5, y: 5, facing: "down" }, { x: 7, y: 5, facing: "down" },
    { x: 3, y: 9, facing: "up" }, { x: 5, y: 9, facing: "up" }, { x: 7, y: 9, facing: "up" },
  ],
  lounge: [          // 쉬는 자리: 소파(앉기) 6곳
    { x: 20, y: 12, facing: "down", sit: true }, { x: 22, y: 12, facing: "down", sit: true }, { x: 24, y: 12, facing: "down", sit: true },
    { x: 20, y: 17, facing: "up", sit: true }, { x: 23, y: 17, facing: "up", sit: true }, { x: 27, y: 15, facing: "left", sit: true },
  ],
  loungeWalk: [{ x: 18, y: 14 }, { x: 19, y: 16 }, { x: 24, y: 15 }, { x: 25, y: 13 }, { x: 26, y: 16 }],
  pantryWalk: [{ x: 26, y: 7 }, { x: 28, y: 6 }, { x: 25, y: 6 }, { x: 29, y: 7 }],
  coffee: { x: 26, y: 5, facing: "up" },     // 커피머신 앞
  printer: { x: 29, y: 5, facing: "up" },    // 복합기 앞 (결과 파일 받는 곳)
  printerSpots: [{ x: 29, y: 5, facing: "up" }, { x: 30, y: 5, facing: "up" }, { x: 29, y: 6, facing: "up" }], // 여럿이 와도 줄 서도록 자리 3곳
  door: { x: 15, y: 18 },
  // 검토필요일 때 서서 기다리는 자리: 매니저 데스크 앞 (매니저는 자기 자리에서 기다림)
  review: { payroll: { x: 12, y: 6 }, bookkeeping: { x: 14, y: 6 }, vat: { x: 16, y: 6 }, corptax: { x: 18, y: 6 } },  // 이름표가 겹치지 않게 2칸 간격
  // ---- 대기중 행동 자리 (선 채로 무언가를 하는 곳. facing = 바라보는 방향)
  water: { x: 28, y: 5, facing: "up" },                                       // 정수기 앞
  plants: [{ x: 11, y: 4, facing: "up" }, { x: 29, y: 12, facing: "up" }, { x: 28, y: 19, facing: "right" }], // 화분 앞
  windowSpots: [{ x: 2, y: 3, facing: "up" }, { x: 3, y: 3, facing: "up" }, { x: 4, y: 3, facing: "up" }],     // 창문 앞
  shelf: [{ x: 20, y: 3, facing: "up" }, { x: 22, y: 3, facing: "up" }],                                      // 책장 앞
};

// 걸을 수 있는 칸 격자 (true = 막힘). 길찾기(2단계)가 이 격자를 쓴다.
function buildGrid() {
  const g = Array.from({ length: ROWS }, () => Array(COLS).fill(false));
  for (let y = 0; y < ROWS; y++) {
    for (let x = 0; x < COLS; x++) {
      if (x < FLOOR.x0 || x > FLOOR.x1 || y < FLOOR.y0 || y > FLOOR.y1) g[y][x] = true; // 벽
    }
  }
  const mark = (r) => { for (let y = r.y; y < r.y + r.h; y++) for (let x = r.x; x < r.x + r.w; x++) if (g[y]) g[y][x] = true; };
  for (const f of FURNITURE) {
    if (f.wall) continue;
    if (f.block) f.block.forEach(mark);
    else if (f.solid) mark(f);
  }
  return g;
}
const GRID = buildGrid();
const isBlocked = (x, y) => y < 0 || y >= ROWS || x < 0 || x >= COLS || GRID[y][x];
