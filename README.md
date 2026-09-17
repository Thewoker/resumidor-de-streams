# Resumidor de streams

Analiza streams completos de Kick y saca automáticamente los mejores momentos como clips horizontales y verticales (9:16 con subtítulos) listos para editar y subir a redes.

## Cómo funciona

1. Descarga el VOD de Kick (HLS directo con ffmpeg).
2. Mide el volumen por segundo y detecta picos (gritos, risas, sustos).
3. Transcribe con `faster-whisper` (`large-v3`) en GPU.
4. Una IA local (Ollama, `qwen2.5:7b`) puntúa fragmentos de la transcripción.
5. Combina nota de la IA y picos de audio, quita solapes y se queda con los mejores.
6. Corta clips con NVENC y avisa por Telegram.

## Requisitos

- Python 3.10+, ffmpeg con NVENC, GPU NVIDIA (probado para RTX 3070, 8 GB)
- Ollama accesible con `qwen2.5:7b`

```bash
pip install -r requirements.txt
```

## Uso

```bash
# Un VOD concreto (URL de Kick o archivo local)
python clip.py https://kick.com/elmemesolitario/videos/<uuid>

# Vigilar el canal y procesar cada VOD nuevo
python watch.py --backfill 3
```

Opciones: `--top 15`, `--min-score 6`, `--no-vertical`, `--cpu`, `--keep-video`.

Resultados en `output/<vod>/`: `clips/`, `report.md`, `report.json`.

## Variables de entorno

| Variable | Por defecto |
|---|---|
| `OLLAMA_URL` | `http://192.168.1.199:11434` |
| `OLLAMA_MODEL` | `qwen2.5:7b` |
| `WHISPER_MODEL` | `large-v3` |
| `KICK_CHANNEL` | `elmemesolitario` |
| `OUTPUT_DIR` | `output` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | (opcional) |
