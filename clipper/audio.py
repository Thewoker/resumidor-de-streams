import subprocess
from pathlib import Path

import numpy as np

RATE = 16000


def loudness(audio: Path, workdir: Path, on_progress=None) -> np.ndarray:
    """Volumen en dB por segundo. on_progress(segundos_analizados)"""
    cache = workdir / "loudness.npy"
    if cache.exists():
        return np.load(cache)
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
    db = 20 * np.log10(np.array(values) / 32768.0)
    np.save(cache, db)
    return db


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
            result[-1][1] = i
            result[-1][2] = max(result[-1][2], float(z[i]))
        else:
            result.append([int(i), int(i), float(z[i])])
    return [tuple(p) for p in result]


def score_range(z: np.ndarray, start, end) -> float:
    seg = z[int(max(0, start)): int(end) + 1]
    if len(seg) == 0:
        return 0.0
    return float(min(max(seg.max(), 0) / 4.0, 1.0))
