import re
import subprocess
from pathlib import Path


def slug(text, max_len=50):
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", text)[:max_len] or "clip"


def get_video(source, workdir: Path) -> Path:
    """Devuelve la ruta al vídeo: si es un archivo local lo usa, si es una URL (Kick, Twitch...) la descarga."""
    if Path(source).is_file():
        return Path(source)
    existing = [p for p in workdir.glob("video.*") if ".part" not in p.name]
    if existing:
        return existing[0]

    if ".m3u8" in source:
        # HLS directo (VODs de Kick): copiar sin recodificar
        out = workdir / "video.mp4"
        part = workdir / "video.part.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-stats", "-i", source, "-c", "copy", "-bsf:a", "aac_adtstoasc", str(part)],
            check=True,
        )
        part.rename(out)
        return out

    import yt_dlp

    opts = {
        "outtmpl": str(workdir / "video.%(ext)s"),
        "format": "best",
        "merge_output_format": "mp4",
        "concurrent_fragment_downloads": 8,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(source, download=True)
        return Path(ydl.prepare_filename(info))


def extract_audio(video: Path, workdir: Path) -> Path:
    audio = workdir / "audio.wav"
    if not audio.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio)],
            check=True,
        )
    return audio


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())
