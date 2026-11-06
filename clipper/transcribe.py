import gc
import json
import time
from pathlib import Path

# Si no entra en la gráfica, se va probando más liviano antes que rendirse.
# (modelo, precisión): large-v3 es el bueno; medium y small gastan bastante menos memoria.
ALTERNATIVAS = [("medium", "int8_float16"), ("small", "int8")]


def _es_falta_de_memoria(e):
    return "out of memory" in str(e).lower()


def _cargar(model_name, compute_type, on_loading=None):
    from faster_whisper import WhisperModel

    intentos = [(model_name, compute_type)] + [a for a in ALTERNATIVAS if a[0] != model_name]
    ultimo = None
    for i, (nombre, precision) in enumerate(intentos):
        try:
            if on_loading:
                on_loading(nombre, i > 0)
            return WhisperModel(nombre, device="cuda", compute_type=precision), nombre
        except Exception as e:
            ultimo = e
            if not _es_falta_de_memoria(e):
                raise
            print(f"  no entró {nombre} en la gráfica, se libera y se prueba otro: {e}")
            gc.collect()
            time.sleep(5)
    raise RuntimeError(
        "La gráfica no tiene memoria libre ni para el modelo más chico. "
        f"Algo más la está usando (¿Ollama u otro contenedor?). Último error: {ultimo}")


def transcribe(audio: Path, workdir: Path, model_name="large-v3", compute_type="int8_float16", language="es",
               on_progress=None, on_loading=None):
    """on_progress(segundos_transcritos, ultima_frase); on_loading() mientras carga/descarga el modelo."""
    cache = workdir / "transcript.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    def aviso(nombre, es_alternativa):
        if on_loading:
            on_loading(nombre, es_alternativa)

    model, usado = _cargar(model_name, compute_type, aviso)
    if usado != model_name:
        print(f"  transcribiendo con {usado} en vez de {model_name} por falta de memoria en la gráfica")
    segments, _ = model.transcribe(
        str(audio), language=language, vad_filter=True, word_timestamps=True, beam_size=5
    )
    result = []
    for s in segments:
        result.append({
            "start": round(s.start, 2),
            "end": round(s.end, 2),
            "text": s.text.strip(),
            "words": [{"start": w.start, "end": w.end, "word": w.word.strip()} for w in (s.words or [])],
        })
        if on_progress:
            on_progress(s.end, s.text.strip())

    # Liberar la VRAM antes de que Ollama cargue su modelo (8 GB no dan para los dos)
    del model
    gc.collect()

    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result
