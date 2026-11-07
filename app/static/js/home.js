// 홈(사무실 평면도) — 에이전트 자리·상태·활동 로그·요약을 실시간 표시
const agents = {}; // id -> {id,name,icon,role,summary,task_name,state}

// ---------- 책상 카드 ----------
// 진행률 틱마다 통째로 다시 그리면 CSS 애니메이션이 매번 처음부터 재시작돼 깜빡이므로,
// 구조가 바뀔 때(상태·고객사·오류 등)만 다시 그리고 나머지는 값만 갱신한다.
function deskKey(a) {
  const s = a.state;
  return [s.status, s.client_name, s.error_reason, s.last_message].join("|");
}

function deskHtml(a) {
  const s = a.state;
  const m = STATUS_META[s.status];
  const busy = s.status === "working" || s.status === "review";
  const client = s.client_name && s.status !== "idle" ? `<div class="mt-2 text-xs t-sub">📌 ${esc(s.client_name)}</div>` : "";
  const body =
    s.status === "working" ? `
      <div class="mt-3">
        <div class="bar-track"><div class="bar" style="width:${s.progress}%"></div></div>
        <div class="mt-1 text-xs t-sub"><b class="pct">${s.progress}%</b> · <span class="step">${esc(s.current_step)}</span></div>
      </div>` :
    s.status === "review" ? `<div class="mt-3 text-sm" style="color:var(--c-amber)">사람의 검토 승인을 기다리고 있어요</div>` :
    s.status === "error" ? `<div class="mt-3 text-sm" style="color:var(--c-red)">⚠ ${esc(s.error_reason)}</div>` :
    s.status === "done" ? `<div class="mt-3 text-sm" style="color:var(--c-teal)">${esc(s.current_step)}</div>` :
    (s.last_message ? `<div class="mt-3 text-xs t-mute">최근: ${esc(s.last_message)}</div>` : "");
  const reviewBtn = s.status === "review"
    ? `<a href="/agent?id=${a.id}" class="btn btn-review">검토하기</a>` : "";
  return `
    <div class="flex items-start justify-between">
      <div class="avatar-wrap"><div class="avatar">${a.icon}</div>${s.status === "working" ? '<div class="kbd">⌨️</div>' : ""}</div>
      <span class="badge badge-${s.status}">${m.icon} ${m.label}</span>
    </div>
    <div class="desk-name mt-3 font-bold text-lg">${esc(a.name)}</div>
    <div class="text-xs t-mute">${esc(a.role)}</div>
    <div class="mt-1 text-sm t-sub">${esc(a.summary)}</div>
    ${client}${body}
    <div class="mt-4 flex flex-wrap gap-2">
      ${reviewBtn}
      <button data-run="${a.id}" class="btn btn-primary" ${busy ? "disabled" : ""}>${a.id === "manager" ? "월마감 시작" : "작업 실행"}</button>
    </div>`;
}

function renderDesk(id) {
  const slot = document.getElementById("desk-" + id);
  const a = agents[id];
  if (!slot || !a) return;
  let desk = slot.firstElementChild;
  if (!desk) {
    desk = document.createElement("div");
    desk.tabIndex = 0;
    desk.setAttribute("role", "link");
    desk.dataset.id = id;
    slot.appendChild(desk);
  }
  const key = deskKey(a);
  if (desk.dataset.key !== key) { // 구조 변경 → 다시 그림
    desk.dataset.key = key;
    desk.className = `desk desk-${a.state.status}`;
    desk.setAttribute("aria-label", `${a.name} 상세 보기 — ${STATUS_META[a.state.status].label}`);
    desk.innerHTML = deskHtml(a);
  } else if (a.state.status === "working") { // 같은 구조 → 진행률만 갱신
    desk.querySelector(".bar").style.width = a.state.progress + "%";
    desk.querySelector(".pct").textContent = a.state.progress + "%";
    desk.querySelector(".step").textContent = a.state.current_step;
  }
}

function applySnapshot(list) {
  list.forEach((a) => { agents[a.id] = a; renderDesk(a.id); });
}

function applyStatus(st) {
  if (!agents[st.agent_id]) return;
  agents[st.agent_id].state = st;
  renderDesk(st.agent_id);
  scheduleSummary();
}

// ---------- 활동 로그 ----------
function feedItem(row) {
  const a = agents[row.agent] || { icon: "•", name: row.agent };
  return `<li><span class="time">${fmtHM(row.at)}</span>${a.icon} <b>${esc(a.name)}</b> — <span class="k-${esc(row.kind)}">${esc(row.message)}</span></li>`;
}

async function loadFeed() {
  const rows = await api("/api/activity?limit=40");
  document.getElementById("feed").innerHTML = rows.length
    ? rows.map(feedItem).join("") : '<li class="t-mute">아직 활동이 없어요</li>';
}

function addFeed(row) {
  const ul = document.getElementById("feed");
  if (ul.firstElementChild && ul.firstElementChild.classList.contains("t-mute")) ul.innerHTML = "";
  ul.insertAdjacentHTML("afterbegin", feedItem(row));
  while (ul.children.length > 60) ul.lastElementChild.remove();
}

// ---------- 요약·기한 ----------
let summaryTimer = null;
function scheduleSummary() { // 상태 이벤트가 몰려도 0.5초에 한 번만 조회
  clearTimeout(summaryTimer);
  summaryTimer = setTimeout(loadSummary, 500);
}
async function loadSummary() {
  try {
    const s = await api("/api/summary");
    document.getElementById("sumProcessed").textContent = s.processed;
    document.getElementById("sumReview").textContent = s.review_waiting;
    document.getElementById("sumError").textContent = s.errors;
  } catch (_) {}
}

async function loadDeadlines() {
  const items = await api("/api/deadlines?limit=3");
  document.getElementById("deadlines").innerHTML = items.map((d) => {
    const level = d.d_day <= 3 ? "dday-soon" : d.d_day <= 7 ? "dday-near" : "";
    const label = d.d_day === 0 ? "D-DAY" : `D-${d.d_day}`;
    const shift = d.shifted ? ` — 원래 ${d.original_date.slice(5).replace("-", "/")} → ${d.shift_reason}로 순연` : "";
    const tip = `${d.source}${shift}${d.verified ? "" : " — 법령 검증 전 값"}`;
    return `<div class="dday ${level}" title="${esc(tip)}"><b>${label}</b>${esc(d.name)} · ${d.date.slice(5).replace("-", "/")}${d.verified ? "" : '<span class="unverified">검증 전</span>'}</div>`;
  }).join("");
}

// ---------- 실행 ----------
async function runAgent(id) {
  const body = {
    client_id: document.getElementById("client").value,
    tax_year: Number(document.getElementById("year").value),
    simulate_error: document.getElementById("simError").checked,
    options: { is_sme: document.getElementById("sme").value === "1" },  // 코택·매니저가 사용 (나머지는 무시)
  };
  const rev = document.getElementById("revenue").value.replace(/[,\s]/g, "");
  if (rev) body.options.revenue = Number(rev);
  try {
    await api(`/api/agents/${id}/run`, { method: "POST", body });
  } catch (e) {
    toast(e.message, true);
  }
}

async function init() {
  initTheme("themeBtn");
  document.getElementById("today").textContent = new Date().toLocaleDateString("ko-KR",
    { year: "numeric", month: "long", day: "numeric", weekday: "long" });

  const meta = await api("/api/meta");
  document.getElementById("year").innerHTML = meta.tax_years.map((y) => `<option value="${y}">${y}년</option>`).join("");
  document.getElementById("client").innerHTML =
    meta.clients.map((c) => `<option value="${esc(c.id)}">${esc(c.name)} (${esc(c.industry)})</option>`).join("");

  // 카드 클릭 → 상세, 버튼은 각자 동작
  const floor = document.getElementById("floor");
  floor.addEventListener("click", (e) => {
    const run = e.target.closest("[data-run]");
    if (run) { if (!run.disabled) runAgent(run.dataset.run); return; }
    if (e.target.closest("a")) return;
    const desk = e.target.closest(".desk");
    if (desk) location.href = `/agent?id=${desk.dataset.id}`;
  });
  floor.addEventListener("keydown", (e) => {
    const desk = e.target.closest(".desk");
    if (desk && e.target === desk && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); location.href = `/agent?id=${desk.dataset.id}`; }
  });

  loadDeadlines().catch(() => {});
  connectEvents({
    onSnapshot: (list) => { const first = !Object.keys(agents).length; applySnapshot(list); if (first) loadFeed().catch(() => {}); },
    onStatus: applyStatus,
    onActivity: addFeed,
    onMode: (m) => (document.getElementById("mode").textContent = modeBadge(m)),
    onPoll: () => { loadSummary(); loadFeed().catch(() => {}); },
  });
  loadSummary();
}

init().catch((e) => toast(e.message, true));
