#!/usr/bin/env python3
"""
Stage 4: Build burned-in captions from the voiceover audio.

Primary (when OPENAI_API_KEY is set):
  Whisper API with word-level timestamps → precise subtitle sync.

Fallback:
  Distribute words proportionally across the measured WAV duration.

Output: build/captions.srt

Usage:
    python scripts/generate_captions.py
    python scripts/generate_captions.py --no-whisper    # force fallback
    python scripts/generate_captions.py --words-per-cue 4
"""
import argparse
import contextlib
import json
import os
import wave
from pathlib import Path


# ── Formatting ────────────────────────────────────────────────────────────────

def fmt_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cues_to_srt(cues: list[tuple[float, float, str]]) -> str:
    lines = []
    for idx, (start, end, text) in enumerate(cues, 1):
        lines += [str(idx), f"{fmt_ts(start)} --> {fmt_ts(end)}", text, ""]
    return "\n".join(lines)


# ── Whisper path ──────────────────────────────────────────────────────────────

def whisper_srt(audio_path: str, words_per_cue: int) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    print("[generate_captions] Whisper transcription for word-level timestamps...")
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )
    words = result.words or []
    if not words:
        raise ValueError("Whisper returned no word timestamps")

    cues = []
    i = 0
    while i < len(words):
        chunk = words[i: i + words_per_cue]
        start = chunk[0].start
        end   = chunk[-1].end
        text  = " ".join(w.word.strip() for w in chunk)
        cues.append((start, end, text))
        i += words_per_cue

    print(f"[generate_captions] Whisper: {len(words)} words → {len(cues)} cues")
    return cues_to_srt(cues)


# ── Fallback: word-count estimation ──────────────────────────────────────────

def wav_duration(path: str) -> float | None:
    try:
        with contextlib.closing(wave.open(path, "rb")) as w:
            frames = w.getnframes()
            rate   = w.getframerate()
            return frames / float(rate) if rate else None
    except (wave.Error, EOFError, FileNotFoundError):
        return None


def estimated_srt(script_txt: str, audio_path: str,
                  words_per_cue: int, wps_fallback: float) -> str:
    words   = script_txt.split()
    duration = wav_duration(audio_path)
    if duration is None:
        duration = len(words) / wps_fallback
        print(f"[generate_captions] WAV unreadable; estimated {duration:.1f}s")
    else:
        print(f"[generate_captions] WAV duration {duration:.1f}s")

    n = len(words)
    per_word = duration / n if n else 0.0
    cues = []
    i = 0
    while i < n:
        group = words[i: i + words_per_cue]
        cues.append((i * per_word, (i + len(group)) * per_word, " ".join(group)))
        i += words_per_cue
    return cues_to_srt(cues)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script",       default="build/script.txt")
    ap.add_argument("--audio",        default="build/voice.wav")
    ap.add_argument("--out",          default="build/captions.srt")
    ap.add_argument("--config",       default="config/sources.json")
    ap.add_argument("--words-per-cue",type=int, default=4)
    ap.add_argument("--no-whisper",   action="store_true",
                    help="Skip Whisper API and use word-count estimation.")
    args = ap.parse_args()

    wps = 2.6
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            wps = json.load(f).get("video", {}).get("words_per_second", wps)

    srt = None

    # Try Whisper if key present and not disabled
    if not args.no_whisper and os.environ.get("OPENAI_API_KEY"):
        try:
            srt = whisper_srt(args.audio, args.words_per_cue)
        except Exception as e:
            print(f"[generate_captions] Whisper failed ({e}), using estimation fallback")

    if srt is None:
        script_txt = Path(args.script).read_text(encoding="utf-8")
        srt = estimated_srt(script_txt, args.audio, args.words_per_cue, wps)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    Path(args.out).write_text(srt, encoding="utf-8")
    print(f"[generate_captions] wrote {args.out}")


if __name__ == "__main__":
    main()
