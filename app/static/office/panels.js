// 오피스 뷰 — 사이드 패널(캐릭터·책상) / 화이트보드 캘린더 / 책장 법령 자료실 / TV 활동 로그 / 상단 정보 줄
// 데이터는 전부 기존 API 를 그대로 쓴다: /api/agents/{id}(/tasks|/uploads|/upload|/run), /api/tasks/{id}(/review|/download),
// /api/calendar, /api/deadlines, /api/summary, /api/activity, /api/rules.
const Panels = (() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const panel = document.getElementById("panel"), bodyEl = document.getElementById("panelBody");
  const titleEl = document.getElementById("panelTitle"), subEl = document.getElementById("panelSub");
  let cur = null, lastFocus = null, meta = null;
  let calY = new Date().getFullYear(), calM = new Date().getMonth() + 1;
  const CAT_COLOR = { "원천세": "#4aa3d6", "부가세": "#47b86b", "법인세": "#8d6cd6", "연말정산·지급명세서": "#e0a526", "4대보험": "#e8607a" };
  const WD = "일월화수목금토";
  const UPLOAD_HINT = {
    payroll: "급여대장 .xlsx (시트: 정보·급여대장·소득자지급내역·전월급여). 올리지 않으면 샘플을 써요.",
    bookkeeping: "파일 이름에 은행·카드·계정·전기·시차 중 하나가 들어간 CSV/xlsx를 역할별로 인식해요.",
    vat: "파일 이름에 매출·매입·장부·카드 중 하나가 들어간 CSV/xlsx를 역할별로 인식해요.",
    corptax: "파일 이름에 '시산표'가 들어간 CSV/xlsx. 없으면 북키 결과(시산표)를 이어받아요.",
    manager: "매니저는 업로드가 필요 없어요.",
  };

  // ---------- 패널 열고 닫기 ----------
  function place() { // 상단 바(헤더+정보 줄) 아래부터 채운다
    const r = document.getElementById("infobar").getBoundingClientRect();
    panel.style.top = Math.round(r.bottom) + "px";
  }
  function show(type, id, title, sub) {
    if (!cur) lastFocus = document.activeElement;
    cur = { type, id };
    titleEl.textContent = title; subEl.textContent = sub || "";
    panel.hidden = false; place();
    bodyEl.scrollTop = 0;
    requestAnimationFrame(() => panel.classList.add("open"));
    panel.focus({ preventScroll: true });
  }
  function close() {
    if (!cur) return;
    cur = null;
    panel.classList.remove("open");
    setTimeout(() => { if (!cur) panel.hidden = true; }, 180);
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }
  const isOpen = () => !!cur;
  const contains = (node) => panel.contains(node);
  function toast(msg, bad = false) {
    const t = document.getElementById("panelToast");
    t.textContent = msg; t.className = "ptoast " + (bad ? "bad" : "ok"); t.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, 3500);
  }
  const getMeta = async () => meta || (meta = await api("/api/meta"));

  // ---------- 에이전트 패널 ----------
  async function openAgent(id) {
    const v = AGENT_VIEW[id];
    show("agent", id, v.name, v.role);
    bodyEl.innerHTML = `<section id="pLive" class="sec"></section><section id="pRun" class="sec"></section><section id="pReview" class="sec"></section>
      <section id="pTasks" class="sec"></section><section id="pUpload" class="sec"></section>
      <p class="sec"><a class="plink" href="/agent?id=${id}">상세 페이지 열기 (이상항목·체크리스트·승인 이력 등) ↗</a></p>`;
    renderLive(); renderRun();
    await Promise.all([loadReview(), loadTasks(), loadUploads()]).catch((e) => toast(e.message, true));
  }
  const stillAgent = (id) => cur && cur.type === "agent" && cur.id === id;

  function renderLive() { // 상태·진행률은 SSE 로 계속 갱신 (입력창이 없는 영역이라 다시 그려도 안전)
    const a = Agents.byId(cur.id), s = a.state, el = $("#pLive");
    if (!el) return;
    const m = STATUS_META[a.status];
    const bar = a.status === "working"
      ? `<div class="pbar"><i style="width:${Math.max(0, Math.min(100, s.progress || 0))}%"></i></div><div class="small">${s.progress || 0}% · ${esc(s.current_step || "")}</div>` : "";
    const err = a.status === "error" ? `<div class="perr">⚠ ${esc(s.error_reason || "오류")}</div>` : "";
    const note = a.status === "review" ? `<div class="small">사람의 검토 승인을 기다리고 있어요</div>` : "";
    el.innerHTML = `<div class="live"><span class="sbadge s-${a.status}">${m.icon} ${m.label}</span>
      ${s.client_name ? `<span class="small">📌 ${esc(s.client_name)}</span>` : ""}</div>${bar}${err}${note}
      <div class="small" style="margin-top:4px">지금 하는 일: ${esc(a.desc())}</div>`;
  }

  async function renderRun() {
    const el = $("#pRun");
    if (!el) return;
    const id = cur.id, m = await getMeta();
    if (!stillAgent(id)) return;
    const mgr = id === "manager", sme = id === "manager" || id === "corptax";
    el.innerHTML = `<h3>${mgr ? "🚀 월마감 시작" : "▶ 작업 실행"}</h3>
      <div class="row">
        <select id="pClient" aria-label="고객사">${m.clients.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}</select>
        <select id="pYear" aria-label="적용연도">${m.tax_years.map((y) => `<option>${y}</option>`).join("")}</select>
      </div>
      ${sme ? `<div class="row"><select id="pSme" aria-label="기업 구분"><option value="1">중소기업</option><option value="0">일반기업</option></select>
        <input id="pRev" inputmode="numeric" placeholder="수입금액(원, 선택)" aria-label="수입금액"></div>` : ""}
      <button id="pRunBtn" class="pbtn primary">${mgr ? "월마감 시작" : "작업 실행"}</button>`;
    $("#pRunBtn").addEventListener("click", async () => {
      const options = {};
      if (sme) { options.is_sme = $("#pSme").value === "1"; const rev = $("#pRev").value.replace(/[,\s]/g, ""); if (rev) options.revenue = Number(rev); }
      try {
        await api(`/api/agents/${id}/run`, { method: "POST", body: { client_id: $("#pClient").value, tax_year: Number($("#pYear").value), simulate_error: false, options } });
        toast("작업을 시작했어요");
      } catch (e) { toast(e.message, true); }
    });
  }

  async function loadReview() { // 검토필요일 때만: 이상항목 요약 + 승인/반려 폼 (/api/tasks/{id}/review)
    const id = cur.id, sec = $("#pReview"), a = Agents.byId(id);
    if (!sec) return;
    if (a.status !== "review") { sec.innerHTML = ""; return; }
    const info = await api(`/api/agents/${id}`);
    if (!stillAgent(id)) return;
    const tid = info.pending_review_task_id;
    if (!tid) { sec.innerHTML = `<div class="small">데모 중이라 실제 검토 대상이 아니에요.</div>`; return; }
    const t = await api(`/api/tasks/${tid}`);
    if (!stillAgent(id)) return;
    const sev = (x) => (x.severity === "error" ? "🔴" : x.severity === "warning" ? "🟡" : "⚪");
    const an = t.anomalies.slice(0, 6).map((x) => `<li>${sev(x)} ${esc(x.message)}</li>`).join("");
    sec.innerHTML = `<div class="review">
      <h3>🟡 검토 승인 — AI는 작성·검증까지, 확정은 담당자</h3>
      <div class="small">작업 #${t.id} · ${esc(t.client_name)} · ${esc(t.title)} — ${esc(t.result || "")}</div>
      ${t.anomalies.length ? `<ul class="anoms">${an}</ul>${t.anomalies.length > 6 ? `<div class="small">외 ${t.anomalies.length - 6}건은 상세 페이지에서 확인</div>` : ""}` : `<div class="small">이상항목 없음</div>`}
      ${t.output_path ? `<a class="plink" href="/api/tasks/${t.id}/download">📥 결과 엑셀 내려받기</a>` : ""}
      <label>담당자 이름 <input id="rvName" placeholder="담당자 이름" value="${esc(localStorage.getItem("reviewerName") || "")}"></label>
      <label>코멘트 (선택) <input id="rvComment" placeholder="코멘트"></label>
      <label>반려 사유 <b class="req">(반려 시 필수)</b> <input id="rvReason" placeholder="반려 사유를 입력하세요" aria-describedby="rvErr"></label>
      <label>반려하면 <select id="rvOn"><option value="rework">재작업(일하는중으로 복귀)</option><option value="error">오류 처리</option></select></label>
      <div id="rvErr" class="perr" role="alert" hidden></div>
      <div class="row"><button id="rvOk" class="pbtn primary">승인</button><button id="rvNo" class="pbtn danger">반려</button></div></div>`;
    const err = (msg, el) => { const e = $("#rvErr"); e.textContent = msg; e.hidden = false; if (el) { el.setAttribute("aria-invalid", "true"); el.focus(); } };
    const submit = async (decision) => {
      $("#rvErr").hidden = true;
      ["rvName", "rvReason"].forEach((k) => $("#" + k).removeAttribute("aria-invalid"));
      const name = $("#rvName").value.trim(), reason = $("#rvReason").value.trim();
      if (!name) return err("담당자 이름을 입력해 주세요", $("#rvName"));
      if (decision === "reject" && !reason) return err("반려 사유를 입력해 주세요 (반려할 때는 필수예요)", $("#rvReason")); // 반려는 사유 필수
      $("#rvOk").disabled = $("#rvNo").disabled = true;
      try {
        await api(`/api/tasks/${tid}/review`, { method: "POST", body: { reviewer_name: name, decision, comment: $("#rvComment").value, reason, on_reject: $("#rvOn").value } });
        localStorage.setItem("reviewerName", name);
        toast(decision === "approve" ? "승인했어요" : "반려했어요");
        loadTasks();
      } catch (e) { err(e.message); $("#rvOk").disabled = $("#rvNo").disabled = false; }
    };
    $("#rvOk").addEventListener("click", () => submit("approve"));
    $("#rvNo").addEventListener("click", () => submit("reject"));
  }

  async function loadTasks() {
    const id = cur.id, el = $("#pTasks");
    if (!el) return;
    const tasks = await api(`/api/agents/${id}/tasks`);
    if (!stillAgent(id)) return;
    el.innerHTML = `<h3>최근 작업 이력</h3>` + (tasks.length ? `<ul class="tasks">${tasks.slice(0, 5).map((t) => `<li>
      <b>#${t.id}</b> ${esc(t.client_name)} · ${TASK_STATUS_LABEL[t.status] || t.status}
      ${t.result ? `<div class="small">${esc(t.result)}</div>` : ""}
      <div class="small">${fmtTime(t.started_at)}${t.output_path ? ` · <a class="plink" href="/api/tasks/${t.id}/download">📥 결과 다운로드</a>` : ""}</div></li>`).join("")}</ul>` : `<div class="small">아직 작업 이력이 없어요.</div>`);
  }

  async function loadUploads() {
    const id = cur.id, el = $("#pUpload");
    if (!el) return;
    const files = await api(`/api/agents/${id}/uploads`);
    if (!stillAgent(id)) return;
    el.innerHTML = `<h3>📎 입력 파일 업로드</h3><div class="small">${esc(UPLOAD_HINT[id] || "")} (최대 10MB)</div>
      <div class="row"><input id="pFile" type="file" accept=".xlsx,.xls,.csv" aria-label="업로드할 파일"><button id="pUp" class="pbtn">업로드</button></div>
      <ul class="tasks">${files.slice(0, 6).map((f) => `<li>📄 ${esc(f.name)} <span class="small">${fmtSize(f.size)} · ${fmtTime(f.uploaded_at)}</span></li>`).join("") || '<li class="small">올린 파일이 없어요.</li>'}</ul>`;
    $("#pUp").addEventListener("click", async () => {
      const f = $("#pFile").files[0];
      if (!f) return toast("파일을 선택해 주세요", true);
      const form = new FormData(); form.append("file", f);
      try { await api(`/api/agents/${id}/upload`, { method: "POST", body: form }); toast("업로드했어요"); loadUploads(); } catch (e) { toast(e.message, true); }
    });
  }

  function onAgentChange(a, changed) { // SSE/데모로 상태가 들어올 때마다
    if (!cur || cur.type !== "agent" || cur.id !== a.id) return;
    renderLive();
    if (changed) { loadReview().catch(() => {}); loadTasks().catch(() => {}); }
  }

  // ---------- 화이트보드: 신고기한 D-day 캘린더 ----------
  async function openCalendar() {
    show("calendar", null, "📅 신고기한 캘린더", "화이트보드 · 매니저의 신고기한 데이터");
    await renderCalendar();
  }
  async function renderCalendar() {
    const r = await api(`/api/calendar?year=${calY}&month=${calM}`);
    if (!cur || cur.type !== "calendar") return;
    const byDate = {}, hol = {};
    r.events.forEach((e) => (byDate[e.date] ||= []).push(e));
    r.holidays.forEach((h) => { hol[h.date] = h.name; });
    const first = new Date(calY, calM - 1, 1).getDay(), days = new Date(calY, calM, 0).getDate();
    let cells = "";
    for (let i = 0; i < first; i++) cells += `<div class="cal-cell empty"></div>`;
    for (let d = 1; d <= days; d++) {
      const ds = `${calY}-${String(calM).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      const wd = (first + d - 1) % 7, ev = byDate[ds] || [];
      cells += `<div class="cal-cell${ds === r.today ? " today" : ""}${wd === 0 || hol[ds] ? " red" : ""}" ${hol[ds] ? `title="${esc(hol[ds])}"` : ""}>
        <span class="cal-d">${d}</span>${ev.slice(0, 2).map((e) => `<i class="cal-chip" style="background:${CAT_COLOR[e.category] || "#888"}" title="${esc(e.name)}">${esc(e.category.slice(0, 4))}</i>`).join("")}${ev.length > 2 ? `<span class="small">+${ev.length - 2}</span>` : ""}</div>`;
    }
    const list = r.events.map((e) => {
      const dd = e.d_day === 0 ? "D-DAY" : e.d_day > 0 ? `D-${e.d_day}` : `D+${-e.d_day}`;
      const shift = e.shifted ? `<div class="small warn">원래 ${e.original_date.slice(5).replace("-", "/")} → ${esc(e.shift_reason)}로 순연${e.holiday_known ? "" : " (공휴일 정보 없음)"}</div>` : "";
      return `<li><b>${e.date.slice(5).replace("-", "/")} (${WD[new Date(e.date).getDay()]})</b> <span class="dd ${e.d_day <= 3 && e.d_day >= 0 ? "soon" : ""}">${dd}</span>
        <div>${esc(e.name)} <i class="cal-chip" style="background:${CAT_COLOR[e.category] || "#888"}">${esc(e.category)}</i>${e.verified ? "" : ' <span class="todo-tag">검증 전</span>'}</div>${shift}
        <div class="small">근거: ${esc(e.source)}</div>${e.note ? `<div class="small ext-where">📝 ${esc(e.note)}</div>` : ""}</li>`;
    }).join("");
    bodyEl.innerHTML = `<section class="sec"><div class="calnav"><button class="pbtn" id="calPrev" aria-label="이전 달">‹</button><b>${calY}년 ${calM}월</b><button class="pbtn" id="calNext" aria-label="다음 달">›</button></div>
      <div class="cal-grid">${[...WD].map((w, i) => `<div class="cal-h${i === 0 ? " red" : ""}">${w}</div>`).join("")}${cells}</div>
      ${r.holidays.length ? `<div class="small" style="margin-top:6px">공휴일: ${r.holidays.map((h) => `${h.date.slice(5).replace("-", "/")} ${esc(h.name)}`).join(" · ")}${r.holiday_known ? " (임시 목록 — 검증 전)" : ""}</div>` : ""}</section>
      <section class="sec"><h3>이달의 신고기한</h3><ul class="events">${list || '<li class="small">이 달에는 신고기한이 없어요.</li>'}</ul>
      <div class="small">순연 근거: ${esc(r.shift_source)} · '검증 전' 표시가 없는 기한은 법령 MCP로 조문을 확인한 값이에요(공휴일 목록은 아직 임시).</div></section>`;
    $("#calPrev").addEventListener("click", () => { calM--; if (calM < 1) { calM = 12; calY--; } renderCalendar(); });
    $("#calNext").addEventListener("click", () => { calM++; if (calM > 12) { calM = 1; calY++; } renderCalendar(); });
  }

  // ---------- 책장: 법령 자료실 (rules 의 모든 수치 — GET /api/rules) ----------
  const loadRules = () => api("/api/rules"); // 열 때마다 새로 읽는다: rules 를 고치면 바로 반영
  async function rulesCounts() { // 상단 '미확인' 개수용
    const d = await loadRules();
    return { total: d.summary.total, todo: d.summary.needs_check, external: d.summary.external || 0, year: d.year, nulls: d.summary.null_values };
  }
  async function openRules() {
    show("rules", null, "📚 법령 자료실", "책장 · rules 의 세율·요율·한도와 근거");
    bodyEl.innerHTML = `<section class="sec small">불러오는 중…</section>`;
    let d;
    try { d = await loadRules(); } catch (e) { bodyEl.innerHTML = `<section class="sec perr">${esc(e.message)}</section>`; return; }
    if (!cur || cur.type !== "rules") return;
    const sm = d.summary;
    bodyEl.innerHTML = `<section class="sec">
      <div class="rsum"><span>${d.year}년 기준</span> <b class="todo-count">🔴 미확인 ${sm.needs_check}</b> / <b class="ext-count">🟠 외부자료 반입 필요 ${sm.external || 0}</b> <span class="small">/ 전체 ${sm.total} (확인 완료 ${sm.verified}${sm.null_values ? `, 값 없음 ${sm.null_values}` : ""})</span></div>
      <div class="small">빨간 항목은 <b>TODO: 확인 필요</b> — 법령 MCP로 검증 전 임시값이거나 값이 비어 있어요. 검증하면 yaml 에 <code>verified: true</code> + 확인일을 적어요.
      주황 항목은 <b>외부 자료 반입 필요</b> — 법령 MCP로는 확인할 수 없어서 홈택스 표·작성요령 등을 받아 넣어야 해요(받을 곳 안내가 항목마다 있어요).
      같은 내용: <code>docs/rules_check.md</code> (<code>scripts/export_rules_check.py</code>)</div>
      <div class="row"><label class="chk"><input id="rOnly" type="checkbox"> 미확인만 보기</label><input id="rQ" placeholder="검색 (항목명·값·근거)" aria-label="자료실 검색"></div></section>
      <section class="sec" id="rList"></section>`;
    const draw = () => {
      const only = $("#rOnly").checked, q = $("#rQ").value.trim().toLowerCase();
      $("#rList").innerHTML = d.files.map((f) => {
        const items = f.items.filter((i) => (!only || i.needs_check || i.external) && (!q || `${i.name} ${i.label} ${i.source} ${i.value}`.toLowerCase().includes(q)));
        if (!items.length) return "";
        const todo = f.items.filter((i) => i.needs_check).length, ext = f.items.filter((i) => i.external).length;
        const tags = (todo ? `<span class="todo-tag">미확인 ${todo}</span> ` : "") + (ext ? `<span class="ext-tag">외부자료 ${ext}</span>` : "");
        return `<details ${only || q ? "open" : ""}><summary>${esc(f.title)} <span class="small">${esc(f.file)}</span> ${tags || '<span class="ok-tag">모두 확인됨</span>'}</summary>
          ${items.map((i) => `<div class="rule ${i.needs_check ? "todo" : i.external ? "ext" : "ok"}"><div><b>${esc(i.label)}</b></div><div class="rpath">${esc(i.name)}</div><div class="rval">${esc(i.value)}</div>
            <div class="small">근거: ${esc(i.source) || "(없음)"}</div>
            <div class="small">적용연도 ${esc(i.year)} · 확인일 ${esc(i.verified_on) || "-"}</div>
            <div>${i.needs_check ? `<span class="todo-tag">🔴 TODO: 확인 필요</span> <span class="small">${esc(i.reason)}</span>`
              : i.external ? `<span class="ext-tag">🟠 외부 자료 반입 필요</span> <span class="small">${esc(i.reason)}</span><div class="small ext-where">📥 ${esc(i.where)}</div>`
              : `<span class="ok-tag">✔ 확인됨</span>`}</div></div>`).join("")}</details>`;
      }).join("") || '<div class="small">조건에 맞는 항목이 없어요.</div>';
    };
    $("#rOnly").addEventListener("change", draw); $("#rQ").addEventListener("input", draw);
    draw();
  }

  // ---------- TV: 실시간 활동 로그 ----------
  const KIND_ICON = { start: "▶", review: "🟡", error: "🔴", done: "✅", upload: "📎", info: "•" };
  const logLine = (row) => { const v = AGENT_VIEW[row.agent]; return `<li class="k-${esc(row.kind)}"><span class="small">${fmtHM(row.at)}</span> ${KIND_ICON[row.kind] || "•"} <b>${esc(v ? v.name : row.agent)}</b> — ${esc(row.message)}</li>`; };
  async function openLog() {
    show("log", null, "📺 실시간 활동 로그", "TV · 하단 한 줄 티커와 같은 데이터");
    bodyEl.innerHTML = `<section class="sec"><ul class="logs" id="logList"><li class="small">불러오는 중…</li></ul></section>`;
    const rows = await api("/api/activity?limit=60");
    if (!cur || cur.type !== "log") return;
    $("#logList").innerHTML = rows.map(logLine).join("") || '<li class="small">아직 활동이 없어요.</li>';
  }
  function onActivity(row) {
    if (!cur || cur.type !== "log") return;
    const ul = $("#logList");
    if (ul) ul.insertAdjacentHTML("afterbegin", logLine(row));
  }

  panel.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
  $("#panelClose").addEventListener("click", close);
  addEventListener("resize", () => { if (cur) place(); });

  return { openAgent, openCalendar, openRules, openLog, close, isOpen, contains, onAgentChange, onActivity, rulesCounts, toast };
})();

// ---------- 상단 정보 줄: D-day 배지, 오늘 처리·검토 대기·오류, 규칙 미확인 개수, 에이전트 상태 칩 ----------
const InfoBar = (() => {
  const $ = (id) => document.getElementById(id);
  async function loadDeadlines() {
    try {
      const items = await api("/api/deadlines?limit=3");
      $("ibDday").innerHTML = items.map((d) => {
        const level = d.d_day <= 3 ? "soon" : d.d_day <= 7 ? "near" : "";
        const label = d.d_day === 0 ? "D-DAY" : `D-${d.d_day}`;
        const shift = d.shifted ? ` — 원래 ${d.original_date.slice(5).replace("-", "/")} → ${d.shift_reason}로 순연` : "";
        return `<button class="chip dday ${level}" data-open="calendar" title="${esc(d.source + shift + (d.verified ? "" : " — 법령 검증 전 값"))}"><b>${label}</b> ${esc(d.name)} · ${d.date.slice(5).replace("-", "/")}${d.verified ? "" : '<span class="unv">검증 전</span>'}</button>`;
      }).join("");
    } catch (_) {}
  }
  async function loadSummary() {
    try {
      const s = await api("/api/summary");
      $("ibSummary").innerHTML = `<span class="chip" title="오늘 만든 작업 중 검토필요·완료 건수">오늘 처리 <b>${s.processed}</b></span>
        <span class="chip ${s.review_waiting ? "warn" : ""}" title="사람의 검토 승인을 기다리는 건수">검토 대기 <b>${s.review_waiting}</b></span>
        <span class="chip ${s.errors ? "bad" : ""}" title="오늘 만든 작업 중 오류 건수">오류 <b>${s.errors}</b></span>`;
    } catch (_) {}
  }
  async function loadRules() {
    const el = $("ibRules");
    try {
      const c = await Panels.rulesCounts();
      el.innerHTML = `📚 미확인 <b>${c.todo}</b> · 외부자료 <b>${c.external}</b> /${c.total}`;
      el.className = "chip " + (c.todo ? "bad" : c.external ? "warn" : "ok");
      el.title = `법령 자료실 열기 — ${c.year}년 rules 중 확인필요(빨강) ${c.todo}개, 외부 자료 반입 필요(주황) ${c.external}개`;
    } catch (_) { el.textContent = "📚 법령 자료실"; el.className = "chip"; }
  }
  function renderChips() { // 에이전트 상태 칩(키보드로도 패널을 열 수 있는 버튼)
    $("ibAgents").innerHTML = AGENT_IDS.map((id) => {
      const a = Agents.byId(id), m = STATUS_META[a ? a.status : "idle"];
      return `<button class="chip s-${a ? a.status : "idle"}" data-agent="${id}" title="${esc(AGENT_VIEW[id].name)} — ${m.label}">${m.icon} ${esc(AGENT_VIEW[id].name)}</button>`;
    }).join("");
  }
  let sumTimer = null;
  const refreshSummarySoon = () => { clearTimeout(sumTimer); sumTimer = setTimeout(loadSummary, 600); };
  function init() {
    loadDeadlines(); loadSummary(); loadRules(); renderChips();
    setInterval(loadSummary, 30000); setInterval(loadDeadlines, 3600000);
    $("infobar").addEventListener("click", (e) => {
      const a = e.target.closest("[data-agent]"), o = e.target.closest("[data-open]");
      if (a) Panels.openAgent(a.dataset.agent);
      else if (o) ({ calendar: Panels.openCalendar, rules: Panels.openRules, log: Panels.openLog })[o.dataset.open]();
    });
  }
  return { init, renderChips, refreshSummarySoon };
})();
