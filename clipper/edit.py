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


def speech_of(transcript):
    """Tramos en los que se está hablando, según la transcripción."""
    return [{"start": float(s["start"]), "end": float(s["end"])}
            for s in (transcript or []) if (s.get("text") or "").strip()]


def _phrase_at(speech, t):
    return next((p for p in speech if p["start"] - 0.15 <= t <= p["end"] + 0.15), None)


def _talks_between(speech, a, b):
    return any(p["end"] > a + 0.15 and p["start"] < b - 0.15 for p in speech)


def _snap(keeps, speech):
    """Ningún corte a mitad de frase: si un borde cae dentro de algo hablado, se estira hasta el silencio."""
    if not speech:
        return keeps
    out = []
    for k in keeps:
        start, end = k["start"], k["end"]
        p = _phrase_at(speech, start)
        if p:
            start = min(start, p["start"])
        p = _phrase_at(speech, end)
        if p:
            end = max(end, p["end"])
        if out and start <= out[-1]["end"] + 0.3:
            out[-1]["end"] = max(out[-1]["end"], end)
        else:
            out.append({"start": start, "end": end})
    return out


def _build(blocks, threshold, bridge=10.0, bridge_speed=2.0, pad=1.5, min_keep=3.0,
           speech=(), context_gap=5.0):
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

    padded = _snap(padded, speech)

    # Continuidad: un clip es UNA escena. Se corta donde cambia el contexto y solo se acelera
    # lo que queda entre medias si ahí no se está hablando.
    clusters, current = [], []
    for k in padded:
        if current:
            gap_a, gap_b = current[-1]["end"], k["start"]
            hablando = _talks_between(speech, gap_a, gap_b)
            largo = gap_b - gap_a > bridge
            # Si en el hueco se habla, saltárselo cambiaría de tema a mitad: se corta el clip ahí
            if hablando or largo:
                clusters.append(current)
                current = []
        current.append(k)
    if current:
        clusters.append(current)

    # Un silencio largo dentro de un trozo también marca final de contexto
    if speech and context_gap:
        split = []
        for cluster in clusters:
            piece = []
            for k in cluster:
                start = k["start"]
                for a, b in zip(speech, speech[1:]):
                    if a["end"] > start and b["start"] < k["end"] and b["start"] - a["end"] >= context_gap:
                        piece.append({"start": start, "end": a["end"]})
                        split.append(piece)
                        piece = []
                        start = b["start"]
                piece.append({"start": start, "end": k["end"]})
            if piece:
                split.append(piece)
        clusters = [c for c in split if c and c[-1]["end"] - c[0]["start"] >= min_keep]

    def weight(cluster):
        a, b = cluster[0]["start"], cluster[-1]["end"]
        return sum(x["score"] * (x["end"] - x["start"]) for x in blocks if x["start"] >= a and x["end"] <= b)

    padded = max(clusters, key=weight) if clusters else []

    segs = []
    for i, k in enumerate(padded):
        gap = k["start"] - padded[i - 1]["end"] if i else 0
        if i and gap > 0:
            if gap < 1.5:  # tan corto que no merece un cambio de velocidad
                k = {"start": padded[i - 1]["end"], "end": k["end"]}
            else:  # hueco en silencio: se acelera como transición
                segs.append({"start": padded[i - 1]["end"], "end": k["start"], "speed": bridge_speed})
        segs.append({"start": k["start"], "end": k["end"], "speed": 1.0})
    return _merge(_trim_silence(segs, speech))


def _trim_silence(segs, speech, lead=2.0, tail=2.5):
    """Fuera el silencio sobrante antes de la primera frase y después de la última."""
    if not segs or not speech:
        return segs
    a, b = segs[0]["start"], segs[-1]["end"]
    dentro = [p for p in speech if p["end"] > a and p["start"] < b]
    if not dentro:
        return segs
    lo, hi = max(a, dentro[0]["start"] - lead), min(b, dentro[-1]["end"] + tail)
    out = []
    for s in segs:
        start, end = max(s["start"], lo), min(s["end"], hi)
        if end - start >= 0.4:
            out.append({**s, "start": start, "end": end})
    return out or segs


def autofit(segs, z, max_dur=60.0, transcript=None):
    """Si el clip pasa del máximo, lo monta solo: se queda con una escena, corta lo flojo
    y acelera los huecos en silencio. Nunca corta a mitad de frase."""
    if out_duration(segs) <= max_dur:
        return segs, False

    blocks = _blocks(segs, z)
    if not blocks:
        return segs, False
    speech = speech_of(transcript)
    scores = sorted(b["score"] for b in blocks)

    for keep in (0.6, 0.5, 0.4, 0.3, 0.25, 0.2, 0.15, 0.1):
        threshold = scores[min(int(len(scores) * (1 - keep)), len(scores) - 1)]
        for bridge_speed in (2.0, 3.0):
            candidate = _build(blocks, threshold, bridge_speed=bridge_speed, speech=speech)
            if candidate and out_duration(candidate) <= max_dur:
                return candidate, True

    # Último recurso: la ventana de max_dur alrededor del bloque más fuerte, cortando en silencios
    best = max(blocks, key=lambda b: b["score"])
    end = min(segs[-1]["end"], best["end"] + max_dur * 0.35)
    start = max(segs[0]["start"], end - max_dur)
    [snapped] = _snap([{"start": start, "end": end}], speech) or [{"start": start, "end": end}]
    snapped["end"] = min(snapped["end"], snapped["start"] + max_dur)
    return [{**snapped, "speed": 1.0}], True


def clamp_to_segments(segs, start, end):
    """Recorta un intervalo del stream al trozo que lo contiene (para los subtítulos)."""
    for s in segs:
        if end > s["start"] and start < s["end"]:
            return max(start, s["start"]), min(end, s["end"]), s
    return None
