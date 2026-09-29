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


def _merge(segs):
    out = []
    for s in segs:
        if out and abs(out[-1]["end"] - s["start"]) < 0.05 and out[-1]["speed"] == s["speed"]:
            out[-1]["end"] = s["end"]
        else:
            out.append(dict(s))
    return [s for s in out if s["end"] - s["start"] >= 0.4]


def _blocks(segs, z, block=2.0):
    """Trocea el momento en bloques cortos con su intensidad de audio."""
    out = []
    for s in segs:
        t = s["start"]
        while t < s["end"] - 0.1:
            end = min(t + block, s["end"])
            a, b = int(t), max(int(end), int(t) + 1)
            score = float(max(z[a:b])) if z is not None and len(z) > a else 0.0
            out.append({"start": t, "end": end, "score": score})
            t = end
    return out


def _build(blocks, threshold, bridge=10.0, bridge_speed=2.0, pad=1.5, min_keep=3.0):
    """Trozos interesantes a 1x (con algo de aire alrededor); los huecos cortos se aceleran
    y los largos se tiran. Nada de trocitos sueltos: quedan clips picados."""
    lo, hi = blocks[0]["start"], blocks[-1]["end"]
    keeps, run = [], None
    for b in blocks:
        if b["score"] >= threshold:
            run = run or {"start": b["start"], "end": b["end"]}
            run["end"] = b["end"]
        elif run:
            keeps.append(run)
            run = None
    if run:
        keeps.append(run)

    # Aire antes y después, longitud mínima, y unir los que queden pegados
    padded = []
    for k in keeps:
        start, end = max(lo, k["start"] - pad), min(hi, k["end"] + pad)
        if end - start < min_keep:
            end = min(hi, start + min_keep)
            start = max(lo, end - min_keep)
        if padded and start <= padded[-1]["end"] + 0.3:
            padded[-1]["end"] = max(padded[-1]["end"], end)
        else:
            padded.append({"start": start, "end": end})

    segs = []
    for i, k in enumerate(padded):
        gap = k["start"] - padded[i - 1]["end"] if i else 0
        if i and gap <= bridge:  # hueco corto: se acelera y no se corta el hilo
            if gap < 1.5:  # tan corto que no merece un cambio de velocidad
                k = {"start": padded[i - 1]["end"], "end": k["end"]}
            else:
                segs.append({"start": padded[i - 1]["end"], "end": k["start"], "speed": bridge_speed})
        segs.append({"start": k["start"], "end": k["end"], "speed": 1.0})
    return _merge(segs)


def autofit(segs, z, max_dur=60.0):
    """Si el clip pasa del máximo, lo monta solo: corta lo flojo y acelera el relleno."""
    if out_duration(segs) <= max_dur:
        return segs, False

    blocks = _blocks(segs, z)
    if not blocks:
        return segs, False
    scores = sorted(b["score"] for b in blocks)

    for keep in (0.6, 0.5, 0.4, 0.3, 0.25, 0.2, 0.15, 0.1):
        threshold = scores[min(int(len(scores) * (1 - keep)), len(scores) - 1)]
        for bridge_speed in (2.0, 3.0):
            candidate = _build(blocks, threshold, bridge_speed=bridge_speed)
            if candidate and out_duration(candidate) <= max_dur:
                return candidate, True

    # Último recurso: la ventana de max_dur alrededor del bloque más fuerte, con aire al final
    best = max(blocks, key=lambda b: b["score"])
    end = min(segs[-1]["end"], best["end"] + max_dur * 0.35)
    return [{"start": max(segs[0]["start"], end - max_dur), "end": end, "speed": 1.0}], True


def clamp_to_segments(segs, start, end):
    """Recorta un intervalo del stream al trozo que lo contiene (para los subtítulos)."""
    for s in segs:
        if end > s["start"] and start < s["end"]:
            return max(start, s["start"]), min(end, s["end"]), s
    return None
