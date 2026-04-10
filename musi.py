#!/usr/bin/env python3
"""
musi — Sheet music image to MP3 melody generator.

Uses a local/cloud multimodal LLM (Ollama-compatible) to read notes
from a photo of sheet music, then synthesizes an MP3 you can listen to.

Usage:
    python3 musi.py <image_path> [--output melody.mp3]
    python3 musi.py photo.jpg --model qwen3-vl:235b-cloud
    python3 musi.py spartito.png --bpm 90 --instrument piano
"""

import argparse
import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.request
import urllib.error

# Load .env from script directory (no external deps)
_env_path = pathlib.Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import numpy as np
from scipy.io import wavfile

SAMPLE_RATE = 44100

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "ollama")
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "qwen3-vl:235b-cloud")

NOTE_FREQS = {}
for _oct in range(1, 8):
    _base = [
        ("C", -9), ("C#", -8), ("Db", -8),
        ("D", -7), ("D#", -6), ("Eb", -6),
        ("E", -5), ("Fb", -5),
        ("F", -4), ("F#", -3), ("Gb", -3),
        ("G", -2), ("G#", -1), ("Ab", -1),
        ("A", 0), ("A#", 1), ("Bb", 1),
        ("B", 2), ("Cb", 2),
    ]
    for name, semitone in _base:
        midi_offset = semitone + (_oct - 4) * 12
        NOTE_FREQS[f"{name}{_oct}"] = 440.0 * (2 ** (midi_offset / 12))

REST_TOKEN = "REST"

INSTRUMENTS = {
    "piano": {"harmonics": [1.0, 0.3, 0.1, 0.05], "attack": 0.02, "decay": 0.08, "sustain": 0.65, "release": 0.15},
    "flute": {"harmonics": [1.0, 0.1, 0.02], "attack": 0.08, "decay": 0.05, "sustain": 0.8, "release": 0.1},
    "organ": {"harmonics": [1.0, 0.5, 0.25, 0.12, 0.06], "attack": 0.01, "decay": 0.02, "sustain": 0.9, "release": 0.05},
    "music_box": {"harmonics": [1.0, 0.6, 0.3, 0.15], "attack": 0.005, "decay": 0.3, "sustain": 0.2, "release": 0.4},
}

VISION_PROMPT = """You are a music notation reader. Analyze this image of sheet music and extract the musical information.

Return a JSON object with this exact structure (no markdown fences, just raw JSON):
{
  "title": "title or description of the piece",
  "key": "C major",
  "time_signature": "4/4",
  "tempo_bpm": 80,
  "dynamics": "p",
  "lyrics": "any lyrics text you see",
  "notes": [
    {"pitch": "C4", "duration": 1.0, "lyric": "La"},
    {"pitch": "D4", "duration": 0.5, "lyric": "lu-"},
    {"pitch": "REST", "duration": 0.25, "lyric": ""}
  ]
}

Rules for the notes array:
- pitch: note name + octave (e.g., "C4", "F#5", "Bb3"). Middle C is C4. Use "REST" for rests.
- duration: in beats. Whole note = 4.0, half = 2.0, quarter = 1.0, eighth = 0.5, sixteenth = 0.25. Add dot = 1.5x.
- Read ALL notes you can see, in order from left to right, top staff first if multiple staves.
- If the image is rotated, account for that.
- Be as accurate as possible with note positions on the staff lines and spaces.
- Treble clef lines bottom to top: E4, G4, B4, D5, F5. Spaces: F4, A4, C5, E5.
- Bass clef lines bottom to top: G2, B2, D3, F3, A3. Spaces: A2, C3, E3, G3.
- Look for sharps, flats, key signatures, accidentals.
- IMPORTANT: Use ASCII only for accidentals: "b" for flat, "#" for sharp. Never use unicode symbols like ♭ or ♯.
- Look for time signature and tempo markings.
- Limit output to the notes actually visible in the image. Do not repeat or loop patterns.

If you cannot read the notes clearly, make your best educated guess based on what you can see."""


def call_vision_llm(image_path, base_url, api_key, model):
    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}
    mime_type = mime_map.get(ext, "image/jpeg")

    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()

    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                {"type": "text", "text": VISION_PROMPT},
            ],
        }],
        "temperature": 0.3,
    }

    data = json.dumps(payload).encode()
    url = f"{base_url}/chat/completions"

    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })

    print(f"[musi] calling {model} via {base_url}...")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"[musi] API error {e.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"[musi] connection error: {e.reason}", file=sys.stderr)
        print("[musi] is Ollama running? try: ollama serve", file=sys.stderr)
        sys.exit(1)

    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    return content


def normalize_music_unicode(text):
    text = text.replace("\u266d", "b")   # ♭ → b
    text = text.replace("\u266f", "#")   # ♯ → #
    text = text.replace("\u266e", "")    # ♮ → remove
    text = text.replace("\U0001D12B", "bb")  # 𝄫 double flat
    text = text.replace("\U0001D12A", "##")  # 𝄪 double sharp
    return text


def extract_json(text):
    text = normalize_music_unicode(text)
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        text = fenced.group(1)
    match = re.search(r"\{[\s\S]*\}", text)
    return match.group(0) if match else text


def repair_truncated_json(text):
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    # Find the last complete note object and close the array/object
    last_brace = text.rfind("}")
    if last_brace == -1:
        return text
    truncated = text[:last_brace + 1]
    # Close open brackets
    open_brackets = truncated.count("[") - truncated.count("]")
    open_braces = truncated.count("{") - truncated.count("}")
    truncated += "]" * open_brackets + "}" * open_braces
    return truncated


def parse_music_data(raw_response):
    cleaned = extract_json(raw_response)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        cleaned = repair_truncated_json(cleaned)
        try:
            data = json.loads(cleaned)
            print("[musi] warning: repaired truncated JSON from LLM", file=sys.stderr)
        except json.JSONDecodeError as e:
            print(f"[musi] failed to parse LLM response: {e}", file=sys.stderr)
            print(f"[musi] raw response:\n{raw_response}", file=sys.stderr)
            sys.exit(1)

    if "notes" not in data or not data["notes"]:
        print("[musi] no notes found in the response", file=sys.stderr)
        print(f"[musi] raw response:\n{raw_response}", file=sys.stderr)
        sys.exit(1)

    return data


def generate_tone(freq, duration, instrument="piano", volume=0.4):
    params = INSTRUMENTS.get(instrument, INSTRUMENTS["piano"])
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)

    wave = np.zeros_like(t)
    for i, amp in enumerate(params["harmonics"]):
        wave += np.sin(2 * np.pi * freq * (i + 1) * t) * amp

    attack = int(params["attack"] * SAMPLE_RATE)
    decay = int(params["decay"] * SAMPLE_RATE)
    release = int(params["release"] * SAMPLE_RATE)
    sustain_level = params["sustain"]

    envelope = np.ones(len(t)) * sustain_level
    if attack > 0 and attack < len(t):
        envelope[:attack] = np.linspace(0, 1, attack)
    if decay > 0 and attack + decay < len(t):
        envelope[attack:attack + decay] = np.linspace(1, sustain_level, decay)
    if release > 0 and release < len(t):
        envelope[-release:] = np.linspace(envelope[-release] if release < len(envelope) else sustain_level, 0, release)

    wave *= envelope * volume
    return wave


def synthesize(music_data, bpm_override=None, instrument="piano"):
    notes = music_data["notes"]
    bpm = bpm_override or music_data.get("tempo_bpm", 80)
    beat_duration = 60.0 / bpm

    dynamics_map = {"ppp": 0.15, "pp": 0.2, "p": 0.3, "mp": 0.4, "mf": 0.5, "f": 0.6, "ff": 0.7, "fff": 0.8}
    dyn = music_data.get("dynamics", "mf")
    volume = dynamics_map.get(dyn, 0.45)

    samples = np.array([], dtype=np.float64)

    for note in notes:
        pitch = note.get("pitch", "C4")
        dur_beats = float(note.get("duration", 1.0))
        duration = dur_beats * beat_duration

        if pitch == REST_TOKEN or pitch.upper() == "REST":
            silence = np.zeros(int(SAMPLE_RATE * duration))
            samples = np.concatenate([samples, silence])
        else:
            freq = NOTE_FREQS.get(pitch)
            if freq is None:
                print(f"[musi] warning: unknown note '{pitch}', skipping", file=sys.stderr)
                continue
            tone = generate_tone(freq, duration, instrument=instrument, volume=volume)
            gap = np.zeros(int(0.02 * SAMPLE_RATE))
            samples = np.concatenate([samples, tone, gap])

    if len(samples) == 0:
        print("[musi] no audio generated", file=sys.stderr)
        sys.exit(1)

    # Simple reverb
    delay = int(0.12 * SAMPLE_RATE)
    reverb = np.zeros(len(samples) + delay)
    reverb[:len(samples)] += samples
    reverb[delay:delay + len(samples)] += samples * 0.15
    samples = reverb

    # Fade in/out
    fade = int(0.2 * SAMPLE_RATE)
    if fade < len(samples):
        samples[:fade] *= np.linspace(0, 1, fade)
        samples[-fade:] *= np.linspace(1, 0, fade)

    # Normalize
    peak = np.max(np.abs(samples))
    if peak > 0:
        samples = samples / peak * 0.85

    return (samples * 32767).astype(np.int16)


def save_mp3(audio_16bit, output_path):
    wav_path = output_path.rsplit(".", 1)[0] + ".wav"
    wavfile.write(wav_path, SAMPLE_RATE, audio_16bit)

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-qscale:a", "2", output_path],
            capture_output=True, check=True,
        )
        os.remove(wav_path)
        print(f"[musi] saved: {output_path}")
    except FileNotFoundError:
        print(f"[musi] ffmpeg not found, saved WAV instead: {wav_path}")
    except subprocess.CalledProcessError as e:
        print(f"[musi] ffmpeg error: {e.stderr.decode()}", file=sys.stderr)
        print(f"[musi] saved WAV instead: {wav_path}")


def main():
    parser = argparse.ArgumentParser(
        description="musi — turn sheet music photos into MP3 melodies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python3 musi.py spartito.jpg\n"
               "  python3 musi.py foto.png --output melodia.mp3 --bpm 90\n"
               "  python3 musi.py score.jpg --model gemma3:27b --instrument music_box\n"
               "  OLLAMA_BASE_URL=https://ollama.com/v1 python3 musi.py score.jpg\n",
    )
    parser.add_argument("image", help="path to sheet music image")
    parser.add_argument("--output", "-o", default=None, help="output MP3 path (default: <image_name>.mp3)")
    parser.add_argument("--model", "-m", default=None, help=f"vision model (default: {OLLAMA_VISION_MODEL})")
    parser.add_argument("--base-url", default=None, help=f"Ollama API base URL (default: {OLLAMA_BASE_URL})")
    parser.add_argument("--bpm", type=int, default=None, help="override tempo in BPM")
    parser.add_argument("--instrument", "-i", choices=list(INSTRUMENTS.keys()), default="piano", help="instrument sound")
    parser.add_argument("--json", action="store_true", help="also save the parsed music data as JSON")
    parser.add_argument("--dry-run", action="store_true", help="only analyze the image, don't generate audio")
    args = parser.parse_args()

    if not os.path.isfile(args.image):
        print(f"[musi] file not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    base_url = args.base_url or OLLAMA_BASE_URL
    api_key = OLLAMA_API_KEY
    model = args.model or OLLAMA_VISION_MODEL

    # Step 1: Vision LLM reads the sheet music
    print(f"[musi] analyzing: {args.image}")
    raw = call_vision_llm(args.image, base_url, api_key, model)
    music_data = parse_music_data(raw)

    title = music_data.get("title", "unknown")
    key = music_data.get("key", "?")
    tempo = music_data.get("tempo_bpm", "?")
    lyrics = music_data.get("lyrics", "")
    n_notes = len(music_data.get("notes", []))

    print(f"[musi] title: {title}")
    print(f"[musi] key: {key}, tempo: {tempo} bpm, notes: {n_notes}")
    if lyrics:
        print(f"[musi] lyrics: {lyrics}")
    print(f"[musi] notes: {', '.join(n.get('pitch', '?') for n in music_data['notes'])}")

    if args.dry_run:
        print(json.dumps(music_data, indent=2, ensure_ascii=False))
        return

    # Step 2: Synthesize audio
    output = args.output or os.path.splitext(args.image)[0] + ".mp3"
    print(f"[musi] synthesizing with {args.instrument} @ {args.bpm or tempo} bpm...")
    audio = synthesize(music_data, bpm_override=args.bpm, instrument=args.instrument)

    # Step 3: Save
    save_mp3(audio, output)

    if args.json:
        json_path = output.rsplit(".", 1)[0] + ".json"
        with open(json_path, "w") as f:
            json.dump(music_data, f, indent=2, ensure_ascii=False)
        print(f"[musi] json: {json_path}")

    print("[musi] done!")


if __name__ == "__main__":
    main()
