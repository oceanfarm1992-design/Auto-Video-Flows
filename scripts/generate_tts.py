#!/usr/bin/env python3
"""
Stage 3: generate a voiceover WAV from the script text.

Priority order:
  1. OpenAI TTS (tts-1-hd) — best quality; requires OPENAI_API_KEY
  2. Piper TTS (offline, no key needed) — good quality fallback
  3. espeak-ng — last resort, always available on Ubuntu runners

Output: build/voice.wav

Usage:
    python scripts/generate_tts.py
    python scripts/generate_tts.py --voice shimmer          # OpenAI voice
    python scripts/generate_tts.py --fallback piper
    python scripts/generate_tts.py --fallback espeak
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

# ── OpenAI TTS ──────────────────────────────────────────────────────────────

OPENAI_VOICES = ["onyx", "nova", "echo", "alloy", "fable", "shimmer"]
# onyx: deep, authoritative — great for motivational content
# nova: warm female voice
# echo: balanced male voice

def run_openai_tts(text: str, out_wav: str, voice: str = "onyx", model: str = "tts-1-hd"):
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    client = OpenAI(api_key=api_key)
    print(f"[generate_tts] OpenAI TTS: model={model} voice={voice}")

    # OpenAI returns mp3 by default; we convert to WAV via ffmpeg
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        with client.audio.speech.with_streaming_response.create(
            model=model,
            voice=voice,
            input=text,
            speed=1.0,  # natural speed — slowdown makes voice sound flat
        ) as resp:
            resp.stream_to_file(tmp_path)

        # convert mp3 → wav (16kHz mono, matches Piper output format)
        cmd = ["ffmpeg", "-y", "-i", tmp_path,
               "-ar", "22050", "-ac", "1", out_wav]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg mp3→wav failed: {proc.stderr.decode()}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

# ── Piper TTS ───────────────────────────────────────────────────────────────

HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICE_PATHS = {
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
    "en_US-amy-medium":    "en/en_US/amy/medium/en_US-amy-medium.onnx",
    "en_US-ryan-high":     "en/en_US/ryan/high/en_US-ryan-high.onnx",
}
HEADERS = {"User-Agent": "yt-shorts-generator/1.0"}


def _download(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return
    print(f"[generate_tts] downloading {url}")
    with requests.get(url, headers=HEADERS, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)


def _ensure_piper_voice(voice, voices_dir):
    if voice not in VOICE_PATHS:
        raise RuntimeError(f"Unknown Piper voice '{voice}'. Known: {list(VOICE_PATHS)}")
    os.makedirs(voices_dir, exist_ok=True)
    onnx_rel = VOICE_PATHS[voice]
    onnx_path = os.path.join(voices_dir, os.path.basename(onnx_rel))
    _download(f"{HF_BASE}/{onnx_rel}", onnx_path)
    _download(f"{HF_BASE}/{onnx_rel}.json", onnx_path + ".json")
    return onnx_path


def run_piper(text: str, out_wav: str, voice: str = "en_US-amy-medium",
              voices_dir: str = "voices"):
    onnx_path = _ensure_piper_voice(voice, voices_dir)
    cmd = ["piper", "--model", onnx_path, "--output_file", out_wav,
           "--length_scale", "1.0", "--sentence_silence", "0.3"]
    print(f"[generate_tts] Piper TTS: {' '.join(cmd)}")
    proc = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"piper failed: {proc.stderr.decode('utf-8', 'replace')}")

# ── espeak fallback ──────────────────────────────────────────────────────────

def run_espeak(text: str, out_wav: str):
    cmd = ["espeak-ng", "-v", "en-us+m3", "-s", "135", "-p", "45", "-g", "6",
           "-w", out_wav, text]
    print("[generate_tts] espeak-ng fallback")
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit(f"espeak-ng failed with code {proc.returncode}")

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="build/script.txt")
    ap.add_argument("--out", default="build/voice.wav")
    ap.add_argument("--voice", default="echo",
                    help="OpenAI voice name (echo/onyx/nova/fable/alloy) or Piper voice ID")
    ap.add_argument("--voices-dir", default="voices")
    ap.add_argument("--fallback", choices=["piper", "espeak"], default=None,
                    help="Skip OpenAI and use piper or espeak instead.")
    ap.add_argument("--openai-model", default="tts-1")  # tts-1 = $15/1M chars vs tts-1-hd $30/1M
    args = ap.parse_args()

    text = Path(args.script).read_text(encoding="utf-8").strip()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    if args.fallback == "espeak":
        run_espeak(text, args.out)
    elif args.fallback == "piper":
        piper_voice = args.voice if args.voice in VOICE_PATHS else "en_US-amy-medium"
        run_piper(text, args.out, piper_voice, args.voices_dir)
    else:
        # Try OpenAI first, fall back to Piper, then espeak
        try:
            oai_voice = args.voice if args.voice in OPENAI_VOICES else "onyx"
            run_openai_tts(text, args.out, voice=oai_voice, model=args.openai_model)
        except Exception as e:
            print(f"[generate_tts] OpenAI TTS failed ({e}), trying Piper...")
            try:
                run_piper(text, args.out, "en_US-amy-medium", args.voices_dir)
            except Exception as e2:
                print(f"[generate_tts] Piper failed ({e2}), using espeak...")
                run_espeak(text, args.out)

    if not (os.path.exists(args.out) and os.path.getsize(args.out) > 0):
        raise SystemExit("TTS produced no audio.")
    print(f"[generate_tts] wrote {args.out}")


if __name__ == "__main__":
    main()
