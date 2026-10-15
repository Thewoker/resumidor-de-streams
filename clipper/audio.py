import subprocess
from pathlib import Path

import numpy as np

RATE = 16000


def loudness(audio: Path, workdir: Path, on_progress=None) -> np.ndarray:
    """Volumen en dB por segundo, guardado en caché. on_progress(segundos_analizados)"""
    cache = workdir / "loudness.npy"
    if cache.exists():
        return np.load(cache)
    db = loudness_of(audio, on_progress)
    np.save(cache, db)
    return db


def loudness_of(audio, on_progress=None) -> np.ndarray:
    """Volumen en dB por segundo de un archivo cualquiera, sin caché."""
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(audio), "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        stdout=subprocess.PIPE,
    )
    values = []
    while True:
        buf = proc.stdout.read(RATE * 2)
        if len(buf) < 2:
            break
        a = np.frombuffer(buf[: len(buf) // 2 * 2], dtype=np.int16).astype(np.float32)
        values.append(np.sqrt(np.mean(a * a)) + 1e-6)
        if on_progress and len(values) % 60 == 0:
            on_progress(len(values))
    proc.wait()
    return 20 * np.log10(np.array(values) / 32768.0)


def find_offset(window_db: np.ndarray, full_db: np.ndarray, around: float, search=90):
    """Busca en qué segundo del VOD empieza realmente un tramo descargado, comparando
    su curva de volumen con la del stream entero. Devuelve (segundo, confianza 0-1)."""
    lo = max(0, int(around) - search)
    hi = min(len(full_db), int(around) + search + len(window_db))
    zona = full_db[lo:hi]
    if len(zona) < len(window_db) + 2 or len(window_db) < 5:
        return None, 0.0
    w = window_db - window_db.mean()
    mejores = []
    for i in range(len(zona) - len(window_db) + 1):
        trozo = zona[i:i + len(window_db)]
        t = trozo - trozo.mean()
        denom = np.sqrt((w * w).sum() * (t * t).sum()) + 1e-9
        mejores.append(float((w * t).sum() / denom))
    mejores = np.array(mejores)
    i = int(np.argmax(mejores))
    # Confianza: cuánto destaca el mejor encaje sobre el resto
    resto = np.delete(mejores, slice(max(0, i - 3), i + 4))
    margen = mejores[i] - (resto.max() if len(resto) else 0)
    return lo + i, float(min(max(margen * 2, 0), 1)) if mejores[i] > 0.5 else 0.0


def excitement(db: np.ndarray, baseline_window=120) -> np.ndarray:
    """Cuánto sobresale cada segundo respecto al volumen normal de su zona (z-score local)."""
    n = len(db)
    half = baseline_window // 2
    baseline = np.array([np.median(db[max(0, i - half): i + half]) for i in range(n)])
    diff = db - baseline
    z = diff / (np.std(diff) + 1e-6)
    return np.convolve(z, np.ones(3) / 3, mode="same")


def peaks(z: np.ndarray, threshold=2.5, gap=5):
    """Agrupa segundos por encima del umbral en picos (inicio, fin, intensidad)."""
    idx = np.where(z > threshold)[0]
    result = []
    for i in idx:
        if result and i - result[-1][1] <= gap:
            result[-1][1] = int(i)
            result[-1][2] = max(result[-1][2], float(z[i]))
        else:
            result.append([int(i), int(i), float(z[i])])
    return [tuple(p) for p in result]


def score_range(z: np.ndarray, start, end) -> float:
    seg = z[int(max(0, start)): int(end) + 1]
    if len(seg) == 0:
        return 0.0
    return float(min(max(seg.max(), 0) / 4.0, 1.0))
