# Resumidor de streams

Analiza streams completos de Kick y saca automáticamente los mejores momentos como clips horizontales y verticales (9:16 con subtítulos) listos para editar y subir a redes.

## Cómo funciona

1. Descarga el VOD de Kick (HLS directo con ffmpeg).
2. Mide el volumen por segundo y detecta picos (gritos, risas, sustos).
3. Transcribe con `faster-whisper` (`large-v3`) en GPU.
4. Una IA local (Ollama, `qwen2.5:7b`) puntúa fragmentos de la transcripción.
5. Combina nota de la IA y picos de audio, quita solapes y se queda con los mejores.
6. La IA propone además un montaje: qué trozos se quedan y cuáles van en cámara rápida.
7. Corta y monta los clips con NVENC y avisa por Telegram.

## Edición

Cada momento guarda un plan de edición en `moments.json`:

```json
"edit": {
  "cuts": [{"start": 601, "end": 612.5, "speed": 1}, {"start": 620, "end": 630, "speed": 2}],
  "subs": [{"start": 602, "end": 604, "text": "lo que realmente dije"}]
}
```

- `cuts`: trozos del stream que se quedan, en orden, cada uno con su velocidad (herramientas de **corte** y **cámara rápida**). Vacío = un solo trozo a velocidad normal.
- `subs`: subtítulos corregidos a mano. Vacío = se usan las palabras de Whisper.

Lo genera la IA al analizar el stream y se puede retocar en la interfaz con **✂ Ajustar corte**.

**Límite de duración:** ningún clip pasa de `MAX_CLIP_SECONDS` (60 s por defecto). Si el montaje de la IA se pasa,
el programa lo rehace solo: conserva los picos de audio con algo de aire, acelera los huecos cortos y tira los largos.
Un montaje hecho a mano (`manual_edit`) manda sobre el límite.

## Requisitos

- Python 3.10+, ffmpeg con NVENC, GPU NVIDIA (probado para RTX 3070, 8 GB)
- Ollama accesible con `qwen2.5:7b`

```bash
pip install -r requirements.txt
```

## Uso

Interfaz web (vigila el canal, procesa VODs nuevos y permite revisar, ajustar y aprobar clips):

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

Por línea de comandos:

```bash
python clip.py https://kick.com/elmemesolitario/videos/<uuid>
```

Resultados en `$OUTPUT_DIR/<vod>/`: `clips/`, `moments.json`, `transcript.json`, `timeline.json`.

## Docker / Coolify

Build pack Dockerfile, puerto 8000, volumen persistente en `/data`, y `--gpus all` en Custom Docker Options (requiere nvidia-container-toolkit en el host).

## Variables de entorno

| Variable | Por defecto |
|---|---|
| `OLLAMA_URL` | `http://192.168.1.199:11434` |
| `OLLAMA_MODEL` | `qwen2.5:7b` |
| `WHISPER_MODEL` | `large-v3` |
| `KICK_CHANNEL` | `elmemesolitario` |
| `OUTPUT_DIR` | `output` (`/data/output` en Docker) |
| `TOP_CLIPS` | `15` |
| `MIN_SCORE` | `6` |
| `MAX_CLIP_SECONDS` | `60` |
| `AUTO_PROCESS` | `1` |
| `CHECK_INTERVAL` | `600` |
| `SKIP_TITLES` | (vacío, p. ej. `meteoro`) |
| `APP_PASSWORD` | (vacío = sin contraseña) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | (opcional) |
