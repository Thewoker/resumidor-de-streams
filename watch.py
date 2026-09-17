"""Vigila el canal de Kick y procesa cada VOD nuevo.

    python watch.py                      # solo VODs que salgan a partir de ahora
    python watch.py --backfill 3         # además procesa los 3 últimos
"""
import argparse
import json
import os
import time
import traceback
from pathlib import Path

from clip import add_options, process
from clipper import kick, notify


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--channel", default=os.getenv("KICK_CHANNEL", "elmemesolitario"))
    p.add_argument("--interval", type=int, default=600, help="segundos entre comprobaciones")
    p.add_argument("--backfill", type=int, default=0, help="procesar también los N últimos VODs existentes")
    add_options(p)
    args = p.parse_args()

    state_file = Path(args.out) / "processed.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    if state_file.exists():
        done = set(json.loads(state_file.read_text()))
    else:
        # Primera vez: marcar como hechos los VODs viejos (salvo los de --backfill)
        vods = kick.list_vods(args.channel)
        done = {v["uuid"] for v in vods[args.backfill:]}
    save = lambda: state_file.write_text(json.dumps(sorted(done)))
    save()
    print(f"Vigilando kick.com/{args.channel} cada {args.interval}s")

    while True:
        try:
            for vod in reversed(kick.list_vods(args.channel)):  # del más antiguo al más nuevo
                if vod["uuid"] in done:
                    continue
                print(f"\nVOD nuevo: {vod['title']} ({vod['duration'] / 3600:.1f} h)")
                try:
                    process(vod["source"], args, key=vod["uuid"], title=vod["title"])
                except Exception:
                    traceback.print_exc()
                    notify.telegram(f"❌ Error procesando {vod['title']}\n{traceback.format_exc()[-1500:]}")
                done.add(vod["uuid"])
                save()
        except Exception as e:
            print(f"Error consultando Kick: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
