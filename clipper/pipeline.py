import json
import threading
import time
from pathlib import Path

from . import audio, cut, llm, media, notify, scoring, transcribe

lock = threading.Lock()

STEPS = [
    ("download", "Descargando vídeo"),
    ("audio", "Extrayendo audio"),
    ("volume", "Analizando volumen"),
    ("transcribe", "Transcribiendo"),
    ("ai", "Buscando momentos con la IA"),
    ("cut", "Cortando clips"),
]
# Peso aproximado de cada paso en el tiempo total, para la barra general
WEIGHTS = {"download": 12, "audio": 4, "volume": 4, "transcribe": 40, "ai": 30, "cut": 10}


def fmt(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02}:{int(t % 60):02}"


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _plain(o):
    # Tipos de numpy (int64, float32...) que json no sabe guardar
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def write_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=_plain), encoding="utf-8")
    tmp.replace(path)


def set_status(workdir: Path, state, step="", error=None):
    write_json(workdir / "status.json", {"state": state, "step": step, "error": error, "updated": time.time()})


class Progress:
    """Estado legible del procesado que lee la interfaz (status.json)."""

    def __init__(self, workdir: Path):
        self.path = workdir / "status.json"
        self.started = time.time()
        self.steps = [{"key": k, "label": label, "state": "pending", "progress": 0, "detail": ""} for k, label in STEPS]
        self.current = None
        self._saved = 0

    def _step(self, key):
        return next(s for s in self.steps if s["key"] == key)

    def start(self, key, detail=""):
        self.done()
        self.current = self._step(key)
        self.current.update(state="running", progress=0, detail=detail, started=time.time())
        print(f"> {self.current['label']} {detail}")
        self.save(force=True)

    def update(self, progress=None, detail=None):
        if progress is not None:
            self.current["progress"] = max(0.0, min(float(progress), 1.0))
        if detail is not None:
            self.current["detail"] = detail
        self.save()

    def skip(self, key, detail="Ya estaba hecho, se reutiliza"):
        self.done()
        self._step(key).update(state="done", progress=1, detail=detail, started=time.time(), ended=time.time())
        self.save(force=True)

    def done(self, detail=None):
        if self.current and self.current["state"] == "running":
            self.current.update(state="done", progress=1, ended=time.time())
            if detail is not None:
                self.current["detail"] = detail
        self.current = None

    def overall(self):
        total = sum(WEIGHTS.values())
        return sum(WEIGHTS[s["key"]] * (1 if s["state"] == "done" else s["progress"]) for s in self.steps) / total

    def save(self, force=False, state="processing", error=None):
        if not force and time.time() - self._saved < 1:
            return
        self._saved = time.time()
        running = self.current["label"] if self.current else ""
        write_json(self.path, {
            "state": state, "step": running, "error": error, "updated": time.time(),
            "started": self.started, "progress": round(self.overall(), 3), "steps": self.steps,
        })

    def fail(self, error):
        if self.current:
            self.current.update(state="error", ended=time.time())
        self.save(force=True, state="error", error=error)

    def finish(self, detail):
        self.done()
        write_json(self.path, {
            "state": "done", "step": detail, "error": None, "updated": time.time(),
            "started": self.started, "progress": 1, "steps": self.steps,
        })


def cut_moment(workdir: Path, m, video, transcript, s):
    """Corta (o recorta de nuevo) un momento. `video` puede ser archivo local o la URL .m3u8."""
    clips = workdir / "clips"
    clips.mkdir(exist_ok=True)
    for old in (m.get("file"), m.get("vertical")):
        if old:
            (clips / old).unlink(missing_ok=True)
    name = f"{m['id']:03}_{media.slug(m['title'])}_{int(time.time()) % 10000}"
    m["file"] = f"{name}.mp4"
    cut.horizontal(video, m["start"], m["end"], clips / m["file"], s.cpu)
    if s.vertical:
        srt = clips / f"{name}.srt"
        cut.write_srt(transcript, m["start"], m["end"], srt)
        m["vertical"] = f"{name}_vertical.mp4"
        cut.vertical(video, m["start"], m["end"], clips / m["vertical"], srt.name, s.cpu)
    return m


def process(source, s, key, title="", meta=None):
    workdir = Path(s.out) / key
    workdir.mkdir(parents=True, exist_ok=True)
    meta = meta or {}
    write_json(workdir / "meta.json", {**meta, "key": key, "title": title, "source": source})
    p = Progress(workdir)
    expected = float(meta.get("duration") or 0)  # duración según Kick, para el % de descarga

    try:
        # 1. Descarga
        if [x for x in workdir.glob("video.*") if ".part" not in x.name]:
            p.skip("download", "Vídeo ya descargado, se reutiliza")
        else:
            p.start("download", "Conectando con Kick…")

            def on_download(seconds, megas, speed):
                if seconds is None:  # yt-dlp: solo megas y porcentaje
                    p.update(None, f"{megas:,.0f} MB descargados ({speed})")
                    return
                frac = seconds / expected if expected else None
                of = f" de {fmt(expected)}" if expected else ""
                speed_txt = f" · velocidad {speed}" if speed and speed != "N/A" else ""
                p.update(frac, f"{fmt(seconds)}{of} del stream · {megas:,.0f} MB{speed_txt}")

            media.get_video(source, workdir, on_download)
        video = media.get_video(source, workdir)
        total = media.duration(video)

        # 2. Audio
        if (workdir / "audio.wav").exists():
            p.skip("audio")
        else:
            p.start("audio", "Separando el audio del vídeo…")
            media.extract_audio(video, workdir, lambda sec: p.update(sec / total, f"{fmt(sec)} de {fmt(total)}"))
        wav = media.extract_audio(video, workdir)
        p.done("Audio separado")

        # 3. Volumen
        p.start("volume", "Midiendo el volumen de cada segundo…")
        db = audio.loudness(wav, workdir, lambda sec: p.update(0.9 * sec / total, f"{fmt(sec)} de {fmt(total)}"))
        p.update(0.95, "Buscando picos (gritos, risas, sustos)…")
        z = audio.excitement(db)
        peaks = audio.peaks(z)
        bucket = 5
        values = [round(float(z[i:i + bucket].max()), 2) for i in range(0, len(z), bucket)]
        write_json(workdir / "timeline.json", {"step": bucket, "duration": total, "values": values})
        p.done(f"{len(peaks)} picos de volumen encontrados")

        # 4. Transcripción
        if (workdir / "transcript.json").exists():
            p.skip("transcribe", "Transcripción ya hecha, se reutiliza")
        else:
            p.start("transcribe", "Liberando la gráfica…")
        llm.unload(s.ollama, s.model)  # que Ollama no ocupe la VRAM mientras corre Whisper
        transcript = transcribe.transcribe(
            wav, workdir, s.whisper, language=s.lang,
            on_loading=lambda: p.update(0, f"Cargando Whisper {s.whisper} en la gráfica (la primera vez se descarga, ~3 GB)…"),
            on_progress=lambda sec, text: p.update(sec / total, f"{fmt(sec)} de {fmt(total)} · “{text[:90]}”"),
        )
        p.done(f"{len(transcript)} frases transcritas")

        # 5. IA
        if (workdir / "llm_moments.json").exists():
            p.skip("ai", "Análisis ya hecho, se reutiliza")
        else:
            p.start("ai", f"Cargando {s.model}…")
        found = llm.find_moments(
            transcript, peaks, workdir, s.ollama, s.model,
            on_progress=lambda n, count, found: p.update(
                n / count if count else 0, f"Fragmento {n + 1} de {count} · {found} momentos candidatos"),
        )
        llm.unload(s.ollama, s.model)
        ranked = scoring.rank(found, peaks, z, total, top=200, min_llm=s.min_score)
        moments = [{**m, "id": i, "status": "pending", "file": None, "vertical": None}
                   for i, m in enumerate(ranked, 1)]
        write_json(workdir / "moments.json", moments)
        p.done(f"{len(found)} candidatos → {len(moments)} momentos buenos")

        # 6. Clips
        to_cut = moments[: s.top]
        p.start("cut", f"Preparando {len(to_cut)} clips…")
        for i, m in enumerate(to_cut):
            p.update(i / max(len(to_cut), 1), f"Clip {i + 1} de {len(to_cut)}: {m['title']}")
            cut_moment(workdir, m, video, transcript, s)
            with lock:
                write_json(workdir / "moments.json", moments)
        p.done(f"{len(to_cut)} clips cortados")

        # El VOD pesa varios GB: los recortes posteriores se hacen desde la URL de Kick
        if not s.keep_video and video.parent == workdir:
            video.unlink(missing_ok=True)
            wav.unlink(missing_ok=True)

        minutes = int((time.time() - p.started) / 60)
        p.finish(f"Listo en {minutes} min")
        lines = [f"{m['id']}. [{fmt(m['start'])}] {m['title']} ({m['score']:.2f})" for m in to_cut]
        notify.telegram(f"🎬 {title or key}\n{len(moments)} momentos, {len(lines)} clips\n\n" + "\n".join(lines))
        return moments
    except Exception as e:
        p.fail(str(e))
        notify.telegram(f"❌ Error procesando {title or key}: {e}")
        raise
