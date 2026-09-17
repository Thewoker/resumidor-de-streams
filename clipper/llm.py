import json
from pathlib import Path

import requests

SYSTEM = """Eres un editor de clips para redes sociales (TikTok, Shorts, Reels) de un streamer hispanohablante.
El streamer juega a videojuegos y a veces charla con amigos en llamada (se oyen varias voces).
Recibes un fragmento de la transcripción con segundos absolutos entre corchetes y los segundos donde hubo picos de volumen.

Busca momentos que funcionen como clip corto por sí solos:
- reacciones fuertes: gritos, sustos, rabia, celebraciones, "no puede ser", insultos de frustración
- momentos de juego: jugada increíble, muerte absurda, fallo ridículo, remontada, victoria
- humor: chistes, risas de todos en la llamada, piques entre amigos, frases absurdas sacadas de contexto
- anécdotas o opiniones con gancho que se entiendan sin contexto previo

Ignora silencios, explicaciones aburridas, leer la configuración, esperas en menús.
Sé exigente: la mayoría de fragmentos no tienen nada bueno. Si no hay nada, devuelve lista vacía.

Responde SOLO con JSON:
{"momentos": [{"inicio": <segundo>, "fin": <segundo>, "nota": <1-10>, "titulo": "<título corto y llamativo>", "motivo": "<por qué>"}]}
Cada momento debe durar entre 10 y 75 segundos e incluir el contexto necesario para entenderse."""


def unload(url, model):
    """Saca el modelo de la VRAM (Whisper y Ollama no caben juntos en 8 GB)."""
    try:
        requests.post(f"{url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
    except requests.RequestException:
        pass


def _windows(transcript, size=150, step=120):
    if not transcript:
        return
    end = transcript[-1]["end"]
    t = 0.0
    while t < end:
        segs = [s for s in transcript if s["end"] > t and s["start"] < t + size]
        if segs:
            yield t, t + size, segs
        t += step


def find_moments(transcript, audio_peaks, workdir: Path, url, model):
    cache = workdir / "llm_moments.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    moments = []
    windows = list(_windows(transcript))
    for n, (w_start, w_end, segs) in enumerate(windows, 1):
        text = "\n".join(f"[{int(s['start'])}] {s['text']}" for s in segs)
        near = [f"{p[0]}-{p[1]}s (fuerza {p[2]:.1f})" for p in audio_peaks if w_start <= p[0] <= w_end]
        user = f"Fragmento {int(w_start)}s - {int(w_end)}s\nPicos de volumen: {', '.join(near) or 'ninguno'}\n\n{text}"
        try:
            r = requests.post(
                f"{url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0.2, "num_ctx": 8192},
                },
                timeout=300,
            )
            r.raise_for_status()
            data = json.loads(r.json()["message"]["content"])
        except Exception as e:
            print(f"  ventana {n}/{len(windows)}: error {e}")
            continue

        for m in data.get("momentos", []):
            try:
                start = max(float(m["inicio"]), w_start - 30)
                end = min(float(m["fin"]), w_end + 30)
                nota = float(m["nota"])
            except (KeyError, TypeError, ValueError):
                continue
            if end - start < 8:
                end = start + 15
            end = min(end, start + 90)
            moments.append({
                "start": start, "end": end, "llm": max(1.0, min(nota, 10.0)),
                "title": str(m.get("titulo", "")).strip() or "Momento", "reason": str(m.get("motivo", "")).strip(),
            })
        print(f"  ventana {n}/{len(windows)}: {len(data.get('momentos', []))} momentos")

    cache.write_text(json.dumps(moments, ensure_ascii=False, indent=1), encoding="utf-8")
    return moments
