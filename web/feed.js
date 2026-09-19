// Modo scroll: revisar clips uno detrás de otro como shorts y aprobar/descartar.
const feed = {
  items: [], idx: 0, key: null, history: [], decided: {}, observer: null,
};

async function openFeed(key = null) {
  try {
    await startFeed(key);
  } catch (e) {
    console.error(e);
    $("#feed").hidden = true;
    document.body.style.overflow = "";
    alert(`No se pudo abrir el modo scroll: ${e.message}`);
  }
}

async function startFeed(key) {
  feed.key = key;
  feed.items = await api(`/api/clips?status=pending${key ? `&key=${encodeURIComponent(key)}` : ""}`);
  feed.idx = 0;
  feed.history = [];
  feed.decided = {};
  const box = $("#feed");
  box.hidden = false;
  document.body.style.overflow = "hidden";
  $(".feed-undo").disabled = true;
  renderFeed();
}

function closeFeed() {
  const box = $("#feed");
  box.querySelectorAll("video").forEach((v) => { v.pause(); v.removeAttribute("src"); v.load(); });
  box.hidden = true;
  document.body.style.overflow = "";
  feed.observer?.disconnect();
  if (view === "approved") showApproved();
  else if (current) refreshDetail();
  loadVods();
}

function renderFeed() {
  const scroll = $(".feed-scroll");
  scroll.innerHTML = "";
  feed.observer?.disconnect();

  if (!feed.items.length) {
    scroll.innerHTML = `<section class="slide end"><div class="end-box">
      <h2>No hay clips pendientes 🎉</h2>
      <p class="muted">No queda ningún clip sin revisar${feed.key ? " en este stream" : ""}.<br>Cuando se procese un stream, sus clips nuevos aparecen acá.</p>
      <button class="feed-exit">Volver</button></div></section>`;
    $(".feed-exit", scroll).onclick = closeFeed;
    updateCount();
    return;
  }

  feed.items.forEach((m, i) => scroll.appendChild(slide(m, i)));
  const end = document.createElement("section");
  end.className = "slide end";
  end.dataset.i = feed.items.length;
  scroll.appendChild(end);

  feed.observer = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) activate(Number(e.target.dataset.i));
  }, { root: scroll, threshold: 0.6 });
  scroll.querySelectorAll(".slide").forEach((s) => feed.observer.observe(s));
  scroll.scrollTop = 0;
  activate(0);
}

function slide(m, i) {
  const base = `/files/${m.key}/clips/`;
  const el = document.createElement("section");
  el.className = "slide";
  el.dataset.i = i;
  el.innerHTML = `
    <div class="frame ${m.vertical ? "vertical" : "horizontal"}">
      <video playsinline loop preload="none"></video>
      <div class="paused-icon">▶</div>
      <div class="stamp"></div>
      <div class="info">
        <div class="tags"><span class="tag score"></span><span class="tag time"></span></div>
        <b class="title"></b>
        <span class="from"></span>
        <p class="reason"></p>
      </div>
    </div>
    <div class="side">
      <button class="like" title="Aprobar (→)"><span>👍</span><small>Aprobar</small></button>
      <button class="dislike" title="Descartar (←)"><span>👎</span><small>Descartar</small></button>
      ${m.vertical ? `<button class="flip" title="Cambiar formato"><span>⇆</span><small>Horizontal</small></button>` : ""}
      <button class="skip" title="Saltar (↓)"><span>⏭</span><small>Saltar</small></button>
    </div>`;
  const video = $("video", el);
  video.dataset.src = base + (m.vertical || m.file);
  $(".score", el).textContent = m.llm != null ? `IA ${m.llm.toFixed(0)}/10` : "🔊 pico de audio";
  $(".time", el).textContent = `${fmt(m.start)} · ${Math.round(m.end - m.start)} s`;
  $(".title", el).textContent = m.title;
  $(".from", el).textContent = m.vod_title;
  $(".reason", el).textContent = m.reason || "";

  video.onclick = () => togglePlay(el);
  $(".paused-icon", el).onclick = () => togglePlay(el);
  $(".like", el).onclick = () => decide(i, "approved");
  $(".dislike", el).onclick = () => decide(i, "discarded");
  $(".skip", el).onclick = () => goTo(i + 1);
  const flip = $(".flip", el);
  if (flip) {
    flip.onclick = () => {
      const frame = $(".frame", el);
      const toHorizontal = frame.classList.contains("vertical");
      frame.classList.toggle("vertical", !toHorizontal);
      frame.classList.toggle("horizontal", toHorizontal);
      $("small", flip).textContent = toHorizontal ? "Vertical" : "Horizontal";
      const t = video.currentTime;
      video.src = video.dataset.src = base + (toHorizontal ? m.file : m.vertical);
      video.currentTime = t;
      video.play().catch(() => {});
    };
  }
  return el;
}

function activate(i) {
  feed.idx = i;
  document.querySelectorAll("#feed .slide").forEach((s) => {
    const j = Number(s.dataset.i);
    const v = $("video", s);
    if (!v) return;
    if (Math.abs(j - i) <= 1) {
      if (!v.getAttribute("src")) { v.src = v.dataset.src; v.preload = "auto"; }
    } else if (v.getAttribute("src")) {
      // Liberar los vídeos lejanos para no cargar 15 clips a la vez
      v.pause(); v.removeAttribute("src"); v.load();
    }
    if (j === i) {
      v.currentTime = 0;
      v.play().then(() => s.classList.remove("paused")).catch(() => s.classList.add("paused"));
    } else {
      v.pause();
    }
  });
  if (i >= feed.items.length) renderEnd();
  updateCount();
}

function togglePlay(el) {
  const v = $("video", el);
  if (v.paused) { v.play(); el.classList.remove("paused"); }
  else { v.pause(); el.classList.add("paused"); }
}

function goTo(i) {
  i = Math.max(0, Math.min(i, feed.items.length));
  const target = document.querySelector(`#feed .slide[data-i="${i}"]`);
  target?.scrollIntoView({ behavior: "smooth" });
}

async function decide(i, status) {
  const m = feed.items[i];
  if (!m) return;
  const el = document.querySelector(`#feed .slide[data-i="${i}"]`);
  const prev = feed.decided[i] || "pending";
  feed.decided[i] = status;
  feed.history.push({ i, prev });
  $(".feed-undo").disabled = false;

  const stamp = $(".stamp", el);
  stamp.textContent = status === "approved" ? "👍 APROBADO" : "👎 DESCARTADO";
  stamp.className = `stamp show ${status}`;
  el.classList.remove("approved", "discarded");
  el.classList.add(status);
  updateCount();

  try {
    await api(`/api/vods/${m.key}/moments/${m.id}`, { method: "PATCH", body: JSON.stringify({ status }) });
  } catch (e) {
    stamp.textContent = "⚠ No se pudo guardar";
    return;
  }
  setTimeout(() => { stamp.className = "stamp"; goTo(i + 1); }, 550);
}

async function undo() {
  const last = feed.history.pop();
  $(".feed-undo").disabled = !feed.history.length;
  if (!last) return;
  const m = feed.items[last.i];
  if (last.prev === "pending") delete feed.decided[last.i];
  else feed.decided[last.i] = last.prev;
  await api(`/api/vods/${m.key}/moments/${m.id}`, { method: "PATCH", body: JSON.stringify({ status: last.prev }) });
  const el = document.querySelector(`#feed .slide[data-i="${last.i}"]`);
  el.classList.remove("approved", "discarded");
  if (last.prev !== "pending") el.classList.add(last.prev);
  goTo(last.i);
  updateCount();
}

function counts() {
  const values = Object.values(feed.decided);
  return {
    approved: values.filter((s) => s === "approved").length,
    discarded: values.filter((s) => s === "discarded").length,
  };
}

function updateCount() {
  const { approved, discarded } = counts();
  const n = feed.items.length;
  const pos = Math.min(feed.idx + 1, n);
  $(".feed-count").textContent = n
    ? `${pos} / ${n} · 👍 ${approved} · 👎 ${discarded}`
    : "";
}

function renderEnd() {
  const end = document.querySelector("#feed .slide.end");
  if (!end) return;
  const { approved, discarded } = counts();
  const skipped = feed.items.length - approved - discarded;
  end.innerHTML = `<div class="end-box">
    <h2>¡Terminaste! 🎬</h2>
    <p class="big"><span>👍 ${approved} aprobados</span><span>👎 ${discarded} descartados</span>${
      skipped ? `<span>⏭ ${skipped} sin decidir</span>` : ""}</p>
    ${discarded ? `<p class="muted">Los descartados siguen ocupando espacio hasta que los borres.</p>
      <button class="danger del">🗑 Borrar los ${discarded} descartados</button>` : ""}
    <div class="end-actions">
      ${approved ? `<button class="ghost see">⭐ Ver aprobados</button>` : ""}
      <button class="ghost again">↑ Volver al principio</button>
      <button class="exit">Salir</button>
    </div>
    <p class="done-msg muted"></p>
  </div>`;
  $(".exit", end).onclick = closeFeed;
  $(".again", end).onclick = () => goTo(0);
  const see = $(".see", end);
  if (see) see.onclick = () => { closeFeed(); showApproved(); };
  const del = $(".del", end);
  if (del) {
    del.onclick = async () => {
      const ok = await confirmDialog({
        title: "Borrar clips descartados",
        message: `Se borran los archivos de los ${discarded} clip(s) que descartaste en esta revisión (horizontal, vertical y subtítulos).\n\nLos aprobados y los que no decidiste no se tocan. Esto no se puede deshacer.`,
        okText: `Borrar ${discarded} clips`,
      });
      if (!ok) return;
      const keys = [...new Set(feed.items.map((m) => m.key))];
      const r = await api("/api/cleanup-discarded", { method: "POST", body: JSON.stringify({ keys }) });
      del.remove();
      $(".done-msg", end).textContent = `Borrados ${r.deleted} clips · ${r.freed_mb} MB liberados`;
      feed.history = [];  // ya no se puede deshacer lo borrado
      $(".feed-undo").disabled = true;
    };
  }
}

// ---------- controles ----------
$("#open-feed").onclick = () => openFeed(null);
$(".feed-close").onclick = closeFeed;
$(".feed-undo").onclick = undo;
document.addEventListener("keydown", (e) => {
  if ($("#feed").hidden || $("#confirm").open || e.target.tagName === "INPUT") return;
  const k = e.key;
  if (k === "Escape") closeFeed();
  else if (k === "ArrowDown" || k === "j") { e.preventDefault(); goTo(feed.idx + 1); }
  else if (k === "ArrowUp" || k === "k") { e.preventDefault(); goTo(feed.idx - 1); }
  else if (k === "ArrowRight" || k === "l") decide(feed.idx, "approved");
  else if (k === "ArrowLeft" || k === "h") decide(feed.idx, "discarded");
  else if (k === " ") {
    e.preventDefault();
    const s = document.querySelector(`#feed .slide[data-i="${feed.idx}"]`);
    if (s && $("video", s)) togglePlay(s);
  } else if (k === "z" || k === "Z") undo();
});
