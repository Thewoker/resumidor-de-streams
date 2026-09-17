"""Procesa un VOD desde la línea de comandos (la interfaz web está en server.py).

    python clip.py https://kick.com/elmemesolitario/videos/<uuid>
    python clip.py stream.mp4
"""
import argparse
import hashlib
from pathlib import Path

from clipper import kick, pipeline
from clipper.config import Settings


def main():
    p = argparse.ArgumentParser()
    p.add_argument("source", help="URL del VOD de Kick o archivo de vídeo")
    p.add_argument("--top", type=int)
    p.add_argument("--min-score", type=float)
    args = p.parse_args()

    s = Settings()
    if args.top:
        s.top = args.top
    if args.min_score:
        s.min_score = args.min_score

    source, title, meta = args.source, "", {}
    if Path(source).is_file():
        key = Path(source).stem
    elif vod := kick.resolve(source):
        source, key, title, meta = vod["source"], vod["uuid"], vod["title"], vod
    else:
        key = hashlib.md5(source.encode()).hexdigest()[:10]

    for m in pipeline.process(source, s, key, title, meta)[: s.top]:
        print(f"{m['id']:>3}. [{pipeline.fmt(m['start'])}] {m['score']:.2f} {m['title']}")


if __name__ == "__main__":
    main()
