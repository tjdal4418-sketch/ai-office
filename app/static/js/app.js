// 공통 유틸: API 호출, 상태 표시 정보, 테마, 실시간 연결(SSE → 실패 시 2초 폴링)
const STATUS_META = {
  idle:    { icon: "🟢", label: "대기중" },
  working: { icon: "🔵", label: "일하는중" },
  review:  { icon: "🟡", label: "검토필요" },
  done:    { icon: "✅", label: "완료" },
  error:   { icon: "🔴", label: "오류" },
};

const TASK_STATUS_LABEL = {
  queued: "대기", working: "작업중", review: "검토필요", done: "승인완료", error: "오류",
};

function esc(text) {
  const d = document.createElement("div");
  d.textContent = text ?? "";
  return d.innerHTML;
}

async function api(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const res = await fetch(path, {
    ...options,
    headers: isForm ? {} : { "Content-Type": "application/json" },
    body: options.body && !isForm ? JSON.stringify(options.body) : options.body,
  });
  if (!res.ok) {
    let msg = `요청 실패 (${res.status})`;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

function toast(message, isError = false) {
  const el = document.createElement("div");
  el.className = "toast " + (isError ? "toast-error" : "toast-ok");
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

function fmtTime(iso) {
  if (!iso) return "-";
  return String(iso).replace("T", " ").slice(0, 19);
}
function fmtHM(iso) {
  return iso ? String(iso).replace("T", " ").slice(11, 16) : "";
}
function fmtSize(n) {
  return n < 1024 ? `${n}B` : n < 1048576 ? `${(n / 1024).toFixed(1)}KB` : `${(n / 1048576).toFixed(1)}MB`;
}

// 다크모드: 기본은 시스템 설정, 버튼으로 수동 전환(브라우저에 기억)
function initTheme(buttonId) {
  const root = document.documentElement;
  try { const saved = localStorage.getItem("theme"); if (saved) root.dataset.theme = saved; } catch (_) {}
  const btn = document.getElementById(buttonId);
  if (!btn) return;
  btn.addEventListener("click", () => {
    const isDark = root.dataset.theme
      ? root.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = isDark ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (_) {}
  });
}

// 실시간 연결: SSE 우선, 끊기면 2초 폴링으로 대체하고 재접속되면 SSE로 복귀
function connectEvents({ onSnapshot, onStatus, onActivity, onMode, onPoll }) {
  let pollTimer = null;
  const startPolling = () => {
    if (pollTimer) return;
    onMode("poll");
    pollTimer = setInterval(async () => {
      try { onSnapshot(await api("/api/agents")); if (onPoll) onPoll(); } catch (_) {}
    }, 2000);
  };
  const stopPolling = () => { clearInterval(pollTimer); pollTimer = null; };

  if (!window.EventSource) { startPolling(); return; }
  const es = new EventSource("/api/events");
  es.addEventListener("snapshot", (e) => { stopPolling(); onMode("sse"); onSnapshot(JSON.parse(e.data)); if (onPoll) onPoll(); });
  es.addEventListener("status", (e) => onStatus(JSON.parse(e.data)));
  if (onActivity) es.addEventListener("activity", (e) => onActivity(JSON.parse(e.data)));
  es.onerror = () => startPolling(); // EventSource 가 자동 재접속 → 성공하면 snapshot 으로 폴링 해제
}

function modeBadge(mode) {
  return mode === "sse" ? "🟢 실시간 연결" : "🟠 폴링 중(2초)";
}
