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

MONTAJE: además de elegir el momento, móntalo para que se haga corto y entretenido, con dos herramientas:
- CORTE: quedarte solo con los trozos buenos y tirar el resto (silencios, repeticiones, gente esperando, explicaciones largas).
- CÁMARA RÁPIDA: acelerar los trozos necesarios pero aburridos (caminar, buscar objetos, recargar, menús) para no perder el hilo.

Reglas del montaje:
- Devuelve el montaje en "cortes": trozos en orden, sin solaparse, dentro de inicio y fin.
- "velocidad": 1 para lo que se oye y tiene gracia, 1.5 o 2 para relleno, 3 solo para tramos largos sin nada.
- El remate (el grito, la risa, la frase graciosa, la muerte) SIEMPRE a velocidad 1 y con un par de segundos de aire después.
- REGLA DURA: el clip final NUNCA puede pasar de 60 segundos. Apunta a 20-45 segundos.
- Si el momento bruto dura más de un minuto, recórtalo de verdad: tira lo que no aporte y acelera el resto.
- Si el momento ya es corto y va seguido, deja "cortes" vacío.

Responde SOLO con JSON:
{"momentos": [{"inicio": <segundo>, "fin": <segundo>, "nota": <1-10>, "titulo": "<título corto y llamativo>", "motivo": "<por qué>",
  "cortes": [{"inicio": <segundo>, "fin": <segundo>, "velocidad": 1}]}]}
Cada momento debe durar entre 10 y 75 segundos e incluir el contexto necesario para entenderse."""


def _cuts(raw, start, end):
    """Valida el montaje propuesto por la IA: trozos dentro del momento, en orden y sin solapes."""
    cuts = []
    for c in raw or []:
        try:
            a, b = float(c["inicio"]), float(c["fin"])
            speed = float(c.get("velocidad") or 1)
        except (KeyError, TypeError, ValueError):
            continue
        a, b = max(a, start), min(b, end)
        if b - a < 1:
            continue
        if cuts and a < cuts[-1]["end"]:
            a = cuts[-1]["end"]
            if b - a < 1:
                continue
        cuts.append({"start": round(a, 2), "end": round(b, 2), "speed": min(max(speed, 1), 4)})
    # Un único trozo a velocidad normal es lo mismo que no montar nada
    if len(cuts) == 1 and cuts[0]["speed"] == 1:
        return []
    return cuts


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


def find_moments(transcript, audio_peaks, workdir: Path, url, model, on_progress=None):
    """on_progress(fragmento_actual, total_fragmentos, momentos_encontrados)"""
    cache = workdir / "llm_moments.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    moments = []
    windows = list(_windows(transcript))
    for n, (w_start, w_end, segs) in enumerate(windows, 1):
        if on_progress:
            on_progress(n - 1, len(windows), len(moments))
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
                "edit": {"cuts": _cuts(m.get("cortes"), start, end)},
            })
        print(f"  ventana {n}/{len(windows)}: {len(data.get('momentos', []))} momentos")

    cache.write_text(json.dumps(moments, ensure_ascii=False, indent=1), encoding="utf-8")
    return moments
