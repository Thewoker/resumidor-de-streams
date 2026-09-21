// Editor de corte: línea de tiempo con zoom y bordes arrastrables, como el editor de clips de Twitch/Kick.
const ZOOMS = [20, 45, 90, 180, 600, 1800, 7200];

const trim = {
  key: null, m: null, dur: 0, values: [], step: 5,
  win: [0, 60], zoom: 1, sel: [0, 0], hls: null, drag: null, stopAt: null,
};

function openTrim(key, m) {
  trim.key = key;
  trim.m = m;
  trim.sel = [m.start, m.end];
  const tl = detail?.timeline || {};
  trim.values = tl.values || [];
  trim.step = tl.step || 5;
  trim.dur = detail?.meta?.duration || tl.duration || m.end + 60;
  // Zoom inicial: el que deja ver el clip entero con algo de margen
  trim.zoom = Math.max(0, ZOOMS.findIndex((z) => z >= (m.end - m.start) * 1.8));
  if (trim.zoom < 0) trim.zoom = ZOOMS.length - 1;
  trim.fine = null;
  centerWindow();

  const dlg = $("#trimmer");
  $(".trim-title", dlg).textContent = m.title;
  const video = $(".trim-video", dlg);
  if (trim.hls) trim.hls.destroy();
  if (window.Hls && Hls.isSupported()) {
    trim.hls = new Hls();
    trim.hls.loadSource(detail.meta.source);
    trim.hls.attachMedia(video);
  } else {
    video.src = detail.meta.source;
  }
  video.ontimeupdate = () => {
    if (trim.stopAt != null && video.currentTime >= trim.stopAt) { video.pause(); trim.stopAt = null; }
    drawTrim();
  };
  video.onloadedmetadata = () => { video.currentTime = trim.sel[0]; };
  dlg.showModal();
  syncTrim();
  requestAnimationFrame(drawTrim);
}

function centerWindow(focus = "middle") {
  const len = ZOOMS[trim.zoom];
  const mid = focus === "start" ? trim.sel[0] : focus === "end" ? trim.sel[1] : (trim.sel[0] + trim.sel[1]) / 2;
  let a = Math.max(0, mid - len / 2);
  let b = Math.min(trim.dur, a + len);
  a = Math.max(0, b - len);
  trim.win = [a, b];
}

const timeToX = (t, w) => ((t - trim.win[0]) / (trim.win[1] - trim.win[0])) * w;
const xToTime = (x, w) => trim.win[0] + (x / w) * (trim.win[1] - trim.win[0]);

function drawTrim() {
  const canvas = $(".trim-canvas");
  if (!canvas || !$("#trimmer").open) return;
  const w = canvas.width = canvas.clientWidth * devicePixelRatio;
  const h = canvas.height = 110 * devicePixelRatio;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#0f1115";
  ctx.fillRect(0, 0, w, h);

  // Volumen del stream en la ventana visible (segundo a segundo si hay detalle cargado)
  const fine = trim.fine && trim.win[0] >= trim.fine.from && trim.win[1] <= trim.fine.to;
  const values = fine ? trim.fine.values : trim.values;
  const step = fine ? 1 : trim.step;
  const base = fine ? trim.fine.from : 0;
  const first = Math.floor((trim.win[0] - base) / step), last = Math.ceil((trim.win[1] - base) / step);
  const bw = w / Math.max(last - first, 1);
  for (let i = first; i < last; i++) {
    const v = values[i];
    if (v == null) continue;
    const t = base + i * step;
    const bh = Math.min(Math.max(v, 0) / 5, 1) * (h - 22 * devicePixelRatio);
    const inside = t >= trim.sel[0] && t <= trim.sel[1];
    ctx.fillStyle = inside ? "#53fc1899" : "#3a4150";
    ctx.fillRect((i - first) * bw, h - bh, Math.max(bw - 1, 1), bh);
  }

  // Zona fuera de la selección
  const x0 = timeToX(trim.sel[0], w), x1 = timeToX(trim.sel[1], w);
  ctx.fillStyle = "#000000a0";
  ctx.fillRect(0, 0, Math.max(x0, 0), h);
  ctx.fillRect(x1, 0, w - x1, h);

  // Marcas de tiempo
  ctx.fillStyle = "#8b93a3";
  ctx.font = `${11 * devicePixelRatio}px system-ui`;
  const span = trim.win[1] - trim.win[0];
  const stepMarks = span <= 45 ? 5 : span <= 180 ? 15 : span <= 600 ? 60 : span <= 1800 ? 300 : 900;
  for (let t = Math.ceil(trim.win[0] / stepMarks) * stepMarks; t < trim.win[1]; t += stepMarks) {
    const x = timeToX(t, w);
    ctx.fillRect(x, h - 14 * devicePixelRatio, 1, 6 * devicePixelRatio);
    ctx.fillText(fmt(t), x + 3 * devicePixelRatio, h - 3 * devicePixelRatio);
  }

  // Bordes de la selección
  for (const [x, label] of [[x0, "inicio"], [x1, "fin"]]) {
    ctx.fillStyle = "#53fc18";
    ctx.fillRect(x - 2 * devicePixelRatio, 0, 4 * devicePixelRatio, h);
    ctx.fillRect(x - 7 * devicePixelRatio, h / 2 - 16 * devicePixelRatio, 14 * devicePixelRatio, 32 * devicePixelRatio);
    ctx.fillStyle = "#0b1a04";
    ctx.font = `bold ${10 * devicePixelRatio}px system-ui`;
    ctx.fillText(label === "inicio" ? "▌" : "▐", x - 3 * devicePixelRatio, h / 2 + 4 * devicePixelRatio);
  }

  // Reproducción
  const video = $(".trim-video");
  if (video && !isNaN(video.currentTime)) {
    const x = timeToX(video.currentTime, w);
    if (x >= 0 && x <= w) {
      ctx.fillStyle = "#fff";
      ctx.fillRect(x, 0, 2 * devicePixelRatio, h);
    }
  }
}

// Con mucho zoom se pide el volumen segundo a segundo del tramo visible
async function loadFine() {
  const span = trim.win[1] - trim.win[0];
  if (span > 240) return;
  const from = Math.max(0, trim.win[0] - span), to = Math.min(trim.dur, trim.win[1] + span);
  if (trim.fine && from >= trim.fine.from && to <= trim.fine.to) return;
  try {
    const r = await api(`/api/vods/${trim.key}/loudness?start=${Math.floor(from)}&end=${Math.ceil(to)}`);
    if (!r.values.length) return;
    trim.fine = { from: r.start, to: r.start + r.values.length, values: r.values };
    drawTrim();
  } catch (e) { /* si falla, se sigue viendo la versión de 5 en 5 segundos */ }
}

function syncTrim() {
  $(".t-start").value = fmt(trim.sel[0]);
  $(".t-end").value = fmt(trim.sel[1]);
  $(".sel-info").textContent = `${(trim.sel[1] - trim.sel[0]).toFixed(1)} s de clip`;
  const span = ZOOMS[trim.zoom];
  $(".zoom-label").textContent = span >= 3600 ? "vista: todo el stream" : `vista: ${span >= 60 ? `${Math.round(span / 60)} min` : `${span} s`}`;
  $(".zoom-in").disabled = trim.zoom === 0;
  $(".zoom-out").disabled = trim.zoom === ZOOMS.length - 1;
  drawTrim();
  loadFine();
}

function setSel(start, end, keepInView = true) {
  const min = 1;
  const moved = start !== trim.sel[0] ? "start" : "end";
  trim.sel = [Math.max(0, Math.min(start, trim.dur - min)), Math.min(trim.dur, Math.max(end, start + min))];
  const edge = moved === "start" ? trim.sel[0] : trim.sel[1];
  if (keepInView && (edge < trim.win[0] || edge > trim.win[1])) centerWindow(moved);
  syncTrim();
}

// ---------- interacción con la línea de tiempo ----------
function initTrimCanvas() {
  const canvas = $(".trim-canvas");
  const near = (x, t) => Math.abs(x - timeToX(t, canvas.clientWidth)) < 10;

  canvas.onpointerdown = (e) => {
    const x = e.offsetX;
    if (near(x, trim.sel[0])) trim.drag = "start";
    else if (near(x, trim.sel[1])) trim.drag = "end";
    else if (x > timeToX(trim.sel[0], canvas.clientWidth) && x < timeToX(trim.sel[1], canvas.clientWidth)) {
      trim.drag = { move: xToTime(x, canvas.clientWidth), sel: [...trim.sel] };
    } else {
      $(".trim-video").currentTime = xToTime(x, canvas.clientWidth);
      return;
    }
    canvas.setPointerCapture(e.pointerId);
  };
  canvas.onpointermove = (e) => {
    const t = xToTime(e.offsetX, canvas.clientWidth);
    if (!trim.drag) {
      canvas.style.cursor = near(e.offsetX, trim.sel[0]) || near(e.offsetX, trim.sel[1]) ? "ew-resize" : "pointer";
      return;
    }
    if (trim.drag === "start") setSel(Math.min(t, trim.sel[1] - 1), trim.sel[1], false);
    else if (trim.drag === "end") setSel(trim.sel[0], Math.max(t, trim.sel[0] + 1), false);
    else {
      const d = t - trim.drag.move;
      setSel(trim.drag.sel[0] + d, trim.drag.sel[1] + d, false);
    }
  };
  canvas.onpointerup = () => { trim.drag = null; };
  canvas.onwheel = (e) => {
    e.preventDefault();
    const before = xToTime(e.offsetX, canvas.clientWidth);
    trim.zoom = Math.max(0, Math.min(trim.zoom + (e.deltaY > 0 ? 1 : -1), ZOOMS.length - 1));
    const len = ZOOMS[trim.zoom];
    const frac = e.offsetX / canvas.clientWidth;
    let a = Math.max(0, before - len * frac);
    trim.win = [Math.max(0, Math.min(a, trim.dur - len)), 0];
    trim.win[1] = Math.min(trim.dur, trim.win[0] + len);
    syncTrim();
  };
}

// ---------- botones ----------
function initTrimControls() {
  const dlg = $("#trimmer");
  const video = $(".trim-video", dlg);
  const nudge = (which, d) => () => {
    if (which === "start") setSel(trim.sel[0] + d, trim.sel[1]);
    else setSel(trim.sel[0], trim.sel[1] + d);
  };
  $(".s2-", dlg).onclick = nudge("start", -2);
  $(".s05-", dlg).onclick = nudge("start", -0.5);
  $(".s05\\+", dlg).onclick = nudge("start", 0.5);
  $(".s2\\+", dlg).onclick = nudge("start", 2);
  $(".e2-", dlg).onclick = nudge("end", -2);
  $(".e05-", dlg).onclick = nudge("end", -0.5);
  $(".e05\\+", dlg).onclick = nudge("end", 0.5);
  $(".e2\\+", dlg).onclick = nudge("end", 2);
  $(".s-now", dlg).onclick = () => setSel(video.currentTime, trim.sel[1]);
  $(".e-now", dlg).onclick = () => setSel(trim.sel[0], video.currentTime);
  $(".t-start", dlg).onchange = (e) => setSel(parse(e.target.value), trim.sel[1]);
  $(".t-end", dlg).onchange = (e) => setSel(trim.sel[0], parse(e.target.value));
  $(".zoom-in", dlg).onclick = () => { trim.zoom = Math.max(0, trim.zoom - 1); centerWindow(); syncTrim(); };
  $(".zoom-out", dlg).onclick = () => { trim.zoom = Math.min(ZOOMS.length - 1, trim.zoom + 1); centerWindow(); syncTrim(); };
  $(".go-start", dlg).onclick = () => { centerWindow("start"); syncTrim(); };
  $(".go-end", dlg).onclick = () => { centerWindow("end"); syncTrim(); };
  $(".play-sel", dlg).onclick = () => {
    video.currentTime = trim.sel[0];
    trim.stopAt = trim.sel[1];
    video.play();
  };
  const close = () => {
    video.pause();
    trim.hls?.destroy();
    trim.hls = null;
    dlg.close();
  };
  $(".trim-close", dlg).onclick = close;
  $(".cancel", dlg).onclick = close;
  $(".save", dlg).onclick = async () => {
    const { key, m } = trim;
    await api(`/api/vods/${key}/moments/${m.id}`, {
      method: "PATCH", body: JSON.stringify({ start: trim.sel[0], end: trim.sel[1] }),
    });
    await api(`/api/vods/${key}/moments/${m.id}/cut`, { method: "POST" });
    close();
    refreshDetail();
  };
  window.addEventListener("resize", () => $("#trimmer").open && drawTrim());
}

initTrimCanvas();
initTrimControls();
