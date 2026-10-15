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


def _run(args, cpu=False, cwd=None):
    """Lanza ffmpeg contando qué falló; si NVENC no puede, reintenta por CPU."""
    proc = subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    if proc.returncode == 0:
        return
    err = (proc.stderr or "").strip()
    if not cpu and ("nvenc" in err.lower() or "cuda" in err.lower()):
        print(f"  NVENC falló ({err.splitlines()[-1][:120] if err else '?'}), se reintenta por CPU")
        _run([a if a != "h264_nvenc" else "libx264" for a in args if a not in ("-preset", "p5", "-cq", "21")],
             cpu=True, cwd=cwd)
        return
    raise RuntimeError(f"ffmpeg falló: {err[-600:] or 'sin detalle'}")


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
    args = ["ffmpeg", "-y", "-v", "error", "-accurate_seek",
            "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", _input(video)]
    graph = (f"[0:v]setpts=(PTS-STARTPTS)/{speed:.4f},fps=60[v];"
             f"[0:a]asetpts=PTS-STARTPTS,{_atempo(speed)},aresample=async=1:first_pts=0[a]")
    args += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]"]
    args += [*_encoder(cpu), "-c:a", "aac", "-b:a", "160k", str(out)]
    _run(args, cpu)


def montage(video, segs, out: Path, cpu=False):
    """Monta el clip a partir de varios trozos, cada uno con su velocidad.

    Todo en un único ffmpeg con el filtro concat: cortar cada trozo por separado y pegarlos
    después iba desfasando el audio respecto al vídeo trozo a trozo."""
    if len(segs) == 1:
        horizontal(video, segs[0]["start"], segs[0]["end"], out, cpu, segs[0]["speed"])
        return out

    args = ["ffmpeg", "-y", "-v", "error"]
    for s in segs:
        args += ["-accurate_seek", "-ss", f"{s['start']:.3f}", "-t", f"{s['end'] - s['start']:.3f}",
                 "-i", _input(video)]

    chains, labels = [], ""
    for i, s in enumerate(segs):
        speed = s["speed"]
        v = f"[{i}:v]setpts=(PTS-STARTPTS)/{speed:.4f},fps=60[v{i}]"
        a = f"[{i}:a]asetpts=PTS-STARTPTS,{_atempo(speed)},aresample=async=1:first_pts=0[a{i}]"
        chains += [v, a]
        labels += f"[v{i}][a{i}]"
    graph = ";".join(chains) + f";{labels}concat=n={len(segs)}:v=1:a=1[v][a]"

    args += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]",
             *_encoder(cpu), "-c:a", "aac", "-b:a", "160k", str(out)]
    _run(args, cpu)
    return out


def vertical(video, start, end, out: Path, srt_name=None, cpu=False):
    """9:16 con el juego centrado sobre fondo desenfocado y subtítulos quemados."""
    style = ("Fontname=DejaVu Sans,Fontsize=13,Bold=1,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=70")
    # Sin subtítulos (o con un .srt vacío) el filtro se deja fuera: si no, ffmpeg falla
    srt = out.parent / srt_name if srt_name else None
    subs = f",subtitles={srt_name}:force_style='{style}'" if srt and srt.exists() and srt.stat().st_size > 10 else ""
    graph = (
        "[0:v]split[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];"
        "[b]scale=1080:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2{subs}[v]"
    )
    # cwd = carpeta del clip para pasar el .srt sin rutas de Windows (los ':' rompen el filtro)
    _run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-i", _input(video), "-t", f"{end - start:.2f}",
         "-filter_complex", graph, "-map", "[v]", "-map", "0:a?", *_encoder(cpu), "-c:a", "aac", "-b:a", "160k",
         out.name],
        cpu, cwd=out.parent,
    )
