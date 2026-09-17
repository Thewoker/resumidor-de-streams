import re

import requests

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def list_vods(channel):
    r = requests.get(f"https://kick.com/api/v2/channels/{channel}/videos", headers=HEADERS, timeout=30)
    r.raise_for_status()
    vods = []
    for v in r.json():
        if v.get("is_live") or not v.get("source") or not v.get("video"):
            continue
        vods.append({
            "uuid": v["video"]["uuid"],
            "title": v.get("session_title") or "",
            "source": v["source"],
            "duration": (v.get("duration") or 0) / 1000,
            "created_at": v.get("created_at"),
            "url": f"https://kick.com/{channel}/videos/{v['video']['uuid']}",
        })
    return vods


def resolve(url):
    """https://kick.com/<canal>/videos/<uuid> -> VOD con su .m3u8, o None si no es una URL de Kick."""
    m = re.search(r"kick\.com/([^/]+)/videos/([0-9a-f-]{36})", url)
    if not m:
        return None
    return next((v for v in list_vods(m.group(1)) if v["uuid"] == m.group(2)), None)
