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
const dur = (sec) => {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec} s`;
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min ${sec % 60} s`;
};

// Traduce errores técnicos a algo accionable
function explainError(err) {
  if (!err) return "";
  if (/CUDA|cuda|NVIDIA|nvidia/.test(err)) return "La gráfica no está disponible dentro del contenedor. Revisá que \"--gpus all\" esté en Custom Docker Options de Coolify y hacé Redeploy.";
  if (/out of memory|OOM/i.test(err)) return "La gráfica se quedó sin memoria. Puede que Ollama u otro programa la esté usando.";
  if (/11434|Ollama|Connection refused/i.test(err)) return "No se pudo conectar con Ollama. Revisá OLLAMA_URL y que el servicio esté encendido.";
  if (/ffmpeg/i.test(err)) return "Falló ffmpeg al procesar el vídeo.";
  if (/403|404|kick/i.test(err)) return "No se pudo descargar el VOD de Kick (puede que se haya borrado o sea privado).";
  return "";
}

function renderProgress(status) {
  const box = $(".progress");
  if (!box) return;
  const st = status?.state;
  if (!st || st === "done" || (!status.steps && st !== "queued" && st !== "error")) { box.hidden = true; return; }
  box.hidden = false;

  const now = Date.now() / 1000;
  const overall = status.progress || 0;
  const elapsed = status.started ? (st === "error" ? status.updated : now) - status.started : 0;
  const title = st === "queued" ? "⏳ En cola, esperando a que termine otro stream"
    : st === "error" ? "❌ El procesado se detuvo"
    : `Procesando · ${Math.round(overall * 100)}%`;
  $(".progress-title").textContent = title;
  $(".progress-times").textContent = elapsed > 0 ? `Lleva ${dur(elapsed)}` : "";
  $(".bar.big > div").style.width = `${overall * 100}%`;
  $(".bar.big > div").style.background = st === "error" ? "var(--bad)" : "";

  const ol = $(".steps");
  ol.innerHTML = "";
  for (const s of status.steps || []) {
    const li = document.createElement("li");
    li.className = s.state;
    const icon = { done: "✓", running: '<span class="spin"></span>', error: "✕", pending: "○" }[s.state];
    let right = "";
    if (s.state === "running") {
      const took = now - (s.started || now);
      const pct = Math.round((s.progress || 0) * 100);
      // Estimación de lo que falta solo cuando ya hay avance suficiente para que sea fiable
      const eta = s.progress > 0.03 && took > 10 ? ` · faltan ~${dur(took / s.progress - took)}` : "";
      right = `${pct}%${eta}`;
    } else if (s.state === "done" && s.ended && s.started) {
      right = s.ended - s.started >= 1 ? dur(s.ended - s.started) : "";
    }
    li.innerHTML = `<span class="icon">${icon}</span><span class="label"></span><span class="pct">${right}</span>
      ${s.state === "running" ? `<div class="bar"><div style="width:${(s.progress || 0) * 100}%"></div></div>` : ""}
      <span class="detail"></span>`;
    $(".label", li).textContent = s.label;
    $(".detail", li).textContent = s.detail || "";
    ol.appendChild(li);
  }

  const hint = explainError(status.error);
  $(".progress-error").textContent = st === "error" ? [hint, `Detalle técnico: ${status.error}`].filter(Boolean).join("\n") : "";
}

let vods = [], current = null, detail = null, transcript = [], hls = null;
let filter = "all", markIn = null, markOut = null, lastMomentsJson = "", activeId = null, view = "streams";

// ---------- confirmación ----------
function confirmDialog({ title, message, requireText = false, okText = "Borrar" }) {
  const dlg = $("#confirm");
  $("h3", dlg).textContent = title;
  $(".msg", dlg).textContent = message;
  const label = $(".type", dlg), input = $("input", label), ok = $(".ok", dlg);
  label.classList.toggle("hidden", !requireText);
  input.value = "";
  ok.textContent = okText;
  ok.disabled = requireText;
  input.oninput = () => { ok.disabled = input.value.trim().toUpperCase() !== "BORRAR"; };
  dlg.showModal();
  if (requireText) input.focus();
  return new Promise((resolve) => {
    ok.onclick = () => { dlg.close(); resolve(true); };
    $(".cancel", dlg).onclick = () => { dlg.close(); resolve(false); };
    dlg.oncancel = () => resolve(false);
  });
}

// ---------- vista de aprobados ----------
async function showApproved() {
  view = "approved";
  $("#tab-streams").classList.remove("on");
  $("#tab-approved").classList.add("on");
  const main = $("#main");
  const items = await api("/api/approved");
  main.innerHTML = `<h2>⭐ Clips aprobados <span class="muted">(${items.length})</span></h2>`;
  if (!items.length) {
    main.innerHTML += `<p class="empty">Todavía no aprobaste ningún clip.<br>Aprobalos desde cada stream y aparecen acá.</p>`;
    return;
  }
  const grid = document.createElement("div");
  grid.className = "gallery";
  for (const m of items) {
    const base = `/files/${m.key}/clips/`;
    const el = document.createElement("article");
    el.innerHTML = `<video controls preload="metadata" src="${base + m.file}"></video>
      <h4></h4><p class="from muted"></p>
      <div class="acts">
        ${m.vertical ? `<button class="ghost v">Ver vertical</button><button class="ghost h">Ver horizontal</button>` : ""}
        <button class="ghost open">Ir al stream</button>
        <span class="downloads"><a href="${base + m.file}" download>⬇ horizontal</a>${
          m.vertical ? `<a href="${base + m.vertical}" download>⬇ vertical</a>` : ""}</span>
      </div>`;
    $("h4", el).textContent = m.title;
    $(".from", el).textContent = `${m.vod_title} · ${fmt(m.start)} · ${Math.round(m.end - m.start)} s`;
    const video = $("video", el);
    if (m.vertical) {
      $(".v", el).onclick = () => { video.src = base + m.vertical; };
      $(".h", el).onclick = () => { video.src = base + m.file; };
    }
    $(".open", el).onclick = () => { showStreams(); openVod(m.key); };
    grid.appendChild(el);
  }
  main.appendChild(grid);
}

function showStreams() {
  view = "streams";
  $("#tab-approved").classList.remove("on");
  $("#tab-streams").classList.add("on");
  $("#vods").classList.remove("hidden");
  if (current) openVod(current);
  else $("#main").innerHTML = `<p class="empty">Elegí un stream de la izquierda.</p>`;
}

// ---------- lista de VODs ----------
async function loadVods(refresh = false) {
  vods = await api(`/api/vods${refresh ? "?refresh=1" : ""}`);
  const box = $("#vods");
  box.innerHTML = "";
  for (const v of vods) {
    const st = v.status?.state;
    const el = document.createElement("div");
    el.className = "vod" + (v.key === current ? " on" : "");
    const pct = st === "processing" && v.status.progress != null ? Math.round(v.status.progress * 100) : null;
    el.innerHTML = `<b></b><span class="muted"></span><br>
      <span class="badge ${st || ""}">${st ? STATUS[st] : "Sin procesar"}${pct != null
        ? ` ${pct}% <span class="mini"><i style="width:${pct}%"></i></span>` : ""}</span>
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
  view = "streams";
  $("#tab-approved").classList.remove("on");
  $("#tab-streams").classList.add("on");
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

  $(".feed-vod").onclick = () => openFeed(key);
  $(".cleanup").onclick = async () => {
    const extra = detail.moments.filter((m) => m.file && m.status !== "approved").length;
    const keep = detail.moments.filter((m) => m.status === "approved").length;
    if (!extra) return alert("No hay clips para borrar: todos los que tienen archivo están aprobados.");
    const ok = await confirmDialog({
      title: "Borrar clips no aprobados",
      message: `Se borran los archivos de ${extra} clip(s) de este stream.\nSe conservan los ${keep} aprobado(s).\n\nLos momentos siguen en la lista y podés volver a cortarlos cuando quieras. Esto no se puede deshacer.`,
      requireText: true,
      okText: `Borrar ${extra} clips`,
    });
    if (!ok) return;
    const r = await api(`/api/vods/${key}/cleanup`, { method: "POST" });
    await refreshDetail();
    alert(`Borrados ${r.deleted} clips · ${r.freed_mb} MB liberados`);
  };
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
    st === "done" ? `Listo${status.step ? " · " + status.step : ""}` : st ? STATUS[st] : "Sin procesar",
  ].join(" · ");
  renderProgress(status);
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
  discard.onclick = async () => {
    if (m.status === "discarded") return patch(m, { status: "pending" });
    if (m.file) {
      const ok = await confirmDialog({
        title: "Descartar y borrar el clip",
        message: `“${m.title}”\n${fmt(m.start)} · ${Math.round(m.end - m.start)} s\n\nSe borran los archivos del clip (horizontal y vertical). El momento queda marcado como descartado y podés volver a cortarlo más adelante.`,
        okText: "Descartar y borrar",
      });
      if (!ok) return;
      await api(`/api/vods/${current}/moments/${m.id}/files`, { method: "DELETE" });
    }
    await patch(m, { status: "discarded" });
  };

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
$("#refresh").onclick = () => (view === "approved" ? showApproved() : loadVods(true));
$("#tab-approved").onclick = showApproved;
$("#tab-streams").onclick = showStreams;
loadVods();
let tick = 0;
setInterval(() => {
  tick++;
  if (view === "approved" || !$("#feed").hidden) return;
  const st = detail?.status?.state;
  const busy = st === "processing" || st === "queued" || detail?.moments.some((m) => m.cutting);
  if (busy) refreshDetail();          // cada 2 s mientras procesa
  if (tick % (busy ? 2 : 3) === 0) loadVods();
}, 2000);
