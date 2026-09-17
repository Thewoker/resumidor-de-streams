import os
from dataclasses import dataclass, field


def _env_bool(name, default):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "si")


@dataclass
class Settings:
    out: str = os.getenv("OUTPUT_DIR", "output")
    top: int = int(os.getenv("TOP_CLIPS", "15"))
    min_score: float = float(os.getenv("MIN_SCORE", "6"))
    lang: str = os.getenv("LANGUAGE", "es")
    whisper: str = os.getenv("WHISPER_MODEL", "large-v3")
    ollama: str = os.getenv("OLLAMA_URL", "http://192.168.1.199:11434")
    model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    vertical: bool = _env_bool("VERTICAL", "1")
    cpu: bool = _env_bool("CPU_ENCODE", "0")
    keep_video: bool = _env_bool("KEEP_VIDEO", "0")
    channel: str = os.getenv("KICK_CHANNEL", "elmemesolitario")
    auto_process: bool = _env_bool("AUTO_PROCESS", "1")
    interval: int = int(os.getenv("CHECK_INTERVAL", "600"))
    skip_titles: list = field(default_factory=lambda: [
        t.strip().lower() for t in os.getenv("SKIP_TITLES", "").split(",") if t.strip()
    ])
