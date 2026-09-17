import gc
import json
from pathlib import Path


def transcribe(audio: Path, workdir: Path, model_name="large-v3", compute_type="int8_float16", language="es"):
    cache = workdir / "transcript.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cuda", compute_type=compute_type)
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
        print(f"  [{int(s.start // 60):>4}:{int(s.start % 60):02}] {s.text.strip()[:80]}")

    # Liberar la VRAM antes de que Ollama cargue su modelo (8 GB no dan para los dos)
    del model
    gc.collect()

    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result
