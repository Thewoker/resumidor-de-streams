// Editor de clips: recorte con zoom, montaje por trozos con velocidad y subtítulos editables.
const ZOOMS = [20, 45, 90, 180, 600, 1800, 7200];

const trim = {
  key: null, m: null, dur: 0, values: [], step: 5, fine: null,
  win: [0, 60], zoom: 1, segs: [], active: 0, subs: [], subsEdited: false,
  hls: null, drag: null, stopAt: null,
};

const segStart = () => trim.segs[0].start;
const segEnd = () => trim.segs[trim.segs.length - 1].end;
const outDur = () => trim.segs.reduce((a, s) => a + (s.end - s.start) / s.speed, 0);

async function openTrim(key, m) {
  trim.key = key;
  trim.m = m;
  const cuts = m.edit?.cuts?.length ? m.edit.cuts : [{ start: m.start, end: m.end, speed: 1 }];
  trim.segs = cuts.map((c) => ({ start: +c.start, end: +c.end, speed: +c.speed || 1 }));
  trim.active = 0;
  const tl = detail?.timeline || {};
  trim.values = tl.values || [];
  trim.step = tl.step || 5;
  trim.dur = detail?.meta?.duration || tl.duration || segEnd() + 60;
  trim.fine = null;
  trim.zoom = Math.max(0, ZOOMS.findIndex((z) => z >= (segEnd() - segStart()) * 1.8));
  if (trim.zoom < 0) trim.zoom = ZOOMS.length - 1;
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
  video.onloadedmetadata = () => { video.currentTime = segStart(); };
  dlg.showModal();
  syncTrim();
  loadSubs();
}

// ---------- subtítulos ----------
async function loadSubs(force = false) {
  if (!force && trim.m.edit?.subs?.length) {
    trim.subs = trim.m.edit.subs.map((s) => ({ ...s }));
    trim.subsEdited = true;
  } else {
    try {
      trim.subs = await api(`/api/vods/${trim.key}/subs?start=${segStart()}&end=${segEnd()}`);
    } catch (e) {
      trim.subs = [];
    }
    trim.subsEdited = force;
  }
  renderSubs();
}

function renderSubs() {
  const box = $(".subs-list");
  box.innerHTML = "";
  $(".subs-count").textContent = trim.subs.length ? `(${trim.subs.length} líneas${trim.subsEdited ? ", editados" : ""})` : "(sin transcripción)";
  trim.subs.forEach((s, i) => {
    const row = document.createElement("div");
    row.className = "sub-row";
    row.innerHTML = `<button class="ghost go" title="Ir a este momento">${fmt(s.start)}</button>
      <input class="txt"><button class="ghost del" title="Borrar línea">✕</button>`;
    const input = $(".txt", row);
    input.value = s.text;
    input.oninput = () => { s.text = input.value; trim.subsEdited = true; };
    $(".go", row).onclick = () => { $(".trim-video").currentTime = s.start; $(".trim-video").play(); };
    $(".del", row).onclick = () => { trim.subs.splice(i, 1); trim.subsEdited = true; renderSubs(); };
    box.appendChild(row);
  });
}

// ---------- ventana visible y dibujo ----------
function centerWindow(focus = "middle") {
  const len = ZOOMS[trim.zoom];
  const s = trim.segs[trim.active] || trim.segs[0];
  const mid = focus === "start" ? s.start : focus === "end" ? s.end : (segStart() + segEnd()) / 2;
  let a = Math.max(0, mid - len / 2);
  const b = Math.min(trim.dur, a + len);
  trim.win = [Math.max(0, b - len), b];
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

  const inSeg = (t) => trim.segs.find((s) => t >= s.start && t <= s.end);
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
    ctx.fillStyle = inSeg(t) ? "#53fc1899" : "#3a4150";
    ctx.fillRect((i - first) * bw, h - bh, Math.max(bw - 1, 1), bh);
  }

  // Oscurecer lo que no entra en ningún trozo
  ctx.fillStyle = "#000000a8";
  let prev = trim.win[0];
  for (const s of trim.segs) {
    if (s.start > prev) ctx.fillRect(timeToX(prev, w), 0, timeToX(s.start, w) - timeToX(prev, w), h);
    prev = Math.max(prev, s.end);
  }
  if (prev < trim.win[1]) ctx.fillRect(timeToX(prev, w), 0, w - timeToX(prev, w), h);

  // Marcas de tiempo
  ctx.fillStyle = "#8b93a3";
  ctx.font = `${11 * devicePixelRatio}px system-ui`;
  const span = trim.win[1] - trim.win[0];
  const marks = span <= 45 ? 5 : span <= 180 ? 15 : span <= 600 ? 60 : span <= 1800 ? 300 : 900;
  for (let t = Math.ceil(trim.win[0] / marks) * marks; t < trim.win[1]; t += marks) {
    const x = timeToX(t, w);
    ctx.fillRect(x, h - 14 * devicePixelRatio, 1, 6 * devicePixelRatio);
    ctx.fillText(fmt(t), x + 3 * devicePixelRatio, h - 3 * devicePixelRatio);
  }

  // Trozos: bordes, número y velocidad
  trim.segs.forEach((s, i) => {
    const x0 = timeToX(s.start, w), x1 = timeToX(s.end, w);
    const activo = i === trim.active;
    ctx.fillStyle = activo ? "#53fc18" : "#53fc1870";
    ctx.fillRect(x0 - 2 * devicePixelRatio, 0, 4 * devicePixelRatio, h);
    ctx.fillRect(x1 - 2 * devicePixelRatio, 0, 4 * devicePixelRatio, h);
    if (activo) {
      for (const x of [x0, x1]) ctx.fillRect(x - 7 * devicePixelRatio, h / 2 - 16 * devicePixelRatio, 14 * devicePixelRatio, 32 * devicePixelRatio);
    }
    if (x1 - x0 > 30 * devicePixelRatio) {
      ctx.fillStyle = activo ? "#fff" : "#ffffff99";
      ctx.font = `bold ${11 * devicePixelRatio}px system-ui`;
      const label = `${i + 1}${s.speed !== 1 ? ` · ${String(s.speed).replace(".", ",")}x` : ""}`;
      ctx.fillText(label, x0 + 8 * devicePixelRatio, 16 * devicePixelRatio);
    }
  });

  const video = $(".trim-video");
  if (video && !isNaN(video.currentTime)) {
    const x = timeToX(video.currentTime, w);
    if (x >= 0 && x <= w) {
      ctx.fillStyle = "#fff";
      ctx.fillRect(x, 0, 2 * devicePixelRatio, h);
    }
  }
}

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
  } catch (e) { /* si falla, se sigue viendo el volumen de 5 en 5 segundos */ }
}

function syncTrim() {
  const s = trim.segs[trim.active];
  $(".t-start").value = fmt(s.start);
  $(".t-end").value = fmt(s.end);
  const bruto = segEnd() - segStart();
  $(".sel-info").textContent = `trozo ${trim.active + 1} de ${trim.segs.length}: ${(s.end - s.start).toFixed(1)} s`;
  $(".out-dur").textContent = `Clip final: ${outDur().toFixed(1)} s${outDur() < bruto - 0.5 ? ` (de ${bruto.toFixed(0)} s)` : ""}`;
  const span = ZOOMS[trim.zoom];
  $(".zoom-label").textContent = span >= 3600 ? "vista: todo el stream" : `vista: ${span >= 60 ? `${Math.round(span / 60)} min` : `${span} s`}`;
  $(".zoom-in").disabled = trim.zoom === 0;
  $(".zoom-out").disabled = trim.zoom === ZOOMS.length - 1;
  $(".drop").disabled = trim.segs.length < 2;
  document.querySelectorAll("#trimmer .sp").forEach((b) => b.classList.toggle("on", +b.dataset.s === s.speed));
  drawTrim();
  loadFine();
}

function setSeg(start, end, keepInView = true) {
  const s = trim.segs[trim.active];
  const prev = trim.segs[trim.active - 1], next = trim.segs[trim.active + 1];
  const min = 0.5;
  start = Math.max(prev ? prev.end : 0, Math.min(start, end - min));
  end = Math.min(next ? next.start : trim.dur, Math.max(end, start + min));
  const moved = start !== s.start ? "start" : "end";
  s.start = start;
  s.end = end;
  const edge = moved === "start" ? s.start : s.end;
  if (keepInView && (edge < trim.win[0] || edge > trim.win[1])) centerWindow(moved);
  syncTrim();
}

// ---------- interacción con la línea de tiempo ----------
function initTrimCanvas() {
  const canvas = $(".trim-canvas");
  const near = (x, t) => Math.abs(x - timeToX(t, canvas.clientWidth)) < 10;

  canvas.onpointerdown = (e) => {
    const x = e.offsetX, t = xToTime(x, canvas.clientWidth);
    const hit = trim.segs.findIndex((s) => near(x, s.start) || near(x, s.end) || (t > s.start && t < s.end));
    if (hit < 0) {
      $(".trim-video").currentTime = t;
      return;
    }
    trim.active = hit;
    const s = trim.segs[hit];
    if (near(x, s.start)) trim.drag = "start";
    else if (near(x, s.end)) trim.drag = "end";
    else trim.drag = { move: t, seg: { ...s } };
    canvas.setPointerCapture?.(e.pointerId);
    syncTrim();
  };
  canvas.onpointermove = (e) => {
    const t = xToTime(e.offsetX, canvas.clientWidth);
    if (!trim.drag) {
      const over = trim.segs.some((s) => near(e.offsetX, s.start) || near(e.offsetX, s.end));
      canvas.style.cursor = over ? "ew-resize" : "pointer";
      return;
    }
    const s = trim.segs[trim.active];
    if (trim.drag === "start") setSeg(t, s.end, false);
    else if (trim.drag === "end") setSeg(s.start, t, false);
    else {
      const d = t - trim.drag.move;
      setSeg(trim.drag.seg.start + d, trim.drag.seg.end + d, false);
    }
  };
  canvas.onpointerup = () => { trim.drag = null; };
  canvas.onwheel = (e) => {
    e.preventDefault();
    const before = xToTime(e.offsetX, canvas.clientWidth);
    trim.zoom = Math.max(0, Math.min(trim.zoom + (e.deltaY > 0 ? 1 : -1), ZOOMS.length - 1));
    const len = ZOOMS[trim.zoom];
    const a = Math.max(0, Math.min(before - len * (e.offsetX / canvas.clientWidth), trim.dur - len));
    trim.win = [a, Math.min(trim.dur, a + len)];
    syncTrim();
  };
}

// ---------- herramientas y botones ----------
function initTrimControls() {
  const dlg = $("#trimmer");
  const video = $(".trim-video", dlg);
  const nudge = (which, d) => () => {
    const s = trim.segs[trim.active];
    if (which === "start") setSeg(s.start + d, s.end);
    else setSeg(s.start, s.end + d);
  };
  $(".s2-", dlg).onclick = nudge("start", -2);
  $(".s05-", dlg).onclick = nudge("start", -0.5);
  $(".s05\\+", dlg).onclick = nudge("start", 0.5);
  $(".s2\\+", dlg).onclick = nudge("start", 2);
  $(".e2-", dlg).onclick = nudge("end", -2);
  $(".e05-", dlg).onclick = nudge("end", -0.5);
  $(".e05\\+", dlg).onclick = nudge("end", 0.5);
  $(".e2\\+", dlg).onclick = nudge("end", 2);
  $(".s-now", dlg).onclick = () => setSeg(video.currentTime, trim.segs[trim.active].end);
  $(".e-now", dlg).onclick = () => setSeg(trim.segs[trim.active].start, video.currentTime);
  $(".t-start", dlg).onchange = (e) => setSeg(parse(e.target.value), trim.segs[trim.active].end);
  $(".t-end", dlg).onchange = (e) => setSeg(trim.segs[trim.active].start, parse(e.target.value));
  $(".zoom-in", dlg).onclick = () => { trim.zoom = Math.max(0, trim.zoom - 1); centerWindow(); syncTrim(); };
  $(".zoom-out", dlg).onclick = () => { trim.zoom = Math.min(ZOOMS.length - 1, trim.zoom + 1); centerWindow(); syncTrim(); };
  $(".go-start", dlg).onclick = () => { centerWindow("start"); syncTrim(); };
  $(".go-end", dlg).onclick = () => { centerWindow("end"); syncTrim(); };

  // Herramienta de corte: parte el trozo en dos por donde va la reproducción
  $(".split", dlg).onclick = () => {
    const t = video.currentTime;
    const i = trim.segs.findIndex((s) => t > s.start + 0.5 && t < s.end - 0.5);
    if (i < 0) return alert("Poné la reproducción dentro de un trozo para dividirlo.");
    const s = trim.segs[i];
    trim.segs.splice(i, 1, { ...s, end: t }, { ...s, start: t });
    trim.active = i + 1;
    syncTrim();
  };
  $(".drop", dlg).onclick = () => {
    if (trim.segs.length < 2) return;
    trim.segs.splice(trim.active, 1);
    trim.active = Math.max(0, trim.active - 1);
    syncTrim();
  };
  // Herramienta de cámara rápida
  document.querySelectorAll("#trimmer .sp").forEach((b) => {
    b.onclick = () => { trim.segs[trim.active].speed = +b.dataset.s; syncTrim(); };
  });
  $(".reset-edit", dlg).onclick = () => {
    trim.segs = [{ start: segStart(), end: segEnd(), speed: 1 }];
    trim.active = 0;
    syncTrim();
  };

  $(".play-sel", dlg).onclick = () => {
    const s = trim.segs[trim.active];
    video.currentTime = s.start;
    video.playbackRate = s.speed;
    trim.stopAt = s.end;
    video.play();
  };
  $(".subs-add", dlg).onclick = () => {
    const t = video.currentTime;
    trim.subs.push({ start: t, end: t + 2, text: "" });
    trim.subs.sort((a, b) => a.start - b.start);
    trim.subsEdited = true;
    renderSubs();
  };
  $(".subs-reset", dlg).onclick = () => loadSubs(true);

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
    const montado = trim.segs.length > 1 || trim.segs[0].speed !== 1;
    const body = {
      start: segStart(), end: segEnd(),
      edit: {
        cuts: montado ? trim.segs.map((s) => ({ start: +s.start.toFixed(2), end: +s.end.toFixed(2), speed: s.speed })) : [],
        subs: trim.subsEdited ? trim.subs.filter((s) => (s.text || "").trim()) : [],
      },
    };
    await api(`/api/vods/${key}/moments/${m.id}`, { method: "PATCH", body: JSON.stringify(body) });
    await api(`/api/vods/${key}/moments/${m.id}/cut`, { method: "POST" });
    close();
    refreshDetail();
  };
  window.addEventListener("resize", () => $("#trimmer").open && drawTrim());
}

initTrimCanvas();
initTrimControls();
