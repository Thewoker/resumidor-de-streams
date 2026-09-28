import subprocess
from pathlib import Path

from . import edit


def _encoder(cpu):
    if cpu:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-b:v", "0"]


def _ts(t):
    h, rem = divmod(max(t, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02}:{int(m):02}:{int(s):02},{int((s % 1) * 1000):03}"


def subtitle_entries(transcript, start, end, words_per_line=4):
    """Líneas de subtítulo (en tiempos del stream) a partir de las palabras de Whisper."""
    words = [
        w for s in transcript if s["end"] > start and s["start"] < end
        for w in s["words"] if start <= w["start"] < end
    ]
    # Agrupar por palabras, cortando también en silencios o cuando la línea dura demasiado
    groups, chunk = [], []
    for w in words:
        if chunk and (len(chunk) >= words_per_line
                      or w["start"] - chunk[-1]["end"] > 0.7
                      or w["end"] - chunk[0]["start"] > 3.5):
            groups.append(chunk)
            chunk = []
        chunk.append(w)
    if chunk:
        groups.append(chunk)
    return [{"start": g[0]["start"], "end": min(g[-1]["end"], g[0]["start"] + 5),
             "text": " ".join(w["word"] for w in g)} for g in groups]


def write_srt(entries, segs, path: Path):
    """Escribe el .srt en tiempos del clip montado, saltando lo que caiga en trozos eliminados."""
    lines = []
    for e in entries:
        text = (e.get("text") or "").strip()
        if not text:
            continue
        clamped = edit.clamp_to_segments(segs, float(e["start"]), float(e["end"]))
        if not clamped:
            continue
        a, b, _ = clamped
        out_a, out_b = edit.to_out_time(segs, a), edit.to_out_time(segs, b)
        if out_a is None or out_b is None or out_b - out_a < 0.2:
            continue
        lines.append(f"{len(lines) + 1}\n{_ts(out_a)} --> {_ts(out_b)}\n{text.upper()}\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def _input(video):
    p = Path(str(video))
    return str(p.resolve()) if p.exists() else str(video)


def _atempo(speed):
    """atempo solo admite 0.5-2, así que las velocidades altas se encadenan."""
    filters, left = [], speed
    while left > 2:
        filters.append("atempo=2.0")
        left /= 2
    while left < 0.5:
        filters.append("atempo=0.5")
        left *= 2
    filters.append(f"atempo={left:.4f}")
    return ",".join(filters)


def horizontal(video, start, end, out: Path, cpu=False, speed=1.0):
    # -ss y -t antes de -i: así limitan lo que se lee del stream, no lo que dura el resultado
    # (con cámara rápida, un trozo de 20 s a 2x tiene que dar 10 s de clip)
    args = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", _input(video)]
    if abs(speed - 1) > 0.01:
        args += ["-filter_complex", f"[0:v]setpts=PTS/{speed:.4f}[v];[0:a]{_atempo(speed)}[a]", "-map", "[v]", "-map", "[a]"]
    args += [*_encoder(cpu), "-c:a", "aac", "-b:a", "160k", "-r", "60", str(out)]
    subprocess.run(args, check=True)


def montage(video, segs, out: Path, cpu=False):
    """Monta el clip a partir de varios trozos, cada uno con su velocidad."""
    if len(segs) == 1:
        horizontal(video, segs[0]["start"], segs[0]["end"], out, cpu, segs[0]["speed"])
        return out

    tmp = out.parent / f".tmp_{out.stem}"
    tmp.mkdir(exist_ok=True)
    try:
        parts = []
        for i, s in enumerate(segs):
            part = tmp / f"{i:03}.mp4"
            horizontal(video, s["start"], s["end"], part, cpu, s["speed"])
            parts.append(part)
        listing = tmp / "list.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listing.name,
             "-c", "copy", str(out.resolve())],
            check=True, cwd=tmp,
        )
    finally:
        for p in tmp.glob("*"):
            p.unlink(missing_ok=True)
        tmp.rmdir()
    return out


def vertical(video, start, end, out: Path, srt_name: str, cpu=False):
    """9:16 con el juego centrado sobre fondo desenfocado y subtítulos quemados."""
    style = "Fontname=Arial,Fontsize=13,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=70"
    graph = (
        "[0:v]split[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];"
        "[b]scale=1080:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,subtitles={srt_name}:force_style='{style}'[v]"
    )
    # cwd = carpeta del clip para pasar el .srt sin rutas de Windows (los ':' rompen el filtro)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-i", _input(video), "-t", f"{end - start:.2f}",
         "-filter_complex", graph, "-map", "[v]", "-map", "0:a?", *_encoder(cpu), "-c:a", "aac", "-b:a", "160k",
         out.name],
        check=True, cwd=out.parent,
    )
