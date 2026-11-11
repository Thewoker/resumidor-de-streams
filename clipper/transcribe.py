import gc
import json
import subprocess
import time
from pathlib import Path

# La gráfica se comparte con Ollama, que puede cargar un modelo en cualquier momento porque
# otros servicios también le consultan. Por eso aquí no se da por hecho que haya memoria:
# se elige el modelo según la libre y, si aun así falla, se reintenta más liviano y al final
# por CPU (lento, pero termina).


def vram_libre_mb():
    """Memoria libre de la gráfica, o None si no se puede consultar."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


def _planes(model_name, compute_type):
    """Configuraciones a probar, de mejor a más modesta: (modelo, dispositivo, precisión, beam)."""
    libre = vram_libre_mb()
    gpu = [(model_name, "cuda", compute_type, 5), ("medium", "cuda", "int8_float16", 2), ("small", "cuda", "int8", 1)]
    if libre is not None:
        # Con poca memoria libre ni se intenta el grande: tarda en fallar y encima a mitad del trabajo
        if libre < 2600:
            gpu = gpu[2:]
        elif libre < 4200:
            gpu = gpu[1:]
        print(f"  memoria libre en la grafica: {libre} MB, se empieza con {gpu[0][0]}")
    # Último recurso: CPU. No es rápido, pero no depende de que Ollama suelte la gráfica.
    return gpu + [("small", "cpu", "int8", 1)]


def _es_falta_de_memoria(e):
    return "out of memory" in str(e).lower()


def transcribe(audio: Path, workdir: Path, model_name="large-v3", compute_type="int8_float16", language="es",
               on_progress=None, on_loading=None, on_retry=None):
    """on_progress(segundos, frase); on_loading(modelo, dispositivo, es_reintento); on_retry() antes de reintentar."""
    cache = workdir / "transcript.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    from faster_whisper import WhisperModel

    planes = _planes(model_name, compute_type)
    ultimo = None
    for i, (nombre, device, precision, beam) in enumerate(planes):
        model = None
        try:
            if i and on_retry:
                on_retry()  # pedirle otra vez a Ollama que libere antes de insistir
            if on_loading:
                on_loading(nombre, device, i > 0)
            model = WhisperModel(nombre, device=device, compute_type=precision)
            segments, _ = model.transcribe(
                str(audio), language=language, vad_filter=True, word_timestamps=True, beam_size=beam
            )
            result = []
            for s in segments:   # acá es donde se transcribe de verdad, y donde puede faltar memoria
                result.append({
                    "start": round(s.start, 2),
                    "end": round(s.end, 2),
                    "text": s.text.strip(),
                    "words": [{"start": w.start, "end": w.end, "word": w.word.strip()} for w in (s.words or [])],
                })
                if on_progress:
                    on_progress(s.end, s.text.strip())
            if i:
                print(f"  transcrito con {nombre} en {device} tras {i} reintento(s)")
            cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            return result
        except Exception as e:
            ultimo = e
            if not _es_falta_de_memoria(e) or i == len(planes) - 1:
                raise
            print(f"  se quedó sin memoria con {nombre} en {device}: se reintenta más liviano")
        finally:
            # Soltar la gráfica pase lo que pase, si no el siguiente intento arranca peor
            del model
            gc.collect()
            time.sleep(5)
    raise RuntimeError(f"No se pudo transcribir ni por CPU. Último error: {ultimo}")
