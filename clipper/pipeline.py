import json
import threading
import time
from pathlib import Path

import numpy as np

from . import audio, cut, llm, media, notify, scoring, transcribe

lock = threading.Lock()


def fmt(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02}:{int(t % 60):02}"


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def set_status(workdir: Path, state, step="", error=None):
    write_json(workdir / "status.json", {"state": state, "step": step, "error": error, "updated": time.time()})


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
    write_json(workdir / "meta.json", {**(meta or {}), "key": key, "title": title, "source": source})
    t0 = time.time()

    def step(n, msg):
        print(f"{n}/6 {msg}")
        set_status(workdir, "processing", f"{n}/6 {msg}")

    try:
        step(1, "Descargando vídeo")
        video = media.get_video(source, workdir)
        total = media.duration(video)

        step(2, "Extrayendo audio")
        wav = media.extract_audio(video, workdir)

        step(3, "Analizando volumen")
        z = audio.excitement(audio.loudness(wav, workdir))
        peaks = audio.peaks(z)
        bucket = 5
        values = [round(float(z[i:i + bucket].max()), 2) for i in range(0, len(z), bucket)]
        write_json(workdir / "timeline.json", {"step": bucket, "duration": total, "values": values})

        step(4, "Transcribiendo")
        llm.unload(s.ollama, s.model)  # que Ollama no ocupe la VRAM mientras corre Whisper
        transcript = transcribe.transcribe(wav, workdir, s.whisper, language=s.lang)

        step(5, "Buscando momentos con la IA")
        found = llm.find_moments(transcript, peaks, workdir, s.ollama, s.model)
        llm.unload(s.ollama, s.model)
        ranked = scoring.rank(found, peaks, z, total, top=200, min_llm=s.min_score)
        moments = [{**m, "id": i, "status": "pending", "file": None, "vertical": None}
                   for i, m in enumerate(ranked, 1)]

        step(6, f"Cortando {min(s.top, len(moments))} clips")
        for m in moments[: s.top]:
            cut_moment(workdir, m, video, transcript, s)
            write_json(workdir / "moments.json", moments)
        write_json(workdir / "moments.json", moments)

        # El VOD pesa varios GB: los recortes posteriores se hacen desde la URL de Kick
        if not s.keep_video and video.parent == workdir:
            video.unlink(missing_ok=True)
            wav.unlink(missing_ok=True)

        set_status(workdir, "done", f"Listo en {int((time.time() - t0) / 60)} min")
        lines = [f"{m['id']}. [{fmt(m['start'])}] {m['title']} ({m['score']:.2f})" for m in moments[: s.top]]
        notify.telegram(f"🎬 {title or key}\n{len(moments)} momentos, {len(lines)} clips\n\n" + "\n".join(lines))
        return moments
    except Exception as e:
        set_status(workdir, "error", error=str(e))
        notify.telegram(f"❌ Error procesando {title or key}: {e}")
        raise
