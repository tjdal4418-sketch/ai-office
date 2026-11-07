// 오피스 뷰 — 메인: 게임 루프, 레이어 합성, 마우스, SSE 연결, 데모 모드
// (1단계 맵·가구 / 2단계 캐릭터 이동·행동 / 3단계 상태별 연출·실시간 연결·데모 / 4단계 사이드 패널·캘린더·자료실·활동 로그, 패널은 panels.js)
(() => {
  const W = COLS * TILE, H = ROWS * TILE; // 논리 해상도 512×352
  const DEBUG = /[?&]debug=1/.test(location.search); // ?debug=1 이면 행동 정보 줄을 보여준다
  const cv = document.getElementById("office"), ctx = cv.getContext("2d");
  const makeLayer = () => { const c = document.createElement("canvas"); c.width = W; c.height = H; return c; };
  const staticCv = makeLayer(), charCv = makeLayer(), fxCv = makeLayer(), overlayCv = makeLayer();
  const sctx = staticCv.getContext("2d"), cctx = charCv.getContext("2d"), fctx = fxCv.getContext("2d"), octx = overlayCv.getContext("2d");
  [sctx, cctx, fctx, octx].forEach((c) => { c.imageSmoothingEnabled = false; });
  Agents.init();

  let scale = 1, staticKey = "", hover = null, hoverAgent = null, debug = false, lastT = 0;

  // 정적 레이어는 시각(분)이 바뀔 때만 다시 그린다 — 시계 바늘과 창밖 풍경(낮/저녁/밤) 때문
  function ensureStatic() {
    const d = new Date(), env = { hour: d.getHours(), minute: d.getMinutes() };
    const key = `${env.hour}:${env.minute}`;
    if (key === staticKey) return;
    Sprites.renderStatic(sctx, env);
    Sprites.renderOverlay(octx);
    staticKey = key;
  }

  // 캔버스를 정수배로만 확대한다(픽셀이 흐려지지 않게). 기기 픽셀 비율(dpr)도 고려
  function layout() {
    const stage = document.getElementById("stage"), dpr = window.devicePixelRatio || 1;
    const s = Math.max(1, Math.floor(Math.min(stage.clientWidth * dpr / W, stage.clientHeight * dpr / H)));
    scale = s;
    cv.width = W * s; cv.height = H * s;
    cv.style.width = (W * s / dpr) + "px"; cv.style.height = (H * s / dpr) + "px";
  }

  function frame(t) {
    requestAnimationFrame(frame); // 먼저 예약: 한 프레임에서 오류가 나도 화면이 멈추지 않게(탭이 안 보이면 브라우저가 알아서 멈춘다)
    const dt = lastT ? (t - lastT) / 1000 : 0;
    lastT = t;
    ensureStatic();
    Agents.update(dt);
    cctx.clearRect(0, 0, W, H); Agents.drawCharacters(cctx);
    fctx.clearRect(0, 0, W, H); Agents.drawEffects(fctx);
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.drawImage(staticCv, 0, 0, W * scale, H * scale);
    ctx.drawImage(charCv, 0, 0, W * scale, H * scale);
    ctx.drawImage(overlayCv, 0, 0, W * scale, H * scale); // 유리 칸막이·매니저 책상(캐릭터 앞)
    ctx.drawImage(fxCv, 0, 0, W * scale, H * scale);       // 모니터 화면·복합기 종이
    OfficeUI.drawNameplates(ctx, scale);
    Agents.drawLabels(ctx, scale);
    if (hoverAgent) OfficeUI.drawHover(ctx, scale, { x: (hoverAgent.px - 8) / TILE, y: (hoverAgent.py - 24) / TILE, w: 1, h: 26 / TILE }, t);
    else OfficeUI.drawHover(ctx, scale, hover, t);
    if (debug) OfficeUI.drawDebug(ctx, scale);
  }

  // ---------- 마우스 ----------
  function toLogical(e) {
    const r = cv.getBoundingClientRect();
    return { x: (e.clientX - r.left) * (cv.width / r.width) / scale, y: (e.clientY - r.top) * (cv.height / r.height) / scale };
  }
  function furnitureAt(p) { // 겹치면 가장 작은 가구 우선
    let best = null, bestArea = Infinity;
    for (const f of FURNITURE) {
      if (!f.tip) continue;
      const x = f.x * TILE, y = f.y * TILE, w = f.w * TILE, h = f.h * TILE;
      if (p.x >= x && p.x < x + w && p.y >= y && p.y < y + h && w * h < bestArea) { best = f; bestArea = w * h; }
    }
    return best;
  }
  cv.addEventListener("mousemove", (e) => {
    const p = toLogical(e), a = Agents.hitTest(p.x, p.y);
    hoverAgent = a; hover = a ? null : furnitureAt(p);
    cv.style.cursor = a || (hover && hover.action) ? "pointer" : hover ? "help" : "default";
    if (a) OfficeUI.showTip(`${a.name} · ${a.view.role}\n${a.desc()}`, e.clientX, e.clientY);
    else if (hover) OfficeUI.showTip(hover.tip, e.clientX, e.clientY);
    else OfficeUI.hideTip();
  });
  cv.addEventListener("mouseleave", () => { hover = hoverAgent = null; OfficeUI.hideTip(); });
  // 클릭: 캐릭터·책상 → 에이전트 패널 / 화이트보드 → 캘린더 / 책장 → 법령 자료실 / TV → 활동 로그
  const OPEN = { calendar: () => Panels.openCalendar(), rules: () => Panels.openRules(), log: () => Panels.openLog() };
  function clickTarget(e) { // 클릭한 곳에서 열 수 있는 것: { open: 함수 } 또는 null
    const p = toLogical(e), a = Agents.hitTest(p.x, p.y);
    if (a) return { open: () => Panels.openAgent(a.id) };
    const f = furnitureAt(p);
    if (!f || !f.action) return null;
    return { open: f.action === "agent" ? () => Panels.openAgent(f.agent) : OPEN[f.action] };
  }
  cv.addEventListener("click", (e) => {
    const t = clickTarget(e);
    if (t) t.open(); else Panels.close();
  });
  // 패널 바깥을 누르면 닫는다 (단, 캐릭터·가구를 눌러 다른 패널을 여는 경우는 그대로 두고 그 클릭이 패널을 바꾼다)
  document.addEventListener("mousedown", (e) => {
    if (!Panels.isOpen() || Panels.contains(e.target)) return;
    if (e.target === cv && clickTarget(e)) return;
    if (e.target.closest && e.target.closest("#infobar")) return; // 상단 정보 줄의 버튼은 패널을 바꾸는 용도
    Panels.close();
  });
  addEventListener("keydown", (e) => { if (e.key === "Escape") Panels.close(); });

  // ---------- 상단 바·하단 ----------
  const dateEl = document.getElementById("date"), timeEl = document.getElementById("clock");
  function tick() {
    const d = new Date();
    dateEl.textContent = d.toLocaleDateString("ko-KR", { month: "long", day: "numeric", weekday: "short" });
    timeEl.textContent = d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  tick(); setInterval(tick, 1000);

  const gridBtn = document.getElementById("gridBtn");
  const toggleGrid = () => { debug = !debug; gridBtn.setAttribute("aria-pressed", debug); gridBtn.classList.toggle("on", debug); };
  gridBtn.addEventListener("click", toggleGrid);
  addEventListener("keydown", (e) => { if ((e.key === "g" || e.key === "G") && !e.ctrlKey && !e.metaKey && !/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) toggleGrid(); });

  // 하단 활동 로그 한 줄 티커 (4단계 TV 와 같은 데이터)
  const tickerEl = document.getElementById("ticker");
  function showActivity(row) {
    const v = AGENT_VIEW[row.agent];
    tickerEl.textContent = `${fmtHM(row.at)}  ${v ? v.name : row.agent} — ${row.message}`;
    tickerEl.classList.remove("flash"); void tickerEl.offsetWidth; tickerEl.classList.add("flash");
    Panels.onActivity(row); // TV(활동 로그 패널)가 열려 있으면 같은 줄을 추가
  }
  api("/api/activity?limit=1").then((rows) => rows[0] && showActivity(rows[0])).catch(() => {});

  // 스크린리더용 상태 목록 (캔버스는 읽을 수 없으니 같은 내용을 숨김 텍스트로, aria-live)
  const a11y = document.getElementById("a11yStatus"), a11yKey = {};
  AGENT_IDS.forEach((id) => { const li = document.createElement("li"); li.id = "a11y-" + id; a11y.append(li); });
  Agents.onChange((a, changed) => {
    Panels.onAgentChange(a, changed);        // 열려 있는 사이드 패널 갱신 (캐릭터는 패널과 상관없이 계속 움직임)
    if (changed) InfoBar.renderChips();
    const s = a.state, step = Math.floor((s.progress || 0) / 10);
    const k = [a.status, a.status === "working" ? step : "", s.error_reason || ""].join("|");
    if (a11yKey[a.id] === k) return;
    a11yKey[a.id] = k;
    const label = STATUS_META[a.status].label;
    document.getElementById("a11y-" + a.id).textContent =
      `${a.name}: ${label}` + (a.status === "working" ? ` ${s.progress || 0}% ${s.current_step || ""}` : "") + (a.status === "error" ? ` — ${s.error_reason || ""}` : "");
  });

  // ---------- 실시간 연결 (기존 /api/events SSE, 끊기면 2초 폴링 — app.js 의 connectEvents 를 그대로 사용) ----------
  const lastStates = {};   // 서버가 알려 준 가장 최근 실제 상태 (데모 종료 시 복원)
  let demo = null;
  const applyReal = (id, st) => {
    const changed = !lastStates[id] || lastStates[id].status !== st.status;
    lastStates[id] = st;
    if (changed) InfoBar.refreshSummarySoon(); // 오늘 처리·검토 대기·오류 건수
    if (!demo) Agents.applyState(id, st);
  };
  const modeEl = document.getElementById("mode");
  connectEvents({
    onSnapshot: (list) => { list.forEach((a) => applyReal(a.id, a.state)); Agents.start(); },
    onStatus: (st) => applyReal(st.agent_id, st),
    onActivity: showActivity,
    onMode: (m) => { modeEl.textContent = modeBadge(m); },
  });
  setTimeout(() => Agents.start(), 2500); // 서버 응답이 없어도 입장은 시작

  // ---------- 데모 모드: 5명이 순서대로 일하는중 → 검토필요 → 완료 → 대기중 (GIF 녹화용, 서버와 무관한 가짜 상태) ----------
  const demoBtn = document.getElementById("demoBtn");
  const STEP_TEXT = {
    payroll: (p) => `급여대장 16명 중 ${Math.round(p * 0.16)}명 계산 중`,
    bookkeeping: (p) => `거래 30건 중 ${Math.round(p * 0.3)}건 분개 추천 중`,
    vat: (p) => (p < 50 ? "매출 세금계산서 ↔ 장부 대사 중" : "불공제 매입세액 후보 점검 중"),
    corptax: (p) => (p < 60 ? "기업업무추진비 한도 계산 중" : "세무조정 항목 판정 중"),
  };
  const fake = (id, status, extra = {}) => ({ agent_id: id, status, progress: 0, current_step: "", error_reason: null, client_name: "(주)가나상사", ...extra });

  function runDemo() {
    const timers = [];
    demo = { timers };
    const at = (ms, fn) => timers.push(setTimeout(fn, ms));
    const put = (id, st) => Agents.applyState(id, st);
    AGENT_IDS.forEach((id) => put(id, fake(id, "idle")));
    at(300, () => put("manager", fake("manager", "working", { progress: 3, current_step: "월마감 업무 배분 시작" })));
    const WORKERS = ["payroll", "bookkeeping", "vat", "corptax"];
    let end = 0;
    WORKERS.forEach((id, k) => {
      const t0 = 6500 + k * 6500, ticks = 13;
      const errAt = id === "corptax" ? 6 : -1;     // 코택은 중간에 오류가 한 번 났다가 다시 진행
      let t = t0;
      at(t0 - 700, () => put("manager", fake("manager", "working", { progress: 8 + k * 22, current_step: `${AGENT_VIEW[id].name} 업무 배분 중 (${k + 1}/4)` })));
      for (let i = 0; i <= ticks; i++) {
        const p = Math.round(i / ticks * 100), tt = t;
        at(tt, () => put(id, fake(id, "working", { progress: p, current_step: STEP_TEXT[id](p) })));
        if (i === errAt) {
          t += 600;
          at(t, () => put(id, fake(id, "error", { error_reason: "주민번호 누락 2건" })));
          t += 4500;
        }
        t += 600;
      }
      at(t + 200, () => put(id, fake(id, "review")));
      at(t + 5400, () => put(id, fake(id, "done")));
      at(t + 11400, () => put(id, fake(id, "idle")));
      end = t + 5400;
    });
    at(end + 800, () => put("manager", fake("manager", "review")));
    at(end + 6300, () => put("manager", fake("manager", "done")));
    at(end + 12300, () => put("manager", fake("manager", "idle")));
    at(end + 17000, () => { if (demo) { demo.timers.forEach(clearTimeout); runDemo(); } }); // 계속 반복
  }
  function toggleDemo() {
    if (demo) { // 중지 → 서버의 실제 상태로 복원
      demo.timers.forEach(clearTimeout); demo = null;
      demoBtn.textContent = "▶ 데모"; demoBtn.classList.remove("on");
      AGENT_IDS.forEach((id) => { if (lastStates[id]) Agents.applyState(id, lastStates[id]); else Agents.applyState(id, { status: "idle" }); });
    } else {
      runDemo();
      demoBtn.textContent = "■ 데모 중지"; demoBtn.classList.add("on");
    }
  }
  demoBtn.addEventListener("click", toggleDemo);
  if (/[?&]demo=1/.test(location.search)) toggleDemo();

  if (DEBUG) {
    const info = document.getElementById("behaviorInfo");
    info.hidden = false;
    setInterval(() => { info.textContent = Agents.describe().join("  ·  "); }, 500);
  }

  InfoBar.init();
  addEventListener("resize", layout);
  layout();
  requestAnimationFrame(frame);
})();
