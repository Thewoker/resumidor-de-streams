"""Analiza un stream y saca los mejores clips.

    python clip.py https://kick.com/elmemesolitario/videos/<uuid>
    python clip.py stream.mp4 --top 20 --no-vertical
"""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from clipper import audio, cut, kick, llm, media, notify, scoring, transcribe


def fmt(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02}:{int(t % 60):02}"


def add_options(p):
    p.add_argument("--out", default=os.getenv("OUTPUT_DIR", "output"))
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--min-score", type=float, default=6, help="nota mínima de la IA (1-10)")
    p.add_argument("--lang", default="es")
    p.add_argument("--whisper", default=os.getenv("WHISPER_MODEL", "large-v3"))
    p.add_argument("--ollama", default=os.getenv("OLLAMA_URL", "http://192.168.1.199:11434"))
    p.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"))
    p.add_argument("--no-vertical", action="store_true")
    p.add_argument("--cpu", action="store_true", help="codificar con libx264 en vez de NVENC")
    p.add_argument("--keep-video", action="store_true", help="no borrar el VOD descargado al terminar")


def process(source, args, key=None, title=""):
    if key is None:
        if Path(source).is_file():
            key = Path(source).stem
        else:
            vod = kick.resolve(source)
            if vod:
                source, key, title = vod["source"], vod["uuid"], vod["title"]
            else:
                key = hashlib.md5(source.encode()).hexdigest()[:10]

    workdir = Path(args.out) / key
    clips_dir = workdir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    print(f"1/6 Obteniendo vídeo... {title}")
    video = media.get_video(source, workdir)
    total = media.duration(video)
    print(f"    {video} ({fmt(total)})")

    print("2/6 Extrayendo audio...")
    wav = media.extract_audio(video, workdir)

    print("3/6 Analizando volumen...")
    z = audio.excitement(audio.loudness(wav, workdir))
    peaks = audio.peaks(z)
    print(f"    {len(peaks)} picos")

    print("4/6 Transcribiendo (Whisper)...")
    transcript = transcribe.transcribe(wav, workdir, args.whisper, language=args.lang)

    print("5/6 Buscando momentos con la IA...")
    moments = llm.find_moments(transcript, peaks, workdir, args.ollama, args.model)
    chosen = scoring.rank(moments, peaks, z, total, top=args.top, min_llm=args.min_score)

    print(f"6/6 Cortando {len(chosen)} clips...")
    for i, c in enumerate(chosen, 1):
        name = f"{i:02}_{media.slug(c['title'])}"
        c["file"] = f"{name}.mp4"
        cut.horizontal(video, c["start"], c["end"], clips_dir / c["file"], args.cpu)
        if not args.no_vertical:
            srt = clips_dir / f"{name}.srt"
            cut.write_srt(transcript, c["start"], c["end"], srt)
            c["vertical"] = f"{name}_vertical.mp4"
            cut.vertical(video, c["start"], c["end"], clips_dir / c["vertical"], srt.name, args.cpu)
        print(f"    {i:02}. [{fmt(c['start'])}] {c['score']:.2f} {c['title']}")

    (workdir / "report.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"{i}. [{fmt(c['start'])}-{fmt(c['end'])}] {c['title']} ({c['score']:.2f})\n   {c['reason']}"
             for i, c in enumerate(chosen, 1)]
    (workdir / "report.md").write_text("\n".join(lines), encoding="utf-8")

    # El VOD completo pesa varios GB; los clips y la caché de análisis se quedan
    if not args.keep_video and video.parent == workdir:
        video.unlink(missing_ok=True)
        wav.unlink(missing_ok=True)

    summary = f"🎬 {title or key}\n{len(chosen)} clips en {int((time.time() - t0) / 60)} min\n\n" + "\n".join(lines)
    print("\n" + summary)
    notify.telegram(summary)
    return chosen


def main():
    p = argparse.ArgumentParser()
    p.add_argument("source", help="URL del VOD (Kick/Twitch) o archivo de vídeo")
    add_options(p)
    args = p.parse_args()
    process(args.source, args)


if __name__ == "__main__":
    main()
