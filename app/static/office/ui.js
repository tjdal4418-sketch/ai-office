// 오피스 뷰 — 화면 위에 얹는 요소: 툴팁, 이름표, 호버 테두리, 격자 디버그 (4단계에서 말풍선·사이드 패널 추가)
// 글자는 확대된 캔버스 위(화면 해상도)에 그려야 또렷하다. 아이콘은 8×8 픽셀 스프라이트를 정수배로 확대한다.
const OfficeUI = (() => {
  const FONT = '"Galmuri11","Galmuri9","DotGothic16","Malgun Gothic","Apple SD Gothic Neo",sans-serif';
  const tipEl = document.getElementById("tip");

  function showTip(text, clientX, clientY) {
    tipEl.textContent = text;
    tipEl.hidden = false;
    const pad = 14, w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    tipEl.style.left = Math.min(clientX + pad, innerWidth - w - 8) + "px";
    tipEl.style.top = Math.min(clientY + pad, innerHeight - h - 8) + "px";
  }
  function hideTip() { tipEl.hidden = true; }

  // 이름표: (cx, topY) 는 논리 좌표(16px 타일 기준), 크기는 scale 배
  function drawNameplate(ctx, scale, cx, topY, iconKind, text) {
    const fs = Math.max(10, 6 * scale);
    ctx.font = `${fs}px ${FONT}`;
    const iconSize = 8 * scale, gap = 2 * scale, padX = 2 * scale;
    const tw = ctx.measureText(text).width;
    const w = Math.ceil(padX * 2 + iconSize + gap + tw), h = Math.ceil(Math.max(9 * scale, fs + 2 * scale));
    const x = Math.round(cx * scale - w / 2), y = Math.round(topY * scale);
    ctx.fillStyle = "#6b4a26"; ctx.fillRect(x - scale, y - scale, w + 2 * scale, h + 2 * scale);
    ctx.fillStyle = "#f6f0dc"; ctx.fillRect(x, y, w, h);
    ctx.fillStyle = "#e6dcbd"; ctx.fillRect(x, y + h - scale, w, scale);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(Sprites.icon(iconKind), x + padX, y + Math.round((h - iconSize) / 2), iconSize, iconSize);
    ctx.fillStyle = "#3a2a15"; ctx.textBaseline = "middle"; ctx.textAlign = "left";
    ctx.fillText(text, x + padX + iconSize + gap, y + h / 2 + 1);
  }

  // 책상마다 이름표를 책상 앞 모서리에 붙인다
  function drawNameplates(ctx, scale) {
    for (const f of FURNITURE) {
      if (!f.agent) continue;
      const v = AGENT_VIEW[f.agent];
      drawNameplate(ctx, scale, (f.x + f.w / 2) * TILE, (f.y + f.h) * TILE - 7, v.icon, v.name);
    }
  }


  // 말풍선: (cx, tipY) 는 꼬리 끝(논리 좌표).
  // spec = { text?, icon?, progress?(0~100 → 막대), maxW?, bg?, border?, color? }
  function drawBubble(ctx, scale, cx, tipY, spec) {
    const fs = Math.max(10, 6 * scale);
    ctx.font = `${fs}px ${FONT}`;
    const pad = 2 * scale + 1, iconSize = 8 * scale, maxW = (spec.maxW || 78) * scale;
    const lines = [];
    if (spec.text) { // 한글은 띄어쓰기가 적어서 글자 단위로 줄바꿈
      let cur = "";
      for (const ch of spec.text) {
        if (ctx.measureText(cur + ch).width > maxW && cur) { lines.push(cur); cur = ch; } else cur += ch;
      }
      lines.push(cur);
    }
    const textW = lines.reduce((m, l) => Math.max(m, ctx.measureText(l).width), 0);
    const lh = fs + 2;
    const hasBar = spec.progress != null;
    const pct = hasBar ? Math.max(0, Math.min(100, Math.round(spec.progress))) : 0;
    const pctText = hasBar ? pct + "%" : "", pctW = hasBar ? ctx.measureText(pctText).width : 0;
    const barH = 4 * scale, barGap = 3 * scale;
    const barW = hasBar ? Math.max(36 * scale, textW - pctW - barGap) : 0;
    const rowW = hasBar ? barW + barGap + pctW : 0;
    const contentW = Math.max(textW + (spec.icon ? iconSize + (spec.text ? 2 * scale : 0) : 0), rowW);
    const w = Math.ceil(pad * 2 + contentW);
    const topH = Math.max(spec.icon ? iconSize : 0, lines.length * lh);
    const h = Math.ceil(pad * 2 + topH + (hasBar ? barGap + Math.max(barH, fs - 2) : 0));
    const tail = 3 * scale;
    let x = Math.round(cx * scale - w / 2);
    x = Math.max(4, Math.min(ctx.canvas.width - w - 4, x));
    const y = Math.round(tipY * scale - h - tail);
    const border = spec.border || "#2a2a3a", bg = spec.bg || "#ffffff";
    ctx.fillStyle = border; ctx.fillRect(x - scale, y - scale, w + 2 * scale, h + 2 * scale);
    ctx.fillStyle = bg; ctx.fillRect(x, y, w, h);
    const tx = Math.max(x + 2 * scale, Math.min(x + w - 4 * scale, Math.round(cx * scale)));
    for (let i = 0; i < 3; i++) { // 계단식 꼬리
      ctx.fillStyle = border; ctx.fillRect(tx - (2 - i) * scale - scale, y + h + i * scale, (2 - i) * 2 * scale + 2 * scale, scale);
    }
    ctx.fillStyle = bg; ctx.fillRect(tx - scale, y + h, 2 * scale, scale); ctx.fillRect(tx - scale, y + h + scale, 2 * scale, scale);
    let ix = x + pad;
    if (spec.icon) { ctx.imageSmoothingEnabled = false; ctx.drawImage(Sprites.icon(spec.icon), ix, y + pad + Math.round((topH - iconSize) / 2), iconSize, iconSize); ix += iconSize + (spec.text ? 2 * scale : 0); }
    ctx.fillStyle = spec.color || "#23232f"; ctx.textBaseline = "top"; ctx.textAlign = "left";
    lines.forEach((l, i) => ctx.fillText(l, ix, y + pad + i * lh + 1));
    if (hasBar) {
      const by = y + pad + topH + barGap, bx = x + pad;
      ctx.fillStyle = "#c9cde0"; ctx.fillRect(bx, by, barW, barH);
      ctx.fillStyle = spec.barColor || "#3d7fd9"; ctx.fillRect(bx, by, Math.round(barW * pct / 100), barH);
      ctx.fillStyle = "#23232f"; ctx.textBaseline = "middle"; ctx.fillText(pctText, bx + barW + barGap, by + barH / 2 + 1);
    }
    return { x, y, w, h };
  }

  function drawHover(ctx, scale, f, t) {
    if (!f) return;
    const pad = 1;
    ctx.save();
    ctx.strokeStyle = "#ffe27a"; ctx.lineWidth = Math.max(1, scale);
    ctx.setLineDash([2 * scale, 2 * scale]); ctx.lineDashOffset = -(t / 120) * scale;
    ctx.strokeRect((f.x * TILE - pad) * scale + 0.5, (f.y * TILE - pad) * scale + 0.5, (f.w * TILE + pad * 2) * scale, (f.h * TILE + pad * 2) * scale);
    ctx.restore();
  }

  // 격자 보기(G): 막힌 칸=빨강, 앉는 자리=초록, 걸어다니는 자리=파랑. 2단계 길찾기 확인용
  function drawDebug(ctx, scale) {
    ctx.save();
    for (let y = 0; y < ROWS; y++) for (let x = 0; x < COLS; x++) {
      if (GRID[y][x]) { ctx.fillStyle = "rgba(220,50,50,.30)"; ctx.fillRect(x * TILE * scale, y * TILE * scale, TILE * scale, TILE * scale); }
      ctx.strokeStyle = "rgba(255,255,255,.12)"; ctx.lineWidth = 1;
      ctx.strokeRect(x * TILE * scale + 0.5, y * TILE * scale + 0.5, TILE * scale, TILE * scale);
    }
    const dot = (p, col) => { ctx.fillStyle = col; ctx.beginPath(); ctx.arc((p.x + 0.5) * TILE * scale, (p.y + 0.5) * TILE * scale, 3 * scale, 0, 7); ctx.fill(); };
    Object.values(SEATS.desk).forEach((p) => dot(p, "#3ddc84"));
    SEATS.meeting.forEach((p) => dot(p, "#3ddc84"));
    SEATS.lounge.forEach((p) => dot(p, "#3ddc84"));
    [...SEATS.loungeWalk, ...SEATS.pantryWalk, SEATS.coffee, SEATS.printer, SEATS.door, SEATS.water, ...Object.values(SEATS.review), ...SEATS.plants, ...SEATS.windowSpots, ...SEATS.shelf]
      .forEach((p) => dot(p, "#4aa3ff"));
    ctx.restore();
  }

  return { showTip, hideTip, drawNameplate, drawBubble, drawNameplates, drawHover, drawDebug };
})();
