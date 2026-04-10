# musi

> "Can AI read sheet music?" — "Hold my metronome."

**musi** snaps a photo of sheet music, feeds it to an open-source vision AI, and spits out an MP3 you can actually listen to. Yes, it's that simple. Yes, it works. No, it won't replace your piano teacher.

```
photo of sheet music → vision LLM reads the notes → synthesizer goes brrr → MP3
```

## The origin story

We pointed Claude at a photo of an Italian lullaby ("La luna cammina sull'acqua") and asked it to read the notes. It tried its best. Then we pointed Qwen3-VL at the same photo and it nailed the key signature (D minor), the descending scale, even the dynamics marking. Then we fed it Beethoven's 5th and it correctly read **G-G-G-Eb** — *ta-ta-ta-TAAAA!* — 91 notes from a blurry image. So we made it a tool.

## What it does

1. Sends your sheet music photo to a multimodal LLM (any Ollama-compatible vision model)
2. The AI reads notes, key, tempo, dynamics, lyrics — returns structured JSON
3. Synthesizes audio with additive synthesis + ADSR envelopes
4. Encodes to MP3 via ffmpeg

## Setup

```bash
pip install numpy scipy    # audio synthesis
# ffmpeg required for MP3 (falls back to WAV if missing)

cp .env.example .env       # add your Ollama API key
```

Works with [Ollama](https://ollama.com) (local or cloud), or any OpenAI-compatible vision API.

## Usage

```bash
# basic — photo in, MP3 out
python3 musi.py spartito.jpg

# choose your instrument
python3 musi.py score.png --instrument music_box

# just read the notes, no audio
python3 musi.py beethoven.jpg --dry-run

# override tempo, save note data
python3 musi.py waltz.jpg --bpm 120 --json

# custom output path
python3 musi.py photo.jpg -o my_melody.mp3
```

### Instruments

| Name | Vibe |
|------|------|
| `piano` | Classic. Default. Can't go wrong. |
| `flute` | Soft and breathy. Good for lullabies. |
| `organ` | Full and churchy. Bach would approve. |
| `music_box` | Tiny and sparkly. Instant nostalgia. |

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Vision API endpoint |
| `OLLAMA_API_KEY` | `ollama` | API key |
| `OLLAMA_VISION_MODEL` | `qwen3-vl:235b-cloud` | Vision model |

## How accurate is it?

Honestly? It depends on the photo. Clean, high-res scans work great. Blurry phone photos of your grandma's songbook? It'll give it a solid try. The AI might occasionally hallucinate a sharp or miss a rest, but the melody will be recognizable. Think of it as "AI karaoke for sheet music."

**Beethoven's 5th** — nailed the iconic motif, read 91 notes across 2 systems, got the tempo from the score marking.

**Italian lullaby** — correctly identified D minor, read the descending scale, caught the `p` dynamic.

## Tech stack

- **Vision**: Any Ollama-compatible multimodal model (tested with Qwen3-VL 235B)
- **Audio**: NumPy + SciPy (additive synthesis, no external audio libs)
- **Encoding**: ffmpeg for MP3
- **Dependencies**: Just `numpy` and `scipy`. That's it. One file. 350 lines.

## License

MIT — do whatever you want with it. Make music. Have fun.
