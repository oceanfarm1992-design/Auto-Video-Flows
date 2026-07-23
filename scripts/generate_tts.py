#!/usr/bin/env python3
"""
Stage 3: generate a voiceover WAV from the script text using Piper TTS.

Piper (https://github.com/rhasspy/piper) is a fast, fully-offline neural TTS that
runs headless on a plain Ubuntu CI runner with no API key and no GPU. We install it
via `pip install piper-tts` (see requirements.txt) which provides the `piper` CLI.

Voice models are two files hosted on HuggingFace (rhasspy/piper-voices):
    <voice>.onnx        (the model)
    <voice>.onnx.json   (the config)
We download them once into ./voices/ and cache them.

# VERIFY: exact Piper CLI flags. As of piper-tts 1.2.x the invocation is:
#   echo "text" | piper --model path/to/voice.onnx --output_file out.wav
# Older/newer builds have occasionally used `-m/-f`. If the CLI call fails, run
#   piper --help
# in CI to confirm the current flag names.

Output: build/voice.wav

Fallback: if Piper is unavailable, pass --fallback espeak to use espeak-ng, which is
apt-installable on Ubuntu runners (`sudo apt-get install espeak-ng`). Quality is worse
but it always works, which keeps the daily pipeline from failing hard.

Usage:
    python scripts/generate_tts.py
    python scripts/generate_tts.py --voice en_US-lessac-medium
    python scripts/generate_tts.py --fallback espeak
"""
import argparse
import os
import subprocess
import sys

import requests

# HuggingFace raw file base for piper voices.
# Path layout: <lang>/<lang_region>/<name>/<quality>/<voice>.onnx[.json]
# VERIFY: the directory path for a given voice. Browse https://huggingface.co/rhasspy/piper-voices
# to confirm. Default below is a widely-used English voice.
HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICE_PATHS = {
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
    "en_US-amy-medium": "en/en_US/amy/medium/en_US-amy-medium.onnx",
    "en_US-ryan-high": "en/en_US/ryan/high/en_US-ryan-high.onnx",
}

HEADERS = {"User-Agent": "yt-shorts-generator/1.0"}


def download(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return
    print(f"[generate_tts] downloading {url}")
    with requests.get(url, headers=HEADERS, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)


def ensure_voice(voice, voices_dir):
    if voice not in VOICE_PATHS:
        raise SystemExit(f"Unknown voice '{voice}'. Known: {list(VOICE_PATHS)}")
    os.makedirs(voices_dir, exist_ok=True)
    onnx_rel = VOICE_PATHS[voice]
    onnx_path = os.path.join(voices_dir, os.path.basename(onnx_rel))
    json_path = onnx_path + ".json"
    download(f"{HF_BASE}/{onnx_rel}", onnx_path)
    download(f"{HF_BASE}/{onnx_rel}.json", json_path)
    return onnx_path


def run_piper(text, onnx_path, out_wav):
    cmd = ["piper", "--model", onnx_path, "--output_file", out_wav]
    print(f"[generate_tts] running: {' '.join(cmd)}")
    proc = subprocess.run(cmd, input=text.encode("utf-8"),
                          capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit(f"piper failed with code {proc.returncode}")


def run_espeak(text, out_wav):
    # espeak-ng outputs a WAV directly. -s words/min, -w write to file.
    cmd = ["espeak-ng", "-s", "150", "-w", out_wav, text]
    print(f"[generate_tts] fallback espeak-ng")
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit(f"espeak-ng failed with code {proc.returncode}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="build/script.txt")
    ap.add_argument("--out", default="build/voice.wav")
    ap.add_argument("--voice", default="en_US-lessac-medium")
    ap.add_argument("--voices-dir", default="voices")
    ap.add_argument("--fallback", choices=["espeak"], default=None,
                    help="Skip Piper and use espeak-ng instead.")
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as f:
        text = f.read().strip()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    if args.fallback == "espeak":
        run_espeak(text, args.out)
    else:
        onnx_path = ensure_voice(args.voice, args.voices_dir)
        run_piper(text, onnx_path, args.out)

    if not (os.path.exists(args.out) and os.path.getsize(args.out) > 0):
        raise SystemExit("TTS produced no audio.")
    print(f"[generate_tts] wrote {args.out}")


if __name__ == "__main__":
    main()
