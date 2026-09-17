from . import audio


def _overlap(a, b):
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    return inter / max(1.0, min(a["end"] - a["start"], b["end"] - b["start"]))


def rank(llm_moments, audio_peaks, z, total_duration, top=15, min_llm=6, pad_before=4, pad_after=3):
    candidates = []
    for m in llm_moments:
        if m["llm"] < min_llm:
            continue
        a = audio.score_range(z, m["start"], m["end"])
        candidates.append({**m, "audio": a, "score": 0.65 * m["llm"] / 10 + 0.35 * a})

    # Picos de audio muy fuertes que la IA no marcó (p. ej. grito sin palabras)
    for p_start, p_end, strength in audio_peaks:
        if strength < 3.5:
            continue
        peak = {"start": p_start - 20, "end": p_end + 10}
        if any(_overlap(peak, c) > 0.3 for c in candidates):
            continue
        a = min(strength / 4.0, 1.0)
        candidates.append({
            **peak, "llm": None, "audio": a, "score": 0.35 * a + 0.2,
            "title": "Pico de audio", "reason": f"Subida de volumen fuerte ({strength:.1f})",
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    chosen = []
    for c in candidates:
        if any(_overlap(c, o) > 0.3 for o in chosen):
            continue
        c["start"] = max(0.0, c["start"] - pad_before)
        c["end"] = min(total_duration, c["end"] + pad_after)
        chosen.append(c)
        if len(chosen) >= top:
            break
    return chosen
