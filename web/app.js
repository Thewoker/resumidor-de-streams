const $ = (sel, el = document) => el.querySelector(sel);
const api = async (path, opts = {}) => {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

const fmt = (t) => {
  t = Math.max(0, Math.round(t));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
};
const parse = (txt) => txt.split(":").map(Number).reduce((a, b) => a * 60 + b, 0);
const STATUS = { done: "Listo", processing: "Procesando", queued: "En cola", error: "Error" };

let vods = [], current = null, detail = null, transcript = [], hls = null;
let filter = "all", markIn = null, markOut = null, lastMomentsJson = "", activeId = null;

// ---------- lista de VODs ----------
async function loadVods(refresh = false) {
  vods = await api(`/api/vods${refresh ? "?refresh=1" : ""}`);
  const box = $("#vods");
  box.innerHTML = "";
  for (const v of vods) {
    const st = v.status?.state;
    const el = document.createElement("div");
    el.className = "vod" + (v.key === current ? " on" : "");
    el.innerHTML = `<b></b><span class="muted"></span><br>
      <span class="badge ${st || ""}">${st ? STATUS[st] : "Sin procesar"}</span>
      ${v.moments ? `<span class="badge">${v.moments} momentos · ${v.approved} ✓</span>` : ""}`;
    $("b", el).textContent = v.title || v.key;
    $("span.muted", el).textContent = `${(v.created_at || "").slice(0, 10)} · ${fmt(v.duration || 0)}`;
    el.onclick = () => openVod(v.key);
    box.appendChild(el);
  }
  const q = await api("/api/queue");
  $("#queue").textContent = q.jobs || q.processing.length ? `Cola: ${q.processing.length} procesando/en cola · ${q.cuts} recortes` : "";
}

// ---------- vista de un VOD ----------
async function openVod(key) {
  current = key;
  lastMomentsJson = "";
  markIn = markOut = null;
  const main = $("#main");
  main.innerHTML = "";
  main.appendChild($("#vod-view").content.cloneNode(true));
  document.querySelectorAll(".vod").forEach((el, i) => el.classList.toggle("on", vods[i]?.key === key));

  detail = await api(`/api/vods/${key}`);
  transcript = detail.status?.state === "done" ? await api(`/api/vods/${key}/transcript`) : [];

  const video = $(".player");
  if (hls) hls.destroy();
  if (Hls.isSupported()) {
    hls = new Hls();
    hls.loadSource(detail.meta.source);
    hls.attachMedia(video);
  } else {
    video.src = detail.meta.source;
  }
  video.ontimeupdate = onTime;

  $(".process").onclick = async () => { await api(`/api/vods/${key}/process`, { method: "POST" }); refreshDetail(); loadVods(); };
  $(".mark-in").onclick = () => { markIn = video.currentTime; updateManual(); };
  $(".mark-out").onclick = () => { markOut = video.currentTime; updateManual(); };
  $(".create").onclick = async () => {
    await api(`/api/vods/${key}/moments`, { method: "POST", body: JSON.stringify({ start: markIn, end: markOut, title: "Clip manual" }) });
    markIn = markOut = null; updateManual(); refreshDetail();
  };
  document.querySelectorAll(".filters button").forEach((b) => b.onclick = () => {
    filter = b.dataset.f;
    document.querySelectorAll(".filters button").forEach((x) => x.classList.toggle("on", x === b));
    lastMomentsJson = ""; renderMoments();
  });
  const canvas = $(".timeline");
  canvas.onclick = (e) => {
    const dur = detail.meta.duration || detail.timeline?.duration || video.duration;
    video.currentTime = (e.offsetX / canvas.clientWidth) * dur;
    video.play();
  };
  window.onresize = drawTimeline;
  renderDetail();
}

async function refreshDetail() {
  if (!current) return;
  const key = current;
  const d = await api(`/api/vods/${key}`);
  if (key !== current) return;
  const wasDone = detail.status?.state === "done";
  detail = d;
  if (!wasDone && d.status?.state === "done") transcript = await api(`/api/vods/${key}/transcript`);
  renderDetail();
}

function renderDetail() {
  const { meta, status } = detail;
  $(".vod-head .title").textContent = meta.title || meta.key;
  const st = status?.state;
  $(".vod-head .info").textContent = [
    (meta.created_at || "").slice(0, 16), fmt(meta.duration || 0),
    st ? `${STATUS[st]}${status.step ? " · " + status.step : ""}${status.error ? " · " + status.error : ""}` : "Sin procesar",
  ].join(" · ");
  const btn = $(".process");
  btn.disabled = st === "processing" || st === "queued";
  btn.textContent = st === "done" || st === "error" ? "Volver a procesar" : "Procesar";
  drawTimeline();
  renderMoments();
}

// ---------- línea de tiempo ----------
function drawTimeline() {
  const canvas = $(".timeline");
  if (!canvas || !detail) return;
  const w = canvas.width = canvas.clientWidth * devicePixelRatio;
  const h = canvas.height = 70 * devicePixelRatio;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#0f1115"; ctx.fillRect(0, 0, w, h);
  const dur = detail.meta.duration || detail.timeline?.duration || 1;

  const tl = detail.timeline;
  if (tl) {
    ctx.fillStyle = "#3a4150";
    const bw = w / tl.values.length;
    tl.values.forEach((v, i) => {
      const bh = Math.min(Math.max(v, 0) / 5, 1) * h * 0.8;
      ctx.fillRect(i * bw, h - bh, Math.max(bw, 1), bh);
    });
  }
  for (const m of detail.moments) {
    if (m.status === "discarded") continue;
    const x0 = (m.start / dur) * w, x1 = (m.end / dur) * w;
    ctx.fillStyle = m.status === "approved" ? "rgba(83,252,24,.55)" : m.file ? "rgba(245,176,65,.5)" : "rgba(139,147,163,.3)";
    if (m.id === activeId) ctx.fillStyle = "rgba(255,255,255,.7)";
    ctx.fillRect(x0, 0, Math.max(x1 - x0, 2 * devicePixelRatio), h);
  }
  const video = $(".player");
  if (video && video.currentTime) {
    ctx.fillStyle = "#fff";
    ctx.fillRect((video.currentTime / dur) * w, 0, 2 * devicePixelRatio, h);
  }
}

let lastDraw = 0;
function onTime() {
  const t = $(".player").currentTime;
  const seg = transcript.find((s) => s.start <= t && s.end >= t);
  $(".now-text").textContent = seg ? `“${seg.text}”` : "";
  if (Date.now() - lastDraw > 500) { lastDraw = Date.now(); drawTimeline(); }
}

function updateManual() {
  $(".range").textContent = `${markIn != null ? fmt(markIn) : "—"} → ${markOut != null ? fmt(markOut) : "—"}`;
  $(".create").disabled = !(markIn != null && markOut != null && markOut - markIn >= 2);
}

// ---------- momentos ----------
function renderMoments() {
  const list = detail.moments.filter((m) =>
    filter === "all" ? true : filter === "clips" ? m.file : m.status === filter);
  const json = JSON.stringify(list);
  if (json === lastMomentsJson) return;  // no reiniciar vídeos que se estén reproduciendo
  lastMomentsJson = json;

  const box = $(".moments");
  box.innerHTML = list.length ? "" : `<p class="empty">${detail.status?.state === "done" ? "Nada en este filtro." : "Todavía no hay momentos."}</p>`;
  for (const m of list) box.appendChild(momentCard(m));
}

function momentCard(m) {
  const el = $("#moment-card").content.firstElementChild.cloneNode(true);
  const base = `/files/${current}/clips/`;
  el.classList.add(m.status);
  if (m.id === activeId) el.classList.add("active");

  const media = $(".media", el);
  if (m.cutting) {
    media.innerHTML = `<div class="placeholder">✂ Cortando…</div>`;
  } else if (m.file) {
    const v = document.createElement("video");
    v.controls = true; v.preload = "metadata"; v.src = base + m.file;
    media.appendChild(v);
    if (m.vertical) {
      const t = document.createElement("div");
      t.className = "toggle";
      t.innerHTML = `<button class="ghost">Horizontal</button><button class="ghost">Vertical</button>`;
      const [hb, vb] = t.querySelectorAll("button");
      hb.onclick = () => { v.src = base + m.file; };
      vb.onclick = () => { v.src = base + m.vertical; };
      media.appendChild(t);
    }
  } else {
    media.innerHTML = `<div class="placeholder">${m.cut_error ? "Error: " + m.cut_error : "Sin clip todavía.<br>Pulsá ✂ Cortar."}</div>`;
  }

  const score = $(".score", el);
  score.textContent = m.llm != null ? m.llm.toFixed(0) + "/10" : "🔊";
  score.title = `puntuación ${m.score?.toFixed(2)} · audio ${m.audio != null ? m.audio.toFixed(2) : "-"}`;
  const name = $(".name", el);
  name.value = m.title;
  name.onchange = () => patch(m, { title: name.value });
  $(".reason", el).textContent = m.reason || "";

  const start = $(".start", el), end = $(".end", el), dur = $(".dur", el);
  const sync = () => { start.value = fmt(m.start); end.value = fmt(m.end); dur.textContent = `${Math.round(m.end - m.start)} s`; };
  sync();
  const move = (field, delta) => { m[field] = Math.max(0, m[field] + delta); sync(); dirty(); };
  $(".s-", el).onclick = () => move("start", -2);
  $(".s\\+", el).onclick = () => move("start", 2);
  $(".e-", el).onclick = () => move("end", -2);
  $(".e\\+", el).onclick = () => move("end", 2);
  start.onchange = () => { m.start = parse(start.value); sync(); dirty(); };
  end.onchange = () => { m.end = parse(end.value); sync(); dirty(); };
  const recut = $(".recut", el);
  const dirty = () => { recut.textContent = "💾 Guardar y cortar"; recut.style.borderColor = "var(--warn)"; };
  recut.textContent = m.file ? "✂ Volver a cortar" : "✂ Cortar";
  recut.disabled = !!m.cutting;
  recut.onclick = async () => {
    await patch(m, { start: m.start, end: m.end, title: name.value }, false);
    await api(`/api/vods/${current}/moments/${m.id}/cut`, { method: "POST" });
    refreshDetail();
  };

  $(".text", el).textContent = transcript
    .filter((s) => s.end > m.start && s.start < m.end)
    .map((s) => `[${fmt(s.start)}] ${s.text}`).join("\n") || "—";

  $(".watch", el).onclick = () => {
    activeId = m.id;
    document.querySelectorAll(".moment").forEach((c) => c.classList.remove("active"));
    el.classList.add("active");
    const video = $(".player");
    video.currentTime = m.start;
    video.play();
    video.scrollIntoView({ behavior: "smooth", block: "center" });
    drawTimeline();
  };
  const approve = $(".approve", el), discard = $(".discard", el);
  approve.textContent = m.status === "approved" ? "↺ Quitar aprobado" : "✓ Aprobar";
  discard.textContent = m.status === "discarded" ? "↺ Recuperar" : "✕ Descartar";
  approve.onclick = () => patch(m, { status: m.status === "approved" ? "pending" : "approved" });
  discard.onclick = () => patch(m, { status: m.status === "discarded" ? "pending" : "discarded" });

  if (m.file && !m.cutting) {
    $(".downloads", el).innerHTML =
      `<a href="${base + m.file}" download>⬇ horizontal</a>` + (m.vertical ? `<a href="${base + m.vertical}" download>⬇ vertical</a>` : "");
  }
  return el;
}

async function patch(m, changes, refresh = true) {
  await api(`/api/vods/${current}/moments/${m.id}`, { method: "PATCH", body: JSON.stringify(changes) });
  if (refresh) await refreshDetail();
}

// ---------- arranque ----------
$("#refresh").onclick = () => loadVods(true);
loadVods();
setInterval(() => {
  loadVods();
  const st = detail?.status?.state;
  if (st === "processing" || st === "queued" || detail?.moments.some((m) => m.cutting)) refreshDetail();
}, 5000);
