// 에이전트 상세 — 업로드, 작업 이력, 결과 다운로드, 검토 승인/반려, 승인 이력
const agentId = new URLSearchParams(location.search).get("id");
let agentInfo = null;
let selectedTaskId = null;

function renderHeader(a) {
  const s = a.state;
  const m = STATUS_META[s.status];
  const sub = s.status === "working" ? `${s.progress}% · ${esc(s.current_step)}` : esc(s.error_reason || s.current_step);
  document.getElementById("agentHead").innerHTML = `
    <div class="flex flex-wrap items-center gap-4">
      <div class="avatar" style="font-size:48px">${a.icon}</div>
      <div class="flex-1 min-w-[200px]">
        <div class="text-2xl font-bold">${esc(a.name)} <span class="text-sm font-normal t-mute">${esc(a.role)}</span></div>
        <div class="text-sm t-sub">${esc(a.summary)}</div>
        ${(a.coming_soon || []).length ? `<div class="mt-2 text-xs t-mute">추후 확장: ${a.coming_soon.map((n) =>
          `<span class="badge" style="opacity:.6;cursor:not-allowed" title="이번 단계에서는 구현하지 않았어요">🔒 ${esc(n)}</span>`).join(" ")}</div>` : ""}
      </div>
      <div class="text-right">
        <span class="badge badge-${s.status}">${m.icon} ${m.label}</span>
        <div class="text-xs t-mute mt-1">${s.client_name ? "📌 " + esc(s.client_name) + " · " : ""}${sub}</div>
      </div>
    </div>`;
}

// ---------- 업로드 ----------
async function loadUploads() {
  const files = await api(`/api/agents/${agentId}/uploads`);
  document.getElementById("uploadList").innerHTML = files.length
    ? files.map((f) => `<li class="py-1">📄 ${esc(f.name)} <span class="t-mute">· ${fmtSize(f.size)} · ${fmtTime(f.uploaded_at)}</span></li>`).join("")
    : '<li class="t-mute">올린 파일이 없어요</li>';
}

async function uploadFile() {
  const input = document.getElementById("fileInput");
  if (!input.files.length) { toast("파일을 선택해 주세요", true); return; }
  const form = new FormData();
  form.append("file", input.files[0]);
  try {
    const r = await api(`/api/agents/${agentId}/upload`, { method: "POST", body: form });
    toast(`${r.name} 업로드 완료`);
    input.value = "";
    await loadUploads();
  } catch (e) {
    toast(e.message, true);
  }
}

// ---------- 작업 이력 ----------
async function loadTasks() {
  const tasks = await api(`/api/agents/${agentId}/tasks`);
  document.getElementById("taskRows").innerHTML = tasks.length ? tasks.map((t) => `
    <tr class="clickable" data-task="${t.id}">
      <td>#${t.id}</td>
      <td>${esc(t.client_name)}</td>
      <td>${esc(t.title)}</td>
      <td>${t.tax_year}</td>
      <td class="t-mute">${fmtTime(t.started_at || t.created_at)}</td>
      <td class="t-mute">${fmtTime(t.finished_at)}</td>
      <td>${TASK_STATUS_LABEL[t.status] || t.status}${t.result ? ` <span class="t-mute">· ${esc(t.result)}</span>` : ""}</td>
      <td>${t.output_path ? `<a class="link" href="/api/tasks/${t.id}/download">엑셀</a>` : "-"}</td>
    </tr>`).join("")
    : '<tr><td class="t-mute" colspan="8">아직 작업 이력이 없어요. 홈에서 "작업 실행"을 눌러 보세요.</td></tr>';

  // 검토 대기 건이 있으면 우선 선택
  const pending = tasks.find((t) => t.status === "review");
  if (pending && (!selectedTaskId || agentInfo?.state.status === "review")) selectedTaskId = pending.id;
  else if (!selectedTaskId) selectedTaskId = (tasks[0] || {}).id || null;
  if (selectedTaskId) await loadTaskDetail(selectedTaskId);
}

async function loadTaskDetail(id) {
  selectedTaskId = id;
  const t = await api(`/api/tasks/${id}`);
  const anomalies = t.anomalies.length
    ? t.anomalies.map((x) => `<li class="py-1">${x.severity === "error" ? "🔴" : "🟡"} ${esc(x.message)} <span class="t-mute">${esc(x.row_ref)}</span></li>`).join("")
    : '<li class="py-1 t-mute">이상항목 없음</li>';
  const form = t.status === "review" ? `
    <div class="notice-review">
      <div class="font-semibold mb-2">검토 승인 — AI는 작성·검증까지, 확정은 담당자</div>
      <div class="grid gap-2 md:grid-cols-2">
        <input id="rvName" class="input" placeholder="담당자 이름">
        <input id="rvComment" class="input" placeholder="코멘트 (선택)">
        <input id="rvReason" class="input" placeholder="반려 사유 (반려 시 필수)">
        <select id="rvOnReject" class="input">
          <option value="rework">반려 시: 재작업(일하는중으로 복귀)</option>
          <option value="error">반려 시: 오류 처리</option>
        </select>
      </div>
      <div class="mt-3 flex gap-2">
        <button class="btn btn-primary" onclick="submitReview('approve')">승인</button>
        <button class="btn btn-danger" onclick="submitReview('reject')">반려</button>
      </div>
    </div>` : "";
  const itemsHtml = await renderWorkItems(t.id);
  document.getElementById("taskDetail").innerHTML = `
    <div class="font-semibold">작업 #${t.id} · ${esc(t.client_name)} · ${esc(t.title)} · ${t.tax_year}년 · ${TASK_STATUS_LABEL[t.status] || t.status}</div>
    ${t.error_reason ? `<div class="mt-1 text-sm" style="color:var(--c-red)">⚠ ${esc(t.error_reason)}</div>` : ""}
    <ul class="mt-2">${anomalies}</ul>${form}${itemsHtml}`;
}

// ---------- 결산 체크리스트·PBC (북키 작업) ----------
const PBC_STATUS = ["미준비", "준비중", "준비완료", "제출완료"];
const PBC_COLOR = { "미준비": "var(--c-red)", "준비중": "var(--c-amber)", "준비완료": "var(--c-green, #16a34a)", "제출완료": "var(--c-green, #16a34a)" };

async function renderWorkItems(taskId) {
  let items;
  try { items = await api(`/api/tasks/${taskId}/items`); } catch { return ""; }
  if (!items.checklist.length && !items.pbc.length) return "";
  const by = localStorage.getItem("itemBy") || "";
  const done = items.checklist.filter((i) => i.status === "done").length;
  const ready = items.pbc.filter((i) => i.status === "준비완료" || i.status === "제출완료").length;
  const check = items.checklist.map((i) => `
    <tr>
      <td><input type="checkbox" ${i.status === "done" ? "checked" : ""} onchange="saveItem(${i.id}, { status: this.checked ? 'done' : 'open' })"></td>
      <td class="t-mute">${esc(i.group)}</td>
      <td><div>${esc(i.title)}</div><div class="text-xs t-mute">${esc(i.detail)}</div>${i.hint ? `<div class="text-xs" style="color:var(--c-amber)">🔎 ${esc(i.hint)}</div>` : ""}</td>
      <td class="text-xs t-mute">${i.updated_by ? esc(i.updated_by) + " · " + fmtTime(i.updated_at) : ""}</td>
    </tr>`).join("");
  const pbc = items.pbc.map((i) => `
    <tr>
      <td class="t-mute">${esc(i.detail.split(" · ")[0])}</td>
      <td class="t-mute">${esc(i.group)}</td>
      <td>${esc(i.title)}<div class="text-xs t-mute">${esc(i.detail.split(" · ")[1] || "")}</div></td>
      <td><select class="input" style="padding:2px 6px;color:${PBC_COLOR[i.status] || "inherit"}" onchange="saveItem(${i.id}, { status: this.value }); this.style.color = PBC_COLOR[this.value]">
        ${PBC_STATUS.map((s) => `<option ${s === i.status ? "selected" : ""}>${s}</option>`).join("")}</select></td>
    </tr>`).join("");
  return `
    <div class="mt-4 pt-3" style="border-top:1px solid var(--border, #e5e7eb)">
      <div class="flex flex-wrap items-center gap-2 mb-2">
        <span class="font-semibold">체크하는 담당자</span>
        <input id="itemBy" class="input" style="max-width:160px" placeholder="이름" value="${esc(by)}" oninput="localStorage.setItem('itemBy', this.value)">
        <span class="text-xs t-mute">체크·상태 변경은 이 이름으로 기록돼요</span>
      </div>
      <div class="font-semibold mt-3">${agentId === "corptax" ? "세무조정 검토 체크리스트" : "결산 체크리스트"} <span class="t-mute font-normal" id="ckCount">${done}/${items.checklist.length} 완료</span></div>
      <table class="w-full text-sm mt-1"><tbody id="ckRows">${check}</tbody></table>
      <div class="font-semibold mt-4">${agentId === "corptax" ? "세무조사·외부감사 대응 자료" : "PBC 리스트"} <span class="t-mute font-normal">${ready}/${items.pbc.length} 준비</span></div>
      <table class="w-full text-sm mt-1"><tbody>${pbc}</tbody></table>
    </div>`;
}

async function saveItem(id, body) {
  try {
    await api(`/api/items/${id}`, { method: "PUT", body: { ...body, updated_by: (document.getElementById("itemBy") || {}).value || "" } });
    const rows = document.querySelectorAll("#ckRows input[type=checkbox]");
    const el = document.getElementById("ckCount");
    if (el) el.textContent = `${[...rows].filter((c) => c.checked).length}/${rows.length} 완료`;
  } catch (e) {
    toast(e.message, true);
    loadTaskDetail(selectedTaskId);
  }
}

async function submitReview(decision) {
  const body = {
    reviewer_name: document.getElementById("rvName").value,
    comment: document.getElementById("rvComment").value,
    reason: document.getElementById("rvReason").value,
    on_reject: document.getElementById("rvOnReject").value,
    decision,
  };
  try {
    await api(`/api/tasks/${selectedTaskId}/review`, { method: "POST", body });
    toast(decision === "approve" ? "승인되었습니다" : "반려되었습니다");
    await refreshAll();
  } catch (e) {
    toast(e.message, true);
  }
}

async function loadReviews() {
  const rows = await api(`/api/agents/${agentId}/reviews`);
  document.getElementById("reviewRows").innerHTML = rows.length ? rows.map((r) => `
    <tr>
      <td class="t-mute">${fmtTime(r.decided_at)}</td>
      <td>#${r.task_id}</td>
      <td>${esc(r.reviewer_name)}</td>
      <td>${r.decision === "approve" ? "✅ 승인" : "↩️ 반려"}</td>
      <td>${esc(r.comment)}${r.reason ? ` <span style="color:var(--c-red)">[사유: ${esc(r.reason)}]</span>` : ""}</td>
    </tr>`).join("") : '<tr><td class="t-mute" colspan="5">승인 이력이 없어요.</td></tr>';
}

async function refreshAll() {
  await Promise.all([loadTasks(), loadReviews()]);
}

function onState(st) {
  // 매니저 화면은 다른 에이전트의 상태 변화(진행 현황 매트릭스)도 따라간다
  if (agentId === "manager" && st.agent_id !== agentId && typeof managerRefresh === "function") managerRefresh();
  if (st.agent_id !== agentId || !agentInfo) return;
  const prev = agentInfo.state.status;
  agentInfo.state = st;
  renderHeader(agentInfo);
  if (prev !== st.status) refreshAll(); // 상태 전환 시 표·이력 갱신
  if (agentId === "manager" && typeof managerRefresh === "function") managerRefresh();
}

async function init() {
  if (!agentId) { location.href = "/"; return; }
  initTheme("themeBtn");
  document.getElementById("uploadBtn").addEventListener("click", uploadFile);
  document.getElementById("taskRows").addEventListener("click", (e) => {
    const tr = e.target.closest("[data-task]");
    if (tr && !e.target.closest("a")) loadTaskDetail(Number(tr.dataset.task));
  });
  loadUploads().catch((e) => toast(e.message, true));
  if (agentId === "manager" && typeof initManagerPanel === "function") initManagerPanel().catch((e) => toast(e.message, true));
  connectEvents({
    onSnapshot: (list) => {
      const a = list.find((x) => x.id === agentId);
      if (!a) return;
      const first = !agentInfo;
      agentInfo = a;
      renderHeader(a);
      if (first) refreshAll();
    },
    onStatus: onState,
    onMode: (m) => (document.getElementById("mode").textContent = modeBadge(m)),
  });
}

init().catch((e) => toast(e.message, true));
