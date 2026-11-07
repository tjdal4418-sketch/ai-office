// 오피스 뷰 — 스프라이트 그리기 (외부 이미지 없이 코드로 픽셀을 직접 그림)
// 모든 그리기 함수는 논리 좌표(1칸 = 16px)의 캔버스 컨텍스트에 그린다. 나중에 CC0 에셋(예: Kenney)으로 바꾸려면
// 이 파일의 Sprites.draw.* 함수만 drawImage 기반으로 교체하면 된다(맵·움직임 코드는 그대로).
const Sprites = (() => {
  const P = {
    // 바닥·벽
    plankA: "#c98b4f", plankB: "#c2834a", plankHi: "#d79d62", plankSeam: "#a56d39",
    wallFace: "#34426e", wallTop: "#27325a", wallHi: "#4a5b92", wallBase: "#222b4d", wallDark: "#1c2442", wallDarkHi: "#34426e",
    pantryA: "#eef1f3", pantryB: "#e5e9ec", pantryLine: "#cbd3da",
    carpet: "#d8a363", carpetBorder: "#b4814a", carpetIn: "#dfae72", carpetDot: "#c99356",
    // 가구
    deskTop: "#9b6b3e", deskHi: "#b58150", deskEdge: "#5d3b1f", deskFront: "#76502c", deskDark: "#3d2715", deskHandle: "#d9c08a",
    tableTop: "#8f6036", tableHi: "#aa7640", tableEdge: "#60401f",
    chair: "#2c3a66", chairHi: "#41518a", chairDark: "#1e2848", wheel: "#10162b",
    sofa: "#76459a", sofaHi: "#8c58b1", sofaDark: "#4f2c68", sofaSeam: "#5e3680",
    shadow: "rgba(30,15,5,.22)",
    metal: "#d3d6db", metalDark: "#9aa1ab", black: "#1d1e26",
  };

  // ---------- 그리기 도구 ----------
  const R = (c, col, x, y, w, h) => { c.fillStyle = col; c.fillRect(Math.round(x), Math.round(y), Math.round(w), Math.round(h)); };
  const px = (c, col, x, y) => R(c, col, x, y, 1, 1);
  function disc(c, col, cx, cy, r) {
    c.fillStyle = col;
    for (let y = -r; y <= r; y++) for (let x = -r; x <= r; x++) if (x * x + y * y <= r * r + r * 0.4) c.fillRect(cx + x, cy + y, 1, 1);
  }
  function line(c, col, x0, y0, x1, y1) { // 브레젠햄 직선
    c.fillStyle = col;
    x0 |= 0; y0 |= 0; x1 |= 0; y1 |= 0;
    const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    let err = dx + dy;
    for (;;) {
      c.fillRect(x0, y0, 1, 1);
      if (x0 === x1 && y0 === y1) break;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x0 += sx; }
      if (e2 <= dx) { err += dx; y0 += sy; }
    }
  }
  function rng(seed) { // 항상 같은 모양이 나오도록 하는 의사난수
    let a = seed >>> 0;
    return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }

  // ---------- 바닥·벽·카펫 ----------
  function floor(c) {
    const x0 = FLOOR.x0 * TILE, x1 = (FLOOR.x1 + 1) * TILE, y0 = FLOOR.y0 * TILE, y1 = (FLOOR.y1 + 1) * TILE;
    for (let y = y0, i = 0; y < y1; y += 4, i++) {
      R(c, i % 2 ? P.plankA : P.plankB, x0, y, x1 - x0, 4);
      R(c, P.plankHi, x0, y, x1 - x0, 1);
      R(c, P.plankSeam, x0, y + 3, x1 - x0, 1);
      const off = (i * 23) % 56; // 판자 이음매를 어긋나게
      for (let x = x0 + off; x < x1; x += 56) R(c, P.plankSeam, x, y, 1, 4);
    }
    R(c, "rgba(40,20,5,.28)", x0, y0, x1 - x0, 4); // 위쪽 벽 아래 그림자
  }
  function walls(c) {
    const W = COLS * TILE, H = ROWS * TILE, T = FLOOR.y0 * TILE; // T = 위쪽 벽 높이
    R(c, P.wallFace, 0, 0, W, T - 8); R(c, P.wallTop, 0, 0, W, 6); R(c, P.wallHi, 0, 6, W, 1);
    for (let x = 0; x < W; x += 32) R(c, "rgba(255,255,255,.04)", x, 7, 16, T - 15); // 벽지 줄무늬
    R(c, P.wallHi, 0, T - 8, W, 1); R(c, P.wallBase, 0, T - 7, W, 7);
    R(c, P.wallDark, 0, T, 16, H - T); R(c, P.wallDarkHi, 15, T, 1, H - T - 32);
    R(c, P.wallDark, W - 16, T, 16, H - T); R(c, P.wallDarkHi, W - 16, T, 1, H - T - 32);
    R(c, P.wallDark, 0, H - 32, W, 32); R(c, P.wallDarkHi, 16, H - 32, W - 32, 1);
    R(c, P.wallDarkHi, 0, T - 1, 16, 1); R(c, P.wallDarkHi, W - 16, T - 1, 16, 1);
  }
  function pantryFloor(c) {
    const z = ZONES.pantry, x = z.x * TILE, y = z.y * TILE;
    for (let ty = 0; ty < z.h; ty++) for (let tx = 0; tx < z.w; tx++) {
      R(c, (tx + ty) % 2 ? P.pantryA : P.pantryB, x + tx * TILE, y + ty * TILE, TILE, TILE);
      R(c, P.pantryLine, x + tx * TILE, y + ty * TILE, TILE, 1); R(c, P.pantryLine, x + tx * TILE, y + ty * TILE, 1, TILE);
    }
    R(c, P.pantryLine, x, y + z.h * TILE - 1, z.w * TILE, 1);
    R(c, "rgba(60,40,20,.25)", x - 1, y, 1, z.h * TILE); // 나무 바닥과의 경계
  }
  function carpet(c) {
    const z = ZONES.lounge, x = z.x * TILE, y = z.y * TILE, w = (FLOOR.x1 + 1 - z.x) * TILE, h = (FLOOR.y1 + 1 - z.y) * TILE;
    R(c, P.carpetBorder, x, y, w, h); R(c, P.carpet, x + 3, y + 3, w - 6, h - 6); R(c, P.carpetIn, x + 8, y + 8, w - 16, h - 16);
    const r = rng(7);
    for (let i = 0; i < 90; i++) px(c, P.carpetDot, x + 10 + Math.floor(r() * (w - 20)), y + 10 + Math.floor(r() * (h - 20)));
    for (let xx = x + 8; xx < x + w - 8; xx += 8) R(c, P.carpetBorder, xx, y + 5, 3, 1), R(c, P.carpetBorder, xx, y + h - 6, 3, 1);
  }

  // ---------- 벽 장식 ----------
  function window_(c, x, y, w, h, f, env) {
    const hour = env.hour;
    const phase = hour >= 6 && hour < 17 ? "day" : hour >= 17 && hour < 20 ? "dusk" : "night";
    const sky = { day: ["#7fc4ee", "#a9dcf6", "#cdeefb"], dusk: ["#6a4f9e", "#e07f6b", "#f6b36e"], night: ["#0d1233", "#161c4a", "#232c63"] }[phase];
    R(c, "#e9eaf0", x, y, w, h); R(c, "#b9bccb", x, y + h - 2, w, 2);
    const ix = x + 3, iy = y + 3, iw = w - 6, ih = h - 7;
    const bands = 3, bh = Math.ceil(ih / bands);
    sky.forEach((col, i) => R(c, col, ix, iy + i * bh, iw, bh));
    const r = rng(11);
    if (phase === "night") for (let i = 0; i < 9; i++) px(c, "#fff6c9", ix + 2 + Math.floor(r() * (iw - 4)), iy + 1 + Math.floor(r() * 12));
    if (phase === "day") { R(c, "#fff", ix + 6, iy + 5, 10, 3); R(c, "#fff", ix + 9, iy + 3, 7, 3); R(c, "#fff", ix + iw - 20, iy + 8, 12, 3); }
    if (phase === "dusk") disc(c, "#ffd08a", ix + iw - 10, iy + ih - 12, 5);
    // 도시 풍경
    const sil = phase === "day" ? "#6f84a8" : phase === "dusk" ? "#3a3560" : "#0a0e26";
    let bx = ix;
    const rb = rng(5);
    while (bx < ix + iw) {
      const bw = 5 + Math.floor(rb() * 6), bhh = 8 + Math.floor(rb() * 14);
      R(c, sil, bx, iy + ih - bhh, Math.min(bw, ix + iw - bx), bhh);
      if (phase !== "day") for (let wy = iy + ih - bhh + 2; wy < iy + ih - 2; wy += 4) for (let wx = bx + 1; wx < bx + bw - 1 && wx < ix + iw; wx += 3) if (rb() > 0.45) px(c, "#ffe08a", wx, wy);
      bx += bw + 1;
    }
    R(c, "#e9eaf0", x + Math.floor(w / 2) - 1, iy, 2, ih); R(c, "#e9eaf0", ix, iy + Math.floor(ih / 2), iw, 2);
    R(c, "rgba(255,255,255,.18)", ix + 2, iy, 4, ih); // 유리 반사
  }
  function whiteboard(c, x, y, w, h) {
    R(c, "rgba(0,0,0,.18)", x + 2, y + h, w - 2, 2);
    R(c, "#aab1bc", x, y, w, h); R(c, "#e8ecf1", x + 1, y + 1, w - 2, 1);
    R(c, "#f5f7f9", x + 3, y + 3, w - 6, h - 8);
    for (let i = 0; i < 4; i++) for (let j = 0; j < 2; j++) R(c, "#cfd6de", x + 6 + i * 8, y + 5 + j * 8, 6, 6); // 미니 달력
    R(c, "#e0524f", x + 6 + 2 * 8, y + 5 + 8, 6, 6); // 오늘 표시(빨강)
    R(c, "#3d7fd9", x + 44, y + 5, 40, 2); R(c, "#3d7fd9", x + 44, y + 10, 28, 2); R(c, "#2a9d5c", x + 44, y + 15, 34, 2); R(c, "#e0524f", x + 44, y + 20, 18, 2);
    R(c, "#868d98", x + 8, y + h - 5, w - 16, 3); R(c, "#e0524f", x + 12, y + h - 6, 6, 2); R(c, "#3d7fd9", x + 22, y + h - 6, 6, 2);
  }
  function clock(c, x, y, w, h, f, env) {
    disc(c, "#4a4f5c", x + 8, y + 8, 7); disc(c, "#f4f4f2", x + 8, y + 8, 6);
    for (const [dx, dy] of [[0, -5], [5, 0], [0, 5], [-5, 0]]) px(c, "#444", x + 8 + dx, y + 8 + dy);
    const m = env.minute, hh = (env.hour % 12) + m / 60;
    const ang = (a, len) => [x + 8 + Math.round(Math.sin(a) * len), y + 8 - Math.round(Math.cos(a) * len)];
    const [mx, my] = ang((m / 60) * Math.PI * 2, 5), [hx, hy] = ang((hh / 12) * Math.PI * 2, 3);
    line(c, "#222", x + 8, y + 8, mx, my); line(c, "#c0392b", x + 8, y + 8, hx, hy); px(c, "#222", x + 8, y + 8);
  }
  function bookshelf(c, x, y, w, h, f) {
    R(c, "rgba(0,0,0,.2)", x + 1, y + h, w, 2);
    R(c, "#7b4c2a", x, y, w, h); R(c, "#4b2e1a", x + 2, y + 2, w - 4, h - 4);
    const r = rng(f.x * 31 + f.y);
    const cols = ["#c0392b", "#2e6fb5", "#e0a526", "#3a9d5d", "#8e5ac8", "#e8e2d0", "#d9622b", "#2b8fa0"];
    for (let s = 0; s < 3; s++) {
      const sy = y + 2 + s * 14;
      let bx = x + 3;
      while (bx < x + w - 5) {
        const bw = 2 + Math.floor(r() * 3), bh = 8 + Math.floor(r() * 4);
        R(c, cols[Math.floor(r() * cols.length)], bx, sy + 12 - bh, bw, bh);
        R(c, "rgba(255,255,255,.25)", bx, sy + 12 - bh, 1, bh);
        bx += bw;
      }
      R(c, "#7b4c2a", x + 2, sy + 12, w - 4, 2);
    }
  }
  function tv(c, x, y, w, h) {
    R(c, "rgba(0,0,0,.25)", x + 2, y + h, w - 2, 2);
    R(c, P.black, x, y, w, h); R(c, "#26406f", x + 2, y + 2, w - 4, h - 5);
    R(c, "#37599a", x + 2, y + 2, w - 4, 3);
    for (let i = 0; i < 3; i++) R(c, "#8fb3ff", x + 5, y + 8 + i * 5, 14 + (i * 13) % 26, 2); // 로그 줄 느낌
    R(c, "#e0524f", x + w - 7, y + h - 4, 2, 1);
  }
  function plant(c, x, y) {
    R(c, P.shadow, x + 2, y + 29, 12, 3);
    R(c, "#b4631f", x + 3, y + 21, 10, 9); R(c, "#cc7a2c", x + 2, y + 20, 12, 3); R(c, "#8c4a14", x + 4, y + 28, 8, 2);
    const g = ["#2d7a3a", "#3f9a4a", "#58b85c"];
    const blob = (cx, cy, rr, col) => disc(c, col, x + cx, y + cy, rr);
    blob(8, 9, 6, g[0]); blob(4, 12, 4, g[1]); blob(12, 12, 4, g[1]); blob(8, 6, 4, g[2]); blob(6, 15, 3, g[2]); blob(11, 15, 3, g[0]);
    px(c, "#9fe0a0", x + 7, y + 4); px(c, "#9fe0a0", x + 5, y + 10);
  }

  // ---------- 책상·의자·회의 ----------
  function monitor(c, x, y, screen, glare = true) { // x,y = 모니터 전체의 왼쪽 위
    R(c, "#24252d", x, y, 20, 14); R(c, screen, x + 2, y + 2, 16, 10);
    if (glare) { R(c, "rgba(255,255,255,.14)", x + 2, y + 2, 5, 3); px(c, "rgba(255,255,255,.2)", x + 3, y + 5); }
    R(c, "#9aa0aa", x + 8, y + 14, 4, 3); R(c, "#b7bcc6", x + 5, y + 17, 10, 2);
  }
  function desk(c, x, y, w, h, f) { // 3×2칸 = 48×32
    R(c, P.shadow, x + 1, y + h - 1, w - 2, 3);
    R(c, P.deskTop, x, y + 4, w, 17); R(c, P.deskHi, x, y + 4, w, 2); R(c, P.deskEdge, x, y + 21, w, 2);
    R(c, P.deskFront, x + 1, y + 23, w - 2, 8); R(c, P.deskDark, x + 17, y + 24, w - 34, 7);
    for (const dx of [3, w - 15]) { R(c, P.deskDark, x + dx, y + 24, 12, 7); R(c, P.deskFront, x + dx + 1, y + 25, 10, 5); R(c, P.deskHandle, x + dx + 4, y + 27, 4, 1); }
    monitor(c, x + 14, y - 8, "#10141c");
    R(c, "#cfd4db", x + 14, y + 12, 20, 5); R(c, "#9aa0aa", x + 14, y + 16, 20, 1); R(c, "#e8ebef", x + 36, y + 13, 4, 5); // 키보드·마우스
    R(c, "#f7f7f7", x + 4, y + 8, 6, 6); R(c, "#b04a3a", x + 5, y + 9, 4, 1); R(c, "#f7f7f7", x + 10, y + 10, 2, 3); // 머그컵
    R(c, "#f2f2ee", x + 39, y + 6, 7, 9); R(c, "#c4c8cf", x + 40, y + 8, 5, 1); R(c, "#c4c8cf", x + 40, y + 10, 5, 1);
  }
  function manager_desk(c, x, y, w, h) { // 6×2칸 = 96×32
    R(c, P.shadow, x + 1, y + h - 1, w - 2, 3);
    R(c, P.deskTop, x, y + 4, w, 17); R(c, P.deskHi, x, y + 4, w, 2); R(c, P.deskEdge, x, y + 21, w, 2);
    R(c, P.deskFront, x + 1, y + 23, w - 2, 8); R(c, P.deskDark, x + 30, y + 24, w - 60, 7);
    for (const dx of [4, w - 20]) { R(c, P.deskDark, x + dx, y + 24, 16, 7); R(c, P.deskFront, x + dx + 1, y + 25, 14, 5); R(c, P.deskHandle, x + dx + 6, y + 27, 4, 1); }
    monitor(c, x + 20, y - 8, "#10141c"); monitor(c, x + 54, y - 8, "#10141c");
    R(c, "#cfd4db", x + 24, y + 12, 18, 5); R(c, "#cfd4db", x + 56, y + 12, 18, 5);
    R(c, "#f7f7f7", x + 8, y + 8, 6, 6); R(c, "#2f3f6e", x + 9, y + 9, 4, 1);
    R(c, "#3a3d46", x + 80, y + 9, 10, 8); R(c, "#6f7686", x + 82, y + 10, 6, 3); // 전화기
    R(c, "#f2f2ee", x + 4, y + 14, 8, 4);
  }
  function chair(c, x, y, w, h, f) { // 16×16 칸 안의 사무용 의자 (facing: 앉은 사람이 보는 방향)
    R(c, "rgba(30,15,5,.2)", x + 3, y + 13, 11, 2);
    const back = f.facing === "down" ? [x + 3, y + 1] : [x + 3, y + 11];
    const seatY = f.facing === "down" ? y + 4 : y + 3;
    R(c, P.chair, x + 3, seatY, 10, 9); R(c, P.chairHi, x + 3, seatY, 10, 2);
    R(c, P.chairDark, back[0], back[1], 10, 3); R(c, P.chairHi, back[0], back[1], 10, 1);
    for (const dx of [3, 12]) px(c, P.wheel, x + dx, y + 14);
  }
  function meeting_table(c, x, y, w, h) { // 7×3칸 = 112×48
    R(c, P.shadow, x + 2, y + h - 4, w - 3, 5);
    R(c, P.tableTop, x + 2, y, w - 4, h - 6); R(c, P.tableTop, x, y + 2, w, h - 10);
    R(c, P.tableHi, x + 3, y + 1, w - 6, 2); R(c, P.tableEdge, x + 2, y + h - 6, w - 4, 3); R(c, P.tableEdge, x, y + h - 9, 2, 3); R(c, P.tableEdge, x + w - 2, y + h - 9, 2, 3);
    R(c, "#f4f4f0", x + 14, y + 10, 10, 12); R(c, "#f4f4f0", x + 30, y + 14, 9, 11); R(c, "#d9dde3", x + 56, y + 12, 16, 10); R(c, "#3b4252", x + 58, y + 13, 12, 7); // 서류·노트북
    R(c, "#78c4ea", x + 86, y + 12, 4, 9); R(c, "#4a9cc8", x + 86, y + 12, 4, 2);
  }

  // ---------- 라운지·탕비실·기타 ----------
  function sofa(c, x, y, w, h, f) {
    R(c, P.shadow, x + 1, y + h - 1, w - 2, 3);
    R(c, P.sofaDark, x, y, w, h);
    const face = f.facing;
    if (face === "down" || face === "up") {
      const back = face === "down" ? y + 1 : y + h - 11, seat = face === "down" ? y + 11 : y + 1;
      R(c, P.sofa, x + 3, back, w - 6, 10); R(c, P.sofaHi, x + 3, back, w - 6, 2);
      const n = Math.max(1, Math.round(w / 32)), cw = (w - 6) / n;
      for (let i = 0; i < n; i++) { R(c, P.sofaHi, x + 3 + i * cw + 1, seat, cw - 2, h - 12); R(c, P.sofa, x + 3 + i * cw + 1, seat + (h - 12) - 3, cw - 2, 3); if (i) R(c, P.sofaSeam, x + 3 + i * cw, seat, 1, h - 12); }
      R(c, P.sofa, x + 1, y + 2, 3, h - 4); R(c, P.sofa, x + w - 4, y + 2, 3, h - 4); // 팔걸이
    } else { // left: 서쪽을 보고 앉음 (등받이는 오른쪽)
      R(c, P.sofa, x + w - 11, y + 3, 10, h - 6); R(c, P.sofaHi, x + w - 11, y + 3, 2, h - 6);
      const n = Math.max(1, Math.round(h / 32)), ch = (h - 6) / n;
      for (let i = 0; i < n; i++) { R(c, P.sofaHi, x + 1, y + 3 + i * ch + 1, w - 12, ch - 2); if (i) R(c, P.sofaSeam, x + 1, y + 3 + i * ch, w - 12, 1); }
      R(c, P.sofa, x + 2, y + 1, w - 4, 3); R(c, P.sofa, x + 2, y + h - 4, w - 4, 3);
    }
  }
  function glass_table(c, x, y) {
    disc(c, "rgba(30,15,5,.2)", x + 17, y + 18, 15); disc(c, "#e8f6fa", x + 16, y + 16, 15); disc(c, "#b9e0ec", x + 16, y + 16, 13);
    R(c, "rgba(255,255,255,.7)", x + 8, y + 8, 6, 2); px(c, "#fff", x + 7, y + 10); R(c, "#f7f7f7", x + 18, y + 14, 5, 5); R(c, "#7b4c2a", x + 19, y + 15, 3, 3);
  }
  function water_cooler(c, x, y) {
    R(c, P.shadow, x + 2, y + 29, 12, 3);
    R(c, "#4aa3d6", x + 4, y + 1, 8, 2); R(c, "#74c8f4", x + 3, y + 3, 10, 11); R(c, "#b9e6fb", x + 5, y + 4, 2, 8);
    R(c, "#d8dde3", x + 2, y + 14, 12, 15); R(c, "#bcc3cc", x + 2, y + 14, 12, 2);
    R(c, "#2f7dc4", x + 5, y + 18, 2, 3); R(c, "#e0524f", x + 9, y + 18, 2, 3); R(c, "#8f98a3", x + 4, y + 25, 8, 2);
  }
  function coffee_counter(c, x, y, w, h) { // 2×2칸
    R(c, P.shadow, x + 1, y + h - 1, w - 2, 3);
    R(c, "#d7c7a3", x, y + 12, w, 19); R(c, "#efe3c5", x, y + 10, w, 4); R(c, "#b9a77f", x, y + 28, w, 3);
    R(c, "#a89572", x + 3, y + 17, 12, 9); R(c, "#a89572", x + 17, y + 17, 12, 9); R(c, "#c9b88f", x + 3, y + 17, 12, 1);
    R(c, "#2e3138", x + 3, y, 15, 12); R(c, "#4a4e58", x + 3, y, 15, 2); R(c, "#d94141", x + 14, y + 4, 2, 2); R(c, "#7a808c", x + 7, y + 8, 5, 3);
    R(c, "#fff", x + 22, y + 5, 4, 5); R(c, "#fff", x + 27, y + 6, 3, 4);
  }
  function copier(c, x, y, w, h) { // 2×2칸
    R(c, P.shadow, x + 1, y + h - 1, w - 2, 3);
    R(c, "#cfd3d8", x, y + 8, w, 22); R(c, "#e2e5e9", x, y + 8, w, 2);
    R(c, "#3b3f48", x + 1, y + 3, w - 2, 7); R(c, "#4d525e", x + 2, y + 4, w - 4, 2);
    R(c, "#2d5b8a", x + 21, y + 13, 8, 5); R(c, "#7fb3e6", x + 22, y + 14, 6, 1); R(c, "#e0524f", x + 21, y + 20, 2, 2); R(c, "#2a9d5c", x + 25, y + 20, 2, 2);
    R(c, "#a9afb8", x + 3, y + 22, 22, 6); R(c, "#f4f4f0", x + 5, y + 23, 18, 4); // 출력 트레이·종이
  }
  function trash(c, x, y) {
    R(c, P.shadow, x + 3, y + 13, 10, 2); R(c, "#8e949c", x + 3, y + 5, 10, 9); R(c, "#b3b9c1", x + 2, y + 3, 12, 3); R(c, "#6f757d", x + 5, y + 7, 1, 6); R(c, "#6f757d", x + 9, y + 7, 1, 6);
  }
  function coat_rack(c, x, y) {
    R(c, P.shadow, x + 3, y + 29, 10, 3);
    R(c, "#6b4423", x + 7, y + 2, 2, 28); R(c, "#6b4423", x + 3, y + 4, 10, 2); R(c, "#4a2f17", x + 3, y + 28, 10, 3);
    R(c, "#2f3f6e", x + 3, y + 7, 6, 13); R(c, "#3f528a", x + 3, y + 7, 6, 2); R(c, "#c0392b", x + 10, y + 7, 3, 9);
  }
  function door(c, x, y, w, h) { // 4×2칸 = 64×32 (아래쪽 벽)
    R(c, "#14808a", x, y, w, h); R(c, "#0e5e66", x, y + h - 3, w, 3);
    for (const dx of [3, 33]) {
      R(c, "#37c5d0", x + dx, y + 2, 28, 27); R(c, "#8fe9ef", x + dx + 3, y + 5, 22, 20);
      R(c, "rgba(255,255,255,.55)", x + dx + 5, y + 6, 3, 18); R(c, "rgba(255,255,255,.35)", x + dx + 11, y + 6, 2, 18);
    }
    R(c, "#f0f0f0", x + 27, y + 14, 2, 7); R(c, "#f0f0f0", x + 35, y + 14, 2, 7); R(c, "#14808a", x + 31, y + 2, 2, 27);
  }
  function mat(c, x, y, w, h) {
    R(c, "#7a4a3a", x + 2, y + 4, w - 4, 10); R(c, "#9a6150", x + 3, y + 5, w - 6, 8);
    for (let i = x + 6; i < x + w - 6; i += 4) R(c, "#7a4a3a", i, y + 7, 2, 4);
  }

  // ---------- 오버레이(캐릭터 앞에 그려지는 것) ----------
  function glassPartition(c) { // 매니저 데스크 둘레의 유리 칸막이 (책상 좌우로 1칸씩 넓게)
    const d = FURNITURE.find((f) => f.id === "desk_manager");
    const x = (d.x - 1) * TILE, y = (d.y - 1) * TILE + 6, w = (d.w + 2) * TILE, h = (d.h + 1) * TILE + 12;
    R(c, "rgba(170,215,235,.20)", x, y, w, h);
    R(c, "rgba(235,248,255,.85)", x, y, w, 1); R(c, "rgba(235,248,255,.85)", x, y + h - 1, w, 1);
    R(c, "rgba(235,248,255,.85)", x, y, 1, h); R(c, "rgba(235,248,255,.85)", x + w - 1, y, 1, h);
    R(c, "rgba(255,255,255,.26)", x + 12, y + 2, 7, h - 4); R(c, "rgba(255,255,255,.18)", x + 24, y + 2, 3, h - 4);
    R(c, "rgba(255,255,255,.26)", x + w - 20, y + 2, 7, h - 4);
  }

  // ---------- 작은 아이콘 (이름표·말풍선용 8×8) ----------
  const ICONS = {
    coin: { pal: { o: "#b8860b", y: "#ffd23f" }, rows: ["..oooo..", ".oyyyyo.", "oyyoyyyo", "oyyoyyyo", "oyyoyyyo", "oyyyyyyo", ".oyyyyo.", "..oooo.."] },
    notebook: { pal: { r: "#e0a526", w: "#fff7d6", k: "#8a6a1a" }, rows: ["rrrrrrr.", "rwwwwwr.", "rwwkwwr.", "rwwwwwr.", "rwkkkwr.", "rwwwwwr.", "rwwwwwr.", "rrrrrrr."] },
    receipt: { pal: { w: "#f4f4f4", k: "#6b7280" }, rows: ["wwwwwww.", "wkkkkkw.", "wwwwwww.", "wkkkkww.", "wwwwwww.", "wkkkkkw.", "wwwwwww.", "w.w.w.w."] },
    building: { pal: { t: "#c9ccd3", b: "#8d96a8", w: "#ffffff" }, rows: ["..tttt..", ".tttttt.", "bbbbbbbb", ".bwbwbw.", ".bwbwbw.", ".bwbwbw.", ".bbbbbb.", "bbbbbbbb"] },
    cup: { pal: { w: "#f4f4f4", c: "#8b5a2b", s: "#cfd6de" }, rows: ["..s.s...", ".s.s....", "wwwwww..", "wccccwww", "wccccw.w", "wccccww.", ".wwww...", "........"] },
    drop: { pal: { b: "#4aa3d6", l: "#b9e6fb" }, rows: ["...b....", "..bbb...", "..bbb...", ".bbbbb..", ".bblbb..", ".bbbbb..", "..bbb...", "........"] },
    book: { pal: { r: "#c0392b", w: "#f6f0dc", k: "#7b241c" }, rows: ["rrrrrrr.", "rwwwwwk.", "rwkkkwk.", "rwwwwwk.", "rwkkkwk.", "rwwwwwk.", "rrrrrrr.", "........"] },
    leaf: { pal: { g: "#3f9a4a", d: "#2d7a3a" }, rows: ["....gg..", "...gggg.", "..gggggg", ".ggdgggg", ".ggdggg.", "..gdgg..", "...d....", "...d...."] },
    paper: { pal: { w: "#f4f4f4", k: "#9aa1ab" }, rows: [".wwwww..", ".wkkkw..", ".wwwww..", ".wkkkw..", ".wwwww..", ".wkkkw..", ".wwwww..", "........"] },
    check: { pal: { g: "#2a9d5c", l: "#7fe0a3" }, rows: ["........", ".......g", "......gg", "g....gg.", "gg..gg..", ".gggg...", "..gg....", "........"] },
    folder: { pal: { f: "#e0a526", y: "#ffd966" }, rows: ["fff.....", "fffffff.", "fyyyyyyf", "fyyyyyyf", "fyyyyyyf", "fyyyyyyf", "fyyyyyyf", "ffffffff"] },
  };
  const iconCache = {};
  function icon(kind) { // 8×8 캔버스(캐시). drawImage 로 정수배 확대해서 쓴다
    if (iconCache[kind]) return iconCache[kind];
    const def = ICONS[kind], cv = document.createElement("canvas");
    cv.width = cv.height = 8;
    const c = cv.getContext("2d");
    def.rows.forEach((row, y) => [...row].forEach((ch, x) => { if (def.pal[ch]) px(c, def.pal[ch], x, y); }));
    return (iconCache[kind] = cv);
  }


  // ---------- 캐릭터 (12×20, 발 아래 중앙이 (cx, fy)) ----------
  function shade(hex, amt) { // 색을 어둡게(amt<0) / 밝게(amt>0)
    const n = parseInt(hex.slice(1), 16);
    const f = (v) => Math.max(0, Math.min(255, Math.round(v + amt * 255)));
    return "#" + [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => f(v).toString(16).padStart(2, "0")).join("");
  }
  /** o: { dir:'up|down|left|right', frame:0|1|2(걷기), pose:'stand|walk|type|stretch|drink|mug|book|look', seated, hair, outfit, skin, style, t(초), carry } */
  function character(c, cx, fy, o) {
    const dir = o.dir || "down", pose = o.pose || "stand", seated = !!o.seated;
    const hair = o.hair, outfit = o.outfit, skin = o.skin, dark = shade(outfit, -0.16), pants = "#2b3350", shoe = "#171b2e";
    const walking = pose === "walk", f = o.frame || 0, t = o.t || 0;
    const bob = walking && f !== 0 ? 1 : 0;             // 걸을 때 위아래로 살짝
    const ox = cx - 6, oy = fy - 19 + (seated ? 5 : 0) - bob;
    const side = dir === "left" || dir === "right", sgn = dir === "left" ? -1 : 1;
    if (!seated) R(c, "rgba(30,15,5,.25)", ox + 2, fy - 1, 8, 2); // 바닥 그림자
    // 다리
    if (!seated) {
      const lyL = walking && f === 1 ? 1 : 0, lyR = walking && f === 2 ? 1 : 0;
      if (side) {
        const sw = walking ? (f === 1 ? 2 : f === 2 ? -2 : 0) * sgn : 0;
        R(c, pants, ox + 4 - sw, oy + 13, 3, 6 - lyL); R(c, shoe, ox + 4 - sw, oy + 18 - lyL, 4, 1);
        R(c, shade(pants, .06), ox + 6 + sw, oy + 13, 3, 6 - lyR); R(c, shoe, ox + 6 + sw, oy + 18 - lyR, 4, 1);
      } else {
        R(c, pants, ox + 3, oy + 13 + lyL, 3, 6 - lyL); R(c, shoe, ox + 3, oy + 18, 3, 1);
        R(c, pants, ox + 6, oy + 13 + lyR, 3, 6 - lyR); R(c, shoe, ox + 6, oy + 18, 3, 1);
      }
    } else if (dir === "down") { R(c, pants, ox + 2, oy + 13, 8, 2); R(c, shoe, ox + 2, oy + 15, 3, 1); R(c, shoe, ox + 7, oy + 15, 3, 1); }
    else if (side) R(c, pants, ox + (sgn < 0 ? 0 : 6), oy + 13, 6, 2);
    // 몸통
    R(c, outfit, ox + 2, oy + 7, 8, 6); R(c, dark, ox + 2, oy + 12, 8, 1); R(c, shade(outfit, .12), ox + 2, oy + 7, 8, 1);
    if (dir === "down") { R(c, shade(outfit, .2), ox + 5, oy + 7, 2, 2); }
    // 팔
    const swing = walking ? (f === 1 ? 1 : f === 2 ? -1 : 0) : 0;
    const armHands = (lx, ly, rx, ry) => { // 양팔(소매+손)
      R(c, outfit, lx, ly, 2, 5); R(c, skin, lx, ly + 5, 2, 1);
      R(c, outfit, rx, ry, 2, 5); R(c, skin, rx, ry + 5, 2, 1);
    };
    if (pose === "stretch") {
      const w = Math.floor(t * 4) % 2;
      R(c, outfit, ox + 0 - w, oy - 4, 2, 11); R(c, skin, ox + 0 - w, oy - 6, 2, 2);
      R(c, outfit, ox + 10 + w, oy - 4, 2, 11); R(c, skin, ox + 10 + w, oy - 6, 2, 2);
    } else if (pose === "type") {
      const k = Math.floor(t * 6) % 2;
      if (dir === "up") { R(c, outfit, ox + 0, oy + 8 - k, 2, 4); R(c, outfit, ox + 10, oy + 8 - (1 - k), 2, 4); }
      else armHands(ox + 0, oy + 8 + k, ox + 10, oy + 8 + (1 - k));
    } else if (pose === "drink") {
      armHands(ox + 0, oy + 8, ox + 10, oy + 3);
      R(c, "#f4f4f4", ox + 9, oy + 1, 4, 5); R(c, "#9ad4f5", ox + 10, oy + 2, 2, 3); // 물컵
    } else if (pose === "mug") {
      armHands(ox + 0, oy + 8, ox + 10, oy + 6);
      R(c, "#f4f4f4", ox + 10, oy + 8, 4, 4); R(c, "#7a4a22", ox + 11, oy + 8, 2, 1); R(c, "#f4f4f4", ox + 13, oy + 9, 1, 2);
    } else if (pose === "book") {
      armHands(ox + 0, oy + 8, ox + 10, oy + 8);
      R(c, "#c0392b", ox - 3, oy + 9, 5, 6); R(c, "#f6f0dc", ox - 2, oy + 10, 3, 4); R(c, "#7b241c", ox - 3, oy + 9, 1, 6);
    } else if (side) {
      R(c, dark, ox + 5, oy + 8 + swing, 3, 5); R(c, skin, ox + 5, oy + 13 + swing, 3, 1);
    } else {
      armHands(ox + 0, oy + 8 + (walking ? swing : 0), ox + 10, oy + 8 - (walking ? swing : 0));
    }
    if (o.carry === "paper") { R(c, "#f4f4f4", ox + 8, oy + 9, 5, 6); R(c, "#9aa1ab", ox + 9, oy + 10, 3, 1); R(c, "#9aa1ab", ox + 9, oy + 12, 3, 1); }
    // 머리
    const hb = pose === "look" ? (Math.floor(t * 1.2) % 3 === 0 ? -1 : 0) : 0; // 두리번거릴 때 고개를 살짝 듦
    const hy = oy + hb;
    R(c, skin, ox + 2, hy, 8, 7);
    const st = o.style || "short";
    if (dir === "up") { R(c, hair, ox + 2, hy, 8, 6); }
    else if (dir === "down") { R(c, hair, ox + 2, hy, 8, 3); R(c, hair, ox + 2, hy + 3, 1, 2); R(c, hair, ox + 9, hy + 3, 1, 2); px(c, "#222", ox + 4, hy + 4); px(c, "#222", ox + 7, hy + 4); }
    else { // 옆모습
      R(c, hair, ox + 2, hy, 8, 3);
      R(c, hair, ox + (dir === "left" ? 6 : 2), hy + 3, 4, 3);
      px(c, "#222", ox + (dir === "left" ? 4 : 7), hy + 4);
      px(c, shade(skin, -.12), ox + (dir === "left" ? 2 : 9), hy + 5);
    }
    if (st === "long") { if (dir !== "left" && dir !== "right") { R(c, hair, ox + 1, hy + 2, 1, 9); R(c, hair, ox + 10, hy + 2, 1, 9); } if (dir === "up") R(c, hair, ox + 2, hy + 6, 8, 5); }
    if (st === "bob") { R(c, hair, ox + 1, hy + 2, 2, 5); R(c, hair, ox + 9, hy + 2, 2, 5); if (dir === "up") R(c, hair, ox + 2, hy + 6, 8, 1); }
    if (st === "spiky") { px(c, hair, ox + 3, hy - 1); px(c, hair, ox + 6, hy - 2); px(c, hair, ox + 8, hy - 1); R(c, hair, ox + 5, hy - 1, 2, 1); }
  }

  // ---------- 상태 연출(동적): 모니터 화면, 복합기 종이 ----------
  /** 모니터 화면 16×10. mode: off | on(일하는중) | review(검토필요) | error(오류) */
  function monitorScreen(c, x, y, mode, t) {
    if (mode === "on") {
      R(c, "#1d3d6b", x, y, 16, 10);
      const k = Math.floor(t * 4);
      for (let i = 0; i < 4; i++) { // 위로 흘러가는 코드 줄
        const ly = (i * 3 + k) % 10, len = 4 + ((i * 7 + Math.floor(t * 2)) % 9);
        if (ly < 9) R(c, i % 2 ? "#6ee7b7" : "#8fb3ff", x + 2, y + ly, len, 1);
      }
      if (Math.floor(t * 8) % 11 === 0) R(c, "rgba(255,255,255,.14)", x, y, 16, 10); // 깜빡임
      if (Math.floor(t * 2) % 2) R(c, "#fff", x + 13, y + 7, 1, 2); // 커서
    } else if (mode === "review") {
      R(c, "#6b5320", x, y, 16, 10);
      if (Math.floor(t * 1.6) % 2) { R(c, "#ffe27a", x + 7, y + 1, 2, 5); R(c, "#ffe27a", x + 7, y + 7, 2, 2); }
    } else if (mode === "error") {
      const on = Math.floor(t * 2.5) % 2;
      R(c, on ? "#d63a30" : "#7a1313", x, y, 16, 10);
      line(c, "#fff", x + 5, y + 2, x + 10, y + 7); line(c, "#fff", x + 10, y + 2, x + 5, y + 7);
    } else {
      R(c, "#10141c", x, y, 16, 10);
    }
    R(c, "rgba(255,255,255,.14)", x, y, 5, 3);
  }
  /** 복합기에서 종이가 나오는 모습. p: 0~1 진행 */
  function copierPaper(c, p) {
    const f = FURNITURE.find((q) => q.id === "copier"), x = f.x * TILE + 8, y = f.y * TILE + 25;
    const h = 2 + Math.round(Math.min(1, p) * 7);
    R(c, "#9aa1ab", x - 1, y - 1, 14, 2);
    R(c, "#fbfbf8", x, y, 12, h); R(c, "#c4c8cf", x + 2, y + 2, 8, 1);
    if (h > 5) { R(c, "#c4c8cf", x + 2, y + 4, 6, 1); }
  }

  const draw = { window: window_, whiteboard, clock, bookshelf, tv, plant, desk, manager_desk, chair, meeting_table, sofa, glass_table,
    water_cooler, coffee_counter, copier, trash, coat_rack, door, mat };

  // 정적 레이어: 바닥·벽·가구 (캐릭터 뒤). env = { hour, minute }
  function renderStatic(c, env) {
    c.clearRect(0, 0, COLS * TILE, ROWS * TILE);
    floor(c); pantryFloor(c); carpet(c); walls(c);
    const items = [...FURNITURE].sort((a, b) => ((a.wall ? 0 : 1) - (b.wall ? 0 : 1)) || ((a.y + a.h) - (b.y + b.h)));
    for (const f of items) {
      const fn = draw[f.kind];
      if (fn) fn(c, f.x * TILE, f.y * TILE, f.w * TILE, f.h * TILE, f, env);
    }
  }
  // 오버레이 레이어: 캐릭터보다 앞에 그려지는 것(유리 칸막이)
  function renderOverlay(c) {
    c.clearRect(0, 0, COLS * TILE, ROWS * TILE);
    const d = FURNITURE.find((f) => f.id === "desk_manager"); // 매니저는 책상 '뒤'에 앉으므로 책상을 캐릭터 앞에 다시 그린다
    manager_desk(c, d.x * TILE, d.y * TILE, d.w * TILE, d.h * TILE);
    glassPartition(c);
  }

  return { P, R, px, disc, line, rng, draw, icon, character, monitorScreen, copierPaper, renderStatic, renderOverlay };
})();
