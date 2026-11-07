// 오피스 뷰 — 캐릭터 5명: 걷기(A* 길찾기), 서로 피하기, 대기중 행동, 잡담, 그리고 상태별 연출
//  🟢 idle 라운지·탕비실 / 🔵 working 책상+모니터+진행률 / 🟡 review 매니저 데스크 앞 "!" / ✅ done 복합기→라운지 / 🔴 error 빨간 모니터
// 상태는 applyState(상태 객체)로 들어온다 — 실제로는 SSE(/api/events), 데모 모드에서는 가짜 상태.
const Agents = (() => {
  const SPEED = 2.8;                       // 걷는 속도: 타일/초
  const DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1]];
  const key = (x, y) => x + "," + y;
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
  const shuffle = (arr) => { const a = [...arr]; for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };

  const occ = new Map();     // 칸 → 캐릭터: 서 있는 칸 + 이동 목적 칸 (같은 칸에 겹치지 않게)
  const claims = new Map();  // 칸 → 캐릭터: 목적지 예약 (소파 자리·정수기 앞 등)
  const agents = [];
  let chats = [];
  let clock = 0, chatCheck = 0, started = false;
  const START = [{ x: 15, y: 19 }, { x: 16, y: 19 }, { x: 14, y: 19 }, { x: 17, y: 19 }, { x: 15, y: 18 }]; // 출입문 앞에서 등장
  const onChange = [];                     // 상태가 바뀔 때 알려 줄 함수들 (접근성 목록 등)

  // ---------- 행동 정의 ----------
  const IDLE = [ // 대기중: 하나당 5~15초 유지
    { id: "water", label: "정수기에서 물 마시기", spots: () => [SEATS.water], pose: "drink", hold: [5, 8], bubble: "drop" },
    { id: "coffee", label: "커피머신 앞에 서 있기", spots: () => [SEATS.coffee], pose: "mug", hold: [6, 12], bubble: "cup" },
    { id: "sofa", label: "소파에 앉기", spots: () => SEATS.lounge, pose: "stand", sit: true, hold: [10, 15] },
    { id: "plant", label: "화분 보기", spots: () => SEATS.plants, pose: "look", hold: [5, 9], bubble: "leaf" },
    { id: "window", label: "창밖 보기", spots: () => SEATS.windowSpots, pose: "look", hold: [6, 12] },
    { id: "shelf", label: "책장에서 책 꺼내 보기", spots: () => SEATS.shelf, pose: "book", hold: [7, 14], bubble: "book" },
    { id: "wander", label: "사무실 둘러보기", spots: () => [...SEATS.loungeWalk, ...SEATS.pantryWalk], pose: "stand", hold: [5, 8], weight: 0.5 },
    { id: "visit", label: "동료에게 다가가기", special: true, weight: 0.8 },
  ];
  const WORK = { id: "work", label: "일하는 중", pose: "type", sit: true };
  const REVIEW_SEATED = { id: "review", label: "검토 승인 대기", pose: "stand", sit: true };   // 매니저: 자기 자리에서
  const REVIEW = { id: "review", label: "검토 승인 대기", pose: "stand" };                    // 나머지: 매니저 데스크 앞에서
  const ERR = { id: "error", label: "오류 — 확인 필요", pose: "stand", sit: true };
  const PRINT = { id: "print", label: "복합기에서 출력물 가져오기", pose: "stand", hold: [4, 6], bubble: "paper" };
  const PRINTOUT = { id: "printout", label: "결과 파일 출력", pose: "stand", hold: [3.4, 3.4], bubble: "paper" };
  const REST = { id: "rest", label: "완료 — 쉬는 중", pose: "stand" };
  const MEETING = { id: "meeting", label: "월마감 회의", pose: "stand", sit: true, hold: [7, 9] };
  const MEETING_LINES = ["네!", "알겠습니다", "준비됐어요", "좋아요!", "바로 시작할게요"];

  const CHATS = [ // 가까이 있는 대기중 두 명이 마주 보고 주고받는 말
    ["오늘 원천세 마감이죠?", "네, 10일까지예요!"],
    ["부가세 대사 끝났어요~", "오, 수고했어요!"],
    ["시산표 차대 맞췄어요?", "네, 딱 맞았어요!"],
    ["기업업무추진비 한도 넘은 건 없어요?", "확인필요로 올려뒀어요."],
    ["커피 한잔 할래요?", "좋아요, 가요!"],
    ["가지급금 정리해야 하는데…", "코택이랑 같이 봐요."],
    ["신고기한 캘린더 확인했어요?", "네, 이번 주는 여유 있어요."],
    ["요율은 법령 MCP로 검증해야죠", "맞아요, 검증 전 표시 잊지 말기!"],
  ];

  // ---------- 길찾기 (A*, 4방향) ----------
  function findPath(sx, sy, gx, gy, dyn) {
    if (sx === gx && sy === gy) return [];
    const startK = key(sx, sy), goalK = key(gx, gy);
    const g = new Map([[startK, 0]]), parent = new Map(), closed = new Set();
    const open = [{ x: sx, y: sy, f: Math.abs(sx - gx) + Math.abs(sy - gy) }];
    while (open.length) {
      let bi = 0;
      for (let i = 1; i < open.length; i++) if (open[i].f < open[bi].f) bi = i;
      const cur = open.splice(bi, 1)[0], ck = key(cur.x, cur.y);
      if (ck === goalK) {
        const path = [];
        for (let k = ck; k !== startK; k = parent.get(k)) path.push(k.split(",").map(Number));
        return path.reverse();
      }
      if (closed.has(ck)) continue;
      closed.add(ck);
      for (const [dx, dy] of DIRS) {
        const nx = cur.x + dx, ny = cur.y + dy, nk = key(nx, ny);
        if (closed.has(nk)) continue;
        if (nk !== goalK && (isBlocked(nx, ny) || (dyn && dyn.has(nk)))) continue; // 목적지는 막힌 칸(소파)이어도 허용
        if (nx < 0 || ny < 0 || nx >= COLS || ny >= ROWS) continue;
        const ng = g.get(ck) + 1;
        if (ng < (g.get(nk) ?? Infinity)) { g.set(nk, ng); parent.set(nk, ck); open.push({ x: nx, y: ny, f: ng + Math.abs(nx - gx) + Math.abs(ny - gy) }); }
      }
    }
    return null;
  }

  const dirToward = (ax, ay, bx, by) => {
    const dx = bx - ax, dy = by - ay;
    return Math.abs(dx) >= Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up");
  };

  class Agent {
    constructor(id, index) {
      this.id = id; this.index = index; this.view = AGENT_VIEW[id]; this.name = this.view.name;
      const s = START[index % START.length];
      this.tx = s.x; this.ty = s.y; this.px = s.x * TILE + 8; this.py = s.y * TILE + 14;
      occ.set(key(s.x, s.y), this);
      this.dir = "up"; this.status = "idle"; this.mode = "wait"; this.timer = Infinity; // start() 가 불리기 전에는 출입문 앞에서 대기
      this.state = { status: "idle", progress: 0, current_step: "", error_reason: null, client_name: "" };
      this.path = []; this.seg = null; this.goal = null; this.blocked = 0; this.lastReplan = 0;
      this.beh = null; this.last = null; this.seated = false; this.carry = null; this.sub = null;
      this.bubble = null; this.claim = null; this.chat = null; this.chatCd = 0; this.walkT = 0; this.holdT = 0; this.holdFor = 0;
      this.pending = false; this.resume = false; this.afterHold = null; this.afterSeg = null; this.extraT = 0; this.midBubble = false;
    }

    // ----- 상태 -----
    applyState(st) { // 서버(또는 데모)가 보낸 상태 객체를 받는다. 진행률·단계 문구는 저장만 하고 상태가 바뀔 때만 행동을 바꾼다.
      const before = this.status;
      this.state = { ...this.state, ...st };
      this.setStatus(st.status);
      return before !== this.status;
    }
    setStatus(status) {
      if (status === this.status) return;
      const wasDone = this.status === "done";
      this.status = status;
      // 완료(10초)가 끝나 대기중이 되어도, 복합기로 가는 중이거나 출력 중이면 마저 끝낸다(출력 → ✔ → 대기 행동)
      if (started && wasDone && status === "idle" && this.beh === PRINTOUT && (this.mode === "walk" || this.mode === "hold")) {
        return; // 이미 걷거나 출력 중인 행동의 콜백(afterHold)이 ✔ 를 띄우고 다음 행동을 정한다
      }
      endChat(this);
      this.releaseClaim(); this.beh = null; this.afterHold = null; this.sub = null; this.resume = false; this.bubble = null;
      if (!started) return;                       // 아직 입장 전이면 start() 때 정함
      if (this.seg) this.pending = true;          // 걷던 중이면 이번 칸을 마저 걷고 새 행동 시작
      else { this.path = []; this.goal = null; this.decide(); }
    }
    atOwnDesk() {
      const d = SEATS.desk[this.id];
      return this.mode === "hold" && this.seated && this.tx === d.x && this.ty === d.y;
    }

    decide() {
      this.mode = "wait"; this.timer = 0; this.blocked = 0;
      this.releaseClaim();
      switch (this.status) {
        case "working": return this.goDesk(WORK);
        case "review": return this.id === "manager" ? this.goDesk(REVIEW_SEATED) : this.goSlot();
        case "error": return this.goDesk(ERR);
        case "done": return this.goPrinter();
        default: return this.pickIdle();
      }
    }

    // ----- 이동 -----
    othersTiles(exceptKey) {
      const s = new Set();
      for (const [k, a] of occ) if (a !== this && k !== exceptKey) s.add(k);
      return s;
    }
    goTo(x, y, cb) {
      this.goal = { x, y, cb };
      let path = findPath(this.tx, this.ty, x, y, this.othersTiles(key(x, y)));
      if (!path) path = findPath(this.tx, this.ty, x, y, null);
      if (!path) { this.goal = null; this.mode = "wait"; this.timer = 2; return; } // 길이 없으면 잠시 후 다시 정함
      this.path = path; this.mode = "walk"; this.blocked = 0;
      if (path.length) this.seated = false;
      else this.arrive();
    }
    arrive() {
      if (this.resume) { this.resume = false; const g = this.goal; if (g) this.goTo(g.x, g.y, g.cb); return; }
      const cb = this.goal && this.goal.cb; this.goal = null;
      if (cb) cb();
    }
    moveStep(dt) {
      if (!this.seg) {
        if (!this.path.length) { this.arrive(); return; }
        const [nx, ny] = this.path[0], other = occ.get(key(nx, ny));
        if (other && other !== this) { this.handleBlocked(dt); return; }
        this.blocked = 0;
        occ.set(key(nx, ny), this);
        this.seg = { fx: this.px, fy: this.py, tx: nx * TILE + 8, ty: ny * TILE + 14, nx, ny, p: 0 };
        this.dir = dirToward(this.tx, this.ty, nx, ny);
        this.path.shift();
      }
      const s = this.seg;
      s.p = Math.min(1, s.p + dt * SPEED);
      this.px = s.fx + (s.tx - s.fx) * s.p; this.py = s.fy + (s.ty - s.fy) * s.p;
      this.walkT += dt;
      if (s.p >= 1) {
        const old = key(this.tx, this.ty);
        if (occ.get(old) === this) occ.delete(old);
        this.tx = s.nx; this.ty = s.ny; this.seg = null;
        if (this.afterSeg) { const f = this.afterSeg; this.afterSeg = null; this.path = []; this.goal = null; this.resume = false; f(); return; }
        if (this.pending) { this.pending = false; this.path = []; this.goal = null; this.resume = false; this.decide(); }
      }
    }
    handleBlocked(dt) { // 다른 캐릭터가 길을 막고 있을 때: 잠깐 기다림 → 돌아가는 길 → 옆으로 비켜서기 → 포기
      this.blocked += dt;
      const g = this.goal;
      if (!g) return;
      if (this.blocked > 0.45 && clock - this.lastReplan > 0.6) {
        this.lastReplan = clock;
        const np = findPath(this.tx, this.ty, g.x, g.y, this.othersTiles(key(g.x, g.y)));
        if (np && np.length) this.path = np;
      }
      if (this.blocked > 1.6) {
        const free = DIRS.map(([dx, dy]) => ({ x: this.tx + dx, y: this.ty + dy })).filter((p) => !isBlocked(p.x, p.y) && !occ.has(key(p.x, p.y)));
        if (free.length) { const f = pick(free); this.path = [[f.x, f.y]]; this.resume = true; this.blocked = 0; } // path 항목은 [x, y] 배열
      }
      if (this.blocked > 6) { this.path = []; this.goal = null; this.resume = false; this.decide(); }
    }

    // ----- 자리 예약 -----
    releaseClaim() { if (this.claim) { if (claims.get(this.claim) === this) claims.delete(this.claim); this.claim = null; } }
    claimSpot(spot) { this.releaseClaim(); this.claim = key(spot.x, spot.y); claims.set(this.claim, this); }
    pickFreeSpot(spots) {
      return shuffle(spots).find((s) => {
        const k = key(s.x, s.y), o = occ.get(k), c = claims.get(k);
        return (!o || o === this) && (!c || c === this);
      });
    }

    // ----- 대기중 행동 고르기 -----
    pickIdle() {
      const others = agents.filter((a) => a !== this && a.status === "idle" && (a.mode === "hold" || a.mode === "walk"));
      const opts = IDLE.filter((b) => b.id !== this.last && (b.id !== "visit" || others.length));
      const order = [];
      const pool = [...opts];
      while (pool.length) { // 가중치 랜덤으로 순서를 정해 앞에서부터 자리가 비어 있는 행동을 고른다
        const total = pool.reduce((t, b) => t + (b.weight ?? 1), 0);
        let r = Math.random() * total, i = 0;
        for (; i < pool.length - 1; i++) { r -= pool[i].weight ?? 1; if (r <= 0) break; }
        order.push(pool.splice(i, 1)[0]);
      }
      for (const b of order) {
        if (b.id === "visit") { if (this.visit(pick(others))) return; continue; }
        const spot = this.pickFreeSpot(b.spots());
        if (spot) { this.startBehavior(b, spot); return; }
      }
      this.mode = "wait"; this.timer = 1.5; // 갈 곳이 없으면 잠깐 서 있다가 다시
    }
    startBehavior(b, spot) {
      this.last = b.id; this.beh = b; this.claimSpot(spot);
      this.goTo(spot.x, spot.y, () => this.startHold(b, spot));
    }
    visit(other) { // 다른 대기중 동료 가까이로 가서 말을 걸 준비
      const cand = shuffle([[2, 0], [-2, 0], [0, 2], [0, -2], [1, 1], [-1, 1], [1, -1], [-1, -1]])
        .map(([dx, dy]) => ({ x: other.tx + dx, y: other.ty + dy }))
        .filter((p) => !isBlocked(p.x, p.y) && !occ.has(key(p.x, p.y)) && !claims.has(key(p.x, p.y)));
      for (const p of cand) {
        if (!findPath(this.tx, this.ty, p.x, p.y, null)) continue;
        const b = { id: "visit", label: "동료에게 다가가기", pose: "stand", hold: [5, 8] };
        this.last = "visit"; this.beh = b; this.claimSpot(p);
        this.goTo(p.x, p.y, () => this.startHold(b, { x: p.x, y: p.y, facing: dirToward(p.x, p.y, other.tx, other.ty) }));
        return true;
      }
      return false;
    }

    // ----- 상태별 목적지 -----
    goDesk(beh) { // 자기 책상(일하는중·오류·매니저의 검토 대기)
      const seat = SEATS.desk[this.id];
      this.beh = beh;
      this.goTo(seat.x, seat.y, () => this.startHold(beh, seat));
    }
    goSlot() { // 검토필요: 매니저 데스크 앞에 서서 기다림
      const slot = SEATS.review[this.id];
      this.beh = REVIEW;
      this.claimSpot(slot);
      this.goTo(slot.x, slot.y, () => this.startHold(REVIEW, { ...slot, facing: "up" }));
    }
    goPrinter() { // 완료: 복합기에서 결과가 출력 → ✔ → 라운지로
      const spot = this.pickFreeSpot(SEATS.printerSpots) || SEATS.printer;
      this.beh = PRINTOUT;
      this.claimSpot(spot);
      this.goTo(spot.x, spot.y, () => {
        this.startHold(PRINTOUT, spot);
        this.afterHold = () => {
          this.releaseClaim();
          this.say({ icon: "check", bg: "#d9f7e3", border: "#2a9d5c" }, 2.4);
          if (this.status !== "done") { this.decide(); return; }
          const rest = this.pickFreeSpot(SEATS.loungeWalk);
          if (!rest) { this.startHold(REST, spot); return; }
          this.beh = REST; this.claimSpot(rest);
          this.goTo(rest.x, rest.y, () => this.startHold(REST, { ...rest, facing: "down" }));
        };
      });
    }
    gather(seat) { // 월마감 시작: 회의 테이블로 모였다가 흩어짐
      this.beh = MEETING; this.claimSpot(seat);
      this.goTo(seat.x, seat.y, () => {
        this.startHold(MEETING, seat);
        this.say({ text: pick(MEETING_LINES) }, 2.6);
        this.afterHold = () => { this.releaseClaim(); this.decide(); };
      });
    }

    // ----- 도착 후 머무르기 -----
    startHold(b, spot) {
      this.beh = b; this.mode = "hold"; this.holdT = 0; this.blocked = 0; this.midBubble = false;
      this.dir = spot.facing || this.dir; this.seated = !!b.sit;
      this.px = this.tx * TILE + 8; this.py = this.ty * TILE + (this.seated ? 15 : 14);
      if (b.id === "sofa" && spot.facing === "left") this.px -= 2;
      this.holdFor = b.hold ? rand(b.hold[0], b.hold[1]) : Infinity;
      if (b.bubble && b.id !== "printout") this.say({ icon: b.bubble }, 2.6);
      if (b.id === "work") { this.carry = null; this.extraT = rand(14, 30); }
    }
    holdUpdate(dt) {
      this.holdT += dt;
      const b = this.beh;
      if (!b) { this.decide(); return; }
      if (b.id === "work") { // 일하는 중 가끔: 기지개 / 복합기에 출력물 가지러 가기
        this.extraT -= dt;
        if (this.extraT <= 0 && !(this.sub && clock < this.sub.until)) {
          this.extraT = rand(18, 36);
          if (Math.random() < 0.55) this.sub = { pose: "stretch", until: clock + 3.2 };
          else this.printTrip();
        }
        return;
      }
      if (b.bubble && !this.midBubble && b.id !== "printout" && this.holdT > this.holdFor * 0.55) { this.midBubble = true; if (b.id !== "water") this.say({ icon: b.bubble }, 2.2); }
      if (this.holdT >= this.holdFor) {
        const f = this.afterHold; this.afterHold = null;
        if (f) f(); else this.decide();
      }
    }
    printTrip() {
      const spot = this.pickFreeSpot(SEATS.printerSpots);
      if (!spot) return; // 복합기 앞이 다 차 있으면 이번엔 건너뜀
      this.claimSpot(spot);
      this.goTo(spot.x, spot.y, () => {
        this.startHold(PRINT, spot);
        this.afterHold = () => { this.releaseClaim(); if (this.status === "working") { this.carry = "paper"; this.goDesk(WORK); } else this.decide(); };
      });
    }

    say(spec, secs) { this.bubble = { spec, until: clock + secs }; }
    stateBubble() { // 상태에 따라 머리 위(또는 모니터 위)에 계속 떠 있는 말풍선
      const s = this.state;
      const plain = (t) => (t || "").replace(/[\u{1F300}-\u{1FAFF}☀-➿️]/gu, "").replace(/\s+/g, " ").trim(); // 이모지는 픽셀 글자와 안 어울려서 뺀다
      if (this.status === "working") return { text: plain(s.current_step) || "작업 중", progress: s.progress, maxW: 92 };
      if (this.status === "review") return { text: "!", bg: "#ffe27a", border: "#8a6d00", color: "#5a3d00", bounce: true };
      if (this.status === "error") return { text: "! " + (s.error_reason || "오류"), bg: "#ffd9d6", border: "#b91c1c", color: "#7a1313", bounce: true, maxW: 92 };
      return null;
    }
    monitorMode() { return { working: "on", review: "review", error: "error" }[this.status] || "off"; }
    desc() {
      const s = this.state;
      if (this.mode === "chat") return "동료와 잡담 중";
      if (this.status === "working") return `일하는중 ${s.progress ?? 0}% — ${s.current_step || ""}`;
      if (this.status === "review") return "검토 승인 대기";
      if (this.status === "error") return `오류 — ${s.error_reason || ""}`;
      if (this.status === "done") return "완료";
      const b = this.beh;
      return b ? b.label : "대기중";
    }
  }

  // ---------- 잡담 ----------
  function startChat(a, b) {
    const lines = pick(CHATS), chat = { a, b, lines, t: 0, step: -1 };
    a.mode = b.mode = "chat"; a.chat = b.chat = chat;
    if (!a.seated) a.dir = dirToward(a.tx, a.ty, b.tx, b.ty);
    if (!b.seated) b.dir = dirToward(b.tx, b.ty, a.tx, a.ty);
    chats.push(chat);
  }
  function endChat(agent) {
    const c = agent.chat;
    if (!c) return;
    chats = chats.filter((x) => x !== c);
    for (const m of [c.a, c.b]) { m.chat = null; m.chatCd = clock + 30; if (m !== agent) { m.bubble = null; m.decide(); } }
  }
  function updateChats(dt) {
    for (const c of [...chats]) {
      c.t += dt;
      const step = c.t < 3.3 ? 0 : c.t < 6.6 ? 1 : 2;
      if (step !== c.step && step < 2) { c.step = step; (step === 0 ? c.a : c.b).say({ text: c.lines[step] }, 3.0); }
      if (step === 2) {
        chats = chats.filter((x) => x !== c);
        for (const m of [c.a, c.b]) { m.chat = null; m.chatCd = clock + 30; m.decide(); }
      }
    }
    chatCheck -= dt;
    if (chatCheck > 0) return;
    chatCheck = 0.4;
    const idle = agents.filter((a) => a.status === "idle" && a.mode === "hold" && !a.seg && clock > a.chatCd && a.beh);
    for (let i = 0; i < idle.length; i++) for (let j = i + 1; j < idle.length; j++) {
      const a = idle[i], b = idle[j];
      if (a.mode !== "hold" || b.mode !== "hold") continue;
      if (Math.hypot(a.tx - b.tx, a.ty - b.ty) <= 3.2) startChat(a, b);
    }
  }

  // ---------- 공개 함수 ----------
  function init() { AGENT_IDS.forEach((id, i) => agents.push(new Agent(id, i))); }
  function start() { // 첫 상태를 받은 뒤(또는 일정 시간 후) 한 명씩 입장
    if (started) return;
    started = true;
    agents.forEach((a, i) => { a.mode = "wait"; a.timer = 0.3 + i * 0.9; });
  }
  function applyState(id, st) {
    const a = agents.find((x) => x.id === id);
    if (!a) return;
    const was = a.status;
    const changed = a.applyState(st);
    if (id === "manager" && changed && st.status === "working" && was !== "working") startMonthClose();
    onChange.forEach((f) => f(a, changed));
  }
  function startMonthClose() { // 매니저가 월마감을 시작하면 대기 중인 직원들이 회의 테이블로 모인다
    const mgr = agents.find((a) => a.id === "manager");
    mgr.say({ text: "월마감 시작합니다!" }, 3.2);
    const seats = shuffle(SEATS.meeting);
    agents.filter((a) => a.id !== "manager" && a.status === "idle" && a.mode !== "chat").forEach((a) => {
      const seat = seats.pop();
      if (!seat || (claims.has(key(seat.x, seat.y)) && claims.get(key(seat.x, seat.y)) !== a)) return;
      a.afterHold = null; a.resume = false;
      if (a.seg) a.afterSeg = () => a.gather(seat);          // 걷던 칸을 마저 걷고 회의 테이블로
      else { a.path = []; a.goal = null; a.gather(seat); }
    });
  }
  function update(dt) {
    dt = Math.min(dt, 0.1);
    clock += dt;
    for (const a of agents) {
      if (a.bubble && clock > a.bubble.until) a.bubble = null;
      if (a.mode === "wait") { a.timer -= dt; if (a.timer <= 0) a.decide(); }
      else if (a.mode === "walk") a.moveStep(dt);
      else if (a.mode === "hold") a.holdUpdate(dt);
    }
    updateChats(dt);
  }
  function drawCharacters(c) { // 논리 해상도 캔버스에 그린다 (아래쪽 캐릭터가 앞에 오도록 y 순서)
    for (const a of [...agents].sort((p, q) => p.py - q.py)) {
      let pose = "stand", frame = 0;
      if (a.mode === "walk" && a.seg) { pose = "walk"; frame = 1 + (Math.floor(a.walkT * 7) % 2); }
      else if (a.mode === "hold" && a.beh) pose = a.sub && clock < a.sub.until ? a.sub.pose : a.beh.pose;
      Sprites.character(c, Math.round(a.px), Math.round(a.py), {
        dir: a.dir, frame, pose, seated: a.seated && !(a.mode === "walk" && a.seg), t: clock, carry: a.carry,
        hair: a.view.hair, outfit: a.view.outfit, skin: a.view.skin, style: a.view.style,
      });
    }
  }
  function drawEffects(c) { // 논리 해상도: 모니터 화면(상태별)과 복합기 종이
    for (const a of agents) {
      const d = FURNITURE.find((f) => f.id === "desk_" + a.id), mode = a.monitorMode();
      const xs = a.id === "manager" ? [22, 56] : [16];
      xs.forEach((dx) => Sprites.monitorScreen(c, d.x * TILE + dx, d.y * TILE - 6, mode, clock + a.index * 0.37));
    }
    const printing = agents.find((a) => a.mode === "hold" && a.beh && (a.beh.id === "print" || a.beh.id === "printout"));
    if (printing) Sprites.copierPaper(c, Math.min(1, printing.holdT / 2.2));
  }
  function drawLabels(ctx, scale) { // 화면 해상도: 머리 위 이름표와 말풍선
    for (const a of agents) {
      const walking = a.mode === "walk" && a.seg;
      const headTop = a.py - 19 + (a.seated && !walking ? 5 : 0) - 2;
      const tagTop = headTop - 11;
      const atDesk = a.atOwnDesk();
      if (!atDesk) OfficeUI.drawNameplate(ctx, scale, a.px, tagTop, a.view.icon, a.name); // 책상 이름표가 있으니 중복 표시 안 함
      const spec = (a.bubble && a.bubble.spec) || a.stateBubble();
      if (spec) {
        const desk = FURNITURE.find((f) => f.id === "desk_" + a.id);
        const bounce = spec.bounce ? -Math.abs(Math.sin(clock * 7)) * 3 : 0;
        const tip = (atDesk ? desk.y * TILE - 10 : tagTop - 1) + bounce; // 책상에 앉아 있으면 모니터 위로 띄움
        OfficeUI.drawBubble(ctx, scale, a.px, tip, spec);
      }
    }
  }
  function hitTest(lx, ly) { // 마우스가 캐릭터 위에 있는지
    let best = null;
    for (const a of agents) if (lx >= a.px - 8 && lx <= a.px + 8 && ly >= a.py - 24 && ly <= a.py + 2 && (!best || a.py > best.py)) best = a;
    return best;
  }
  const byId = (id) => agents.find((x) => x.id === id);
  const describe = () => agents.map((a) => `${a.name}: ${a.desc()}`);

  return { init, start, update, applyState, drawCharacters, drawEffects, drawLabels, hitTest, describe, byId, list: agents, onChange: (f) => onChange.push(f), get started() { return started; } };
})();
