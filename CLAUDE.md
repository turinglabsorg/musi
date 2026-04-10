# Musi — Sheet Music to MP3

Telegram bot + CLI that reads sheet music photos and generates playable MP3 audio.

## Architecture

```
User sends photo via Telegram
  ↓
bot.py (python-telegram-bot, polling mode)
  ↓ downloads photo, calls musi.py functions
musi.py (core library + CLI)
  ↓ call_vision_llm() → parse_music_data() → synthesize() → save_mp3()
  ↓ Vision LLM (Ollama-compatible API, default: qwen3-vl:235b)
  ↓ Audio synthesis (numpy/scipy additive synthesis + ADSR envelopes)
  ↓ ffmpeg for MP3 encoding
MP3 sent back to user via Telegram
```

## Files

| File | Purpose |
|------|---------|
| `musi.py` | Core: vision LLM call, JSON parsing, audio synthesis, MP3 export. Also works as standalone CLI. |
| `bot.py` | Telegram bot wrapper. Imports functions from musi.py. Handles /start, /help, /instrument, /bpm. |
| `requirements.txt` | Python deps: numpy, scipy, python-telegram-bot |
| `Dockerfile` | Production container (python:3.12-slim + ffmpeg) |
| `.env` | Config: TELEGRAM_BOT_TOKEN, OLLAMA_BASE_URL, OLLAMA_API_KEY, OLLAMA_VISION_MODEL |

## Key functions in musi.py

- `call_vision_llm(image_path, base_url, api_key, model)` → raw LLM response string
- `parse_music_data(raw_response)` → dict with title, key, tempo, notes[]
- `synthesize(music_data, bpm_override, instrument)` → numpy int16 array
- `save_mp3(audio_16bit, output_path)` → writes .mp3 (or .wav if no ffmpeg)

Errors are raised as `RuntimeError` (not sys.exit) so bot.py can catch them gracefully.

## Running

```bash
# CLI mode
python3 musi.py photo.jpg --instrument flute --bpm 90

# Telegram bot
python3 bot.py

# Docker
docker build -t musi .
docker run --env-file .env musi
```

## Instruments

piano, flute, organ, music_box — defined in INSTRUMENTS dict in musi.py.

## Conventions

- Pure Python, no frameworks beyond python-telegram-bot
- All audio synthesis is numpy/scipy (no external audio libs)
- Vision model is swappable via env var (any OpenAI-compatible API)
- Bot stores per-user preferences (instrument, bpm) in memory (context.user_data)
