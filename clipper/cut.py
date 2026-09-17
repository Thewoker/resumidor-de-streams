import subprocess
from pathlib import Path


def _encoder(cpu):
    if cpu:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-b:v", "0"]


def _ts(t):
    h, rem = divmod(max(t, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02}:{int(m):02}:{int(s):02},{int((s % 1) * 1000):03}"


def write_srt(transcript, start, end, path: Path, words_per_line=4):
    words = [
        w for s in transcript if s["end"] > start and s["start"] < end
        for w in s["words"] if start <= w["start"] < end
    ]
    lines = []
    for i in range(0, len(words), words_per_line):
        chunk = words[i: i + words_per_line]
        text = " ".join(w["word"] for w in chunk).upper()
        lines.append(f"{len(lines) + 1}\n{_ts(chunk[0]['start'] - start)} --> {_ts(chunk[-1]['end'] - start)}\n{text}\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def _input(video):
    p = Path(str(video))
    return str(p.resolve()) if p.exists() else str(video)


def horizontal(video, start, end, out: Path, cpu=False):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-i", _input(video), "-t", f"{end - start:.2f}",
         *_encoder(cpu), "-c:a", "aac", "-b:a", "160k", str(out)],
        check=True,
    )


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
