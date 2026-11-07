// 매니저 상세 — 월마감 시작, 전체 진행 현황 매트릭스, 신고기한 캘린더
let calYear = new Date().getFullYear();
let calMonth = new Date().getMonth() + 1;
let matrixTimer = null;

const MATRIX_COLOR = { "검토필요": "var(--c-amber)", "오류": "var(--c-red)", "완료": "var(--c-teal)", "진행중": "var(--c-blue)", "작업 없음": "var(--sub, #64748b)" };
const WD = "일월화수목금토";

async function initManagerPanel() {
  const meta = await api("/api/meta");
  const el = document.getElementById("managerPanel");
  el.hidden = false;
  el.innerHTML = `
    <section class="card mb-4">
      <h2 class="font-bold mb-1">🚀 고객사 월마감 시작</h2>
      <p class="text-xs t-mute mb-2">선택한 고객사의 업무를 💰페이 → 📒북키 → 🧾배트 → 🏛️코택 순서로 나눠 줘요. 북키가 만든 시산표는 코택이 이어받고, 각 업무의 승인은 담당자가 각 화면에서 해요.</p>
      <div class="flex flex-wrap items-center gap-3">
        <label class="text-sm">고객사 <select id="mClient" class="input !w-auto ml-1">${meta.clients.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}</select></label>
        <label class="text-sm">적용연도 <select id="mYear" class="input !w-auto ml-1">${meta.tax_years.map((y) => `<option value="${y}">${y}년</option>`).join("")}</select></label>
        <label class="text-sm">기업 구분 <select id="mSme" class="input !w-auto ml-1"><option value="1">중소기업</option><option value="0">일반기업</option></select></label>
        <label class="text-sm">수입금액(원, 선택) <input id="mRevenue" class="input !w-40 ml-1" inputmode="numeric" placeholder="비우면 시산표 매출"></label>
        <button id="mStart" class="btn btn-primary">월마감 시작</button>
      </div>
    </section>
    <section class="card mb-4">
      <div class="flex flex-wrap items-center justify-between gap-2 mb-2">
        <h2 class="font-bold">📊 전체 진행 현황 <span class="text-xs t-mute font-normal">(고객사 × 업무, 가장 최근 작업)</span></h2>
        <a class="btn btn-ghost" id="mExcel" href="/api/progress.xlsx">엑셀 내려받기</a>
      </div>
      <div class="overflow-x-auto"><table class="tbl" id="mMatrix"></table></div>
    </section>
    <section class="card mb-4">
      <div class="flex flex-wrap items-center justify-between gap-2 mb-2">
        <h2 class="font-bold">🗓️ 신고기한 캘린더</h2>
        <div class="flex items-center gap-2">
          <button class="btn btn-ghost" id="calPrev">‹</button><b id="calTitle"></b><button class="btn btn-ghost" id="calNext">›</button>
        </div>
      </div>
      <div id="calBody" class="text-sm"></div>
    </section>`;
  document.getElementById("mStart").addEventListener("click", startMonthClose);
  document.getElementById("calPrev").addEventListener("click", () => moveMonth(-1));
  document.getElementById("calNext").addEventListener("click", () => moveMonth(1));
  document.getElementById("mYear").addEventListener("change", loadMatrix);
  await Promise.all([loadMatrix(), loadCalendar()]);
}

async function startMonthClose() {
  const rev = document.getElementById("mRevenue").value.replace(/[,\s]/g, "");
  const options = { is_sme: document.getElementById("mSme").value === "1" };
  if (rev) options.revenue = Number(rev);
  try {
    await api("/api/agents/manager/run", { method: "POST", body: {
      client_id: document.getElementById("mClient").value,
      tax_year: Number(document.getElementById("mYear").value), options } });
    toast("월마감 업무 배분을 시작했어요");
  } catch (e) {
    toast(e.message, true);
  }
}

async function loadMatrix() {
  const year = (document.getElementById("mYear") || {}).value;
  const m = await api("/api/progress" + (year ? `?tax_year=${year}` : ""));
  document.getElementById("mExcel").href = "/api/progress.xlsx" + (year ? `?tax_year=${year}` : "");
  document.getElementById("mMatrix").innerHTML = `
    <thead><tr><th>고객사</th>${m.agents.map((a) => `<th>${a.icon} ${esc(a.name)}</th>`).join("")}</tr></thead>
    <tbody>${m.rows.map((r) => `<tr><td class="font-semibold">${esc(r.client_name)}</td>${m.agents.map((a) => {
      const c = r.cells[a.id];
      const tip = c.error || c.result || c.step || "";
      const label = `<span style="color:${MATRIX_COLOR[c.label] || "inherit"};font-weight:600">${esc(c.label)}</span>${c.task_id ? ` <span class="t-mute text-xs">#${c.task_id}</span>` : ""}`;
      return `<td title="${esc(tip)}"><a class="link" href="/agent?id=${a.id}">${label}</a>${c.status === "working" || c.status === "queued" ? ` <span class="text-xs t-mute">${c.progress}%</span>` : ""}</td>`;
    }).join("")}</tr>`).join("")}</tbody>`;
}

// 상태 이벤트가 몰려도 0.5초에 한 번만 다시 그림
function managerRefresh() {
  if (!document.getElementById("mMatrix")) return;
  clearTimeout(matrixTimer);
  matrixTimer = setTimeout(() => loadMatrix().catch(() => {}), 500);
}

function moveMonth(delta) {
  calMonth += delta;
  if (calMonth < 1) { calMonth = 12; calYear -= 1; }
  if (calMonth > 12) { calMonth = 1; calYear += 1; }
  loadCalendar().catch((e) => toast(e.message, true));
}

async function loadCalendar() {
  const r = await api(`/api/calendar?year=${calYear}&month=${calMonth}`);
  document.getElementById("calTitle").textContent = `${r.year}년 ${r.month}월`;
  const rows = r.events.map((e) => {
    const wd = WD[new Date(e.date).getDay()];
    const level = e.d_day < 0 ? "t-mute" : e.d_day <= 3 ? "" : "";
    const dd = e.d_day === 0 ? "D-DAY" : e.d_day > 0 ? `D-${e.d_day}` : `D+${-e.d_day}`;
    const shift = e.shifted ? `<div class="text-xs" style="color:var(--c-amber)">원래 ${e.original_date.slice(5).replace("-", "/")} → ${esc(e.shift_reason)}로 순연${e.holiday_known ? "" : " (공휴일 정보 없음)"}</div>` : "";
    return `<tr class="${level}">
      <td class="whitespace-nowrap"><b>${e.date.slice(5).replace("-", "/")}</b> (${wd})<div class="text-xs t-mute">${dd}</div></td>
      <td><div>${esc(e.name)} <span class="badge">${esc(e.category)}</span>${e.verified ? "" : '<span class="unverified">검증 전</span>'}</div>${shift}
        <div class="text-xs t-mute">근거: ${esc(e.source)}</div></td></tr>`;
  }).join("");
  const hol = r.holidays.length
    ? `<div class="mt-3 text-xs t-mute">공휴일: ${r.holidays.map((h) => `${h.date.slice(5).replace("-", "/")} ${esc(h.name)}`).join(" · ")}${r.holiday_known ? " (임시 목록 — 검증 전)" : ""}</div>`
    : (r.holiday_known ? "" : '<div class="mt-3 text-xs t-mute">이 연도는 공휴일 정보가 없어 주말만 순연해요.</div>');
  document.getElementById("calBody").innerHTML = `
    <table class="tbl"><tbody>${rows || '<tr><td class="t-mute">이 달에는 신고기한이 없어요.</td></tr>'}</tbody></table>${hol}
    <div class="mt-2 text-xs t-mute">순연 근거: ${esc(r.shift_source)} · 모든 기한은 법령 MCP 검증 전 값이에요.</div>`;
}
