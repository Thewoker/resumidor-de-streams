"""Clips pedidos a viva voz durante el directo.

Si en la transcripción aparece algo como "hacé un clip de esto", se crea un momento
con los segundos anteriores a la frase. Estos clips no pasan por la nota de la IA:
los pediste vos, así que se cortan siempre y van los primeros.
"""
import re
import unicodedata

PALABRAS = (
    "haz un clip", "hace un clip", "hacé un clip", "has un clip", "hagan un clip",
    "clip de esto", "clipea esto", "clipeá esto", "clipealo", "clipéalo", "clipeen esto",
    "corta esto", "cortá esto", "guarda esto", "guardá esto", "eso es clip", "eso va al clip",
)


def normalizar(texto):
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto.lower())
                         if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9ñ ]+", " ", sin_tildes.replace("ñ", "ñ"))


def find_commands(transcript, palabras=PALABRAS, seconds=30.0, tail=1.0, total=None):
    """Momentos pedidos de viva voz: [frase - seconds, frase + tail]."""
    claves = [normalizar(p) for p in palabras if p.strip()]
    momentos = []
    for seg in transcript or []:
        texto = normalizar(seg.get("text") or "")
        clave = next((c for c in claves if c and c in texto), None)
        if not clave:
            continue
        t = float(seg["start"])
        start = max(0.0, t - seconds)
        end = float(seg.get("end") or t) + tail
        if total:
            end = min(end, total)
        if momentos and start < momentos[-1]["end"]:   # dos avisos seguidos: un solo clip
            momentos[-1]["end"] = max(momentos[-1]["end"], end)
            continue
        momentos.append({
            "start": round(start, 2), "end": round(end, 2),
            "title": "Clip pedido en el directo",
            "reason": f"Dijiste “{(seg.get('text') or '').strip()[:80]}”",
            "llm": None, "audio": None, "score": 10.0, "voice": True,
            "edit": {"cuts": []},
        })
    return momentos
