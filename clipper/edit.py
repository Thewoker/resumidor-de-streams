"""Plan de edición de un clip: qué trozos del stream se quedan y a qué velocidad.

    edit = {"cuts": [{"start": 601.0, "end": 612.5, "speed": 1},
                     {"start": 620.0, "end": 630.0, "speed": 2}],
            "subs": [{"start": 602.0, "end": 604.0, "text": "lo que realmente dije"}]}

Sin plan, el clip es un único trozo de `start` a `end` a velocidad normal.
"""
SPEEDS = (1, 1.25, 1.5, 2, 2.5, 3, 4)


def segments(m):
    cuts = (m.get("edit") or {}).get("cuts") or []
    segs = []
    for c in cuts:
        try:
            start, end = float(c["start"]), float(c["end"])
            speed = float(c.get("speed") or 1)
        except (KeyError, TypeError, ValueError):
            continue
        if end - start >= 0.3:
            segs.append({"start": start, "end": end, "speed": min(max(speed, 0.5), 4)})
    if not segs:
        return [{"start": float(m["start"]), "end": float(m["end"]), "speed": 1.0}]
    return sorted(segs, key=lambda s: s["start"])


def out_duration(segs):
    return sum((s["end"] - s["start"]) / s["speed"] for s in segs)


def to_out_time(segs, t):
    """Pasa un instante del stream al instante que le toca en el clip montado.
    Devuelve None si ese instante cae en un trozo eliminado."""
    acc = 0.0
    for s in segs:
        if t < s["start"]:
            return None
        if t <= s["end"]:
            return acc + (t - s["start"]) / s["speed"]
        acc += (s["end"] - s["start"]) / s["speed"]
    return None


def clamp_to_segments(segs, start, end):
    """Recorta un intervalo del stream al trozo que lo contiene (para los subtítulos)."""
    for s in segs:
        if end > s["start"] and start < s["end"]:
            return max(start, s["start"]), min(end, s["end"]), s
    return None
