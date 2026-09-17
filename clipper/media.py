import re
import subprocess
from pathlib import Path


def slug(text, max_len=50):
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", text)[:max_len] or "clip"


def run_ffmpeg(args, on_progress=None):
    """Ejecuta ffmpeg llamando a on_progress(segundos_procesados, info) a medida que avanza."""
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1", *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    info = {}
    for line in proc.stdout:
        key, _, value = line.strip().partition("=")
        info[key] = value
        if key == "progress" and on_progress:
            try:
                seconds = int(info.get("out_time_us", "0")) / 1e6
            except ValueError:
                seconds = 0
            on_progress(seconds, info)
    err = proc.stderr.read()
    proc.wait()
    if proc.returncode:
        raise RuntimeError(f"ffmpeg falló: {err.strip()[-500:]}")


def get_video(source, workdir: Path, on_progress=None) -> Path:
    """Devuelve la ruta al vídeo: si es un archivo local lo usa, si es una URL (Kick, Twitch...) la descarga.
    on_progress(segundos_descargados, megas, velocidad)"""
    if Path(source).is_file():
        return Path(source)
    existing = [p for p in workdir.glob("video.*") if ".part" not in p.name]
    if existing:
        return existing[0]

    if ".m3u8" in source:
        # HLS directo (VODs de Kick): copiar sin recodificar
        out = workdir / "video.mp4"
        part = workdir / "video.part.mp4"

        def progress(seconds, info):
            if on_progress:
                size = int(info.get("total_size", "0") or 0) / 1e6 if info.get("total_size", "N/A") != "N/A" else 0
                on_progress(seconds, size, info.get("speed", "").strip())

        run_ffmpeg(["-i", source, "-c", "copy", "-bsf:a", "aac_adtstoasc", str(part)], progress)
        part.rename(out)
        return out

    import yt_dlp

    def hook(d):
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            frac = d.get("downloaded_bytes", 0) / total if total else 0
            on_progress(None, d.get("downloaded_bytes", 0) / 1e6, f"{frac:.0%}")

    opts = {
        "outtmpl": str(workdir / "video.%(ext)s"),
        "format": "best",
        "merge_output_format": "mp4",
        "concurrent_fragment_downloads": 8,
        "progress_hooks": [hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(source, download=True)
        return Path(ydl.prepare_filename(info))


def extract_audio(video: Path, workdir: Path, on_progress=None) -> Path:
    audio = workdir / "audio.wav"
    if not audio.exists():
        part = workdir / "audio.part.wav"
        run_ffmpeg(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(part)],
                   lambda seconds, _: on_progress and on_progress(seconds))
        part.rename(audio)
    return audio


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())
