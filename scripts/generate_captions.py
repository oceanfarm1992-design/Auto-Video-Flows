#!/usr/bin/env python3
"""
Stage 4: build burned-in captions from the KNOWN script text.

Because we authored the narration, we don't need speech-to-text. We take the total
voiceover duration (read from the WAV header) and distribute the words across it
proportionally, then group them into short caption cues (a few words each) for a
punchy, readable, "word-by-word-ish" look.

Output: build/captions.srt

Duration source:
  - Preferred: read the exact length of build/voice.wav (stdlib `wave`).
  - Fallback: if the audio isn't a readable PCM WAV, estimate from words_per_second
    in config/sources.json.

Usage:
    python scripts/generate_captions.py
    python scripts/generate_captions.py --audio build/voice.wav --words-per-cue 3
"""
import argparse
import contextlib
import json
import os
import wave


def wav_duration_seconds(path):
    """Return duration in seconds, or None if not a readable PCM WAV."""
    try:
        with contextlib.closing(wave.open(path, "rb")) as w:
            frames = w.getnframes()
            rate = w.getframerate()
            if rate:
                return frames / float(rate)
    except (wave.Error, EOFError, FileNotFoundError):
        return None
    return None


def fmt_ts(seconds):
    """seconds -> SRT timestamp HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600 * 1000)
    m, ms = divmod(ms, 60 * 1000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(words, total_seconds, words_per_cue):
    """Distribute words evenly across total_seconds, grouped into cues."""
    n = len(words)
    per_word = total_seconds / n if n else 0.0
    cues = []
    i = 0
    while i < n:
        group = words[i:i + words_per_cue]
        start = i * per_word
        end = (i + len(group)) * per_word
        cues.append((start, end, " ".join(group)))
        i += words_per_cue

    lines = []
    for idx, (start, end, text) in enumerate(cues, start=1):
        lines.append(str(idx))
        lines.append(f"{fmt_ts(start)} --> {fmt_ts(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="build/script.txt")
    ap.add_argument("--audio", default="build/voice.wav")
    ap.add_argument("--out", default="build/captions.srt")
    ap.add_argument("--config", default="config/sources.json")
    ap.add_argument("--words-per-cue", type=int, default=3)
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as f:
        words = f.read().split()

    duration = wav_duration_seconds(args.audio)
    if duration is None:
        with open(args.config, encoding="utf-8") as f:
            wps = json.load(f)["video"]["words_per_second"]
        duration = len(words) / wps
        print(f"[generate_captions] WAV unreadable; estimated {duration:.1f}s "
              f"from {wps} words/sec")
    else:
        print(f"[generate_captions] voiceover duration {duration:.1f}s")

    srt = build_srt(words, duration, args.words_per_cue)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(srt)
    print(f"[generate_captions] wrote {args.out} ({len(words)} words)")


if __name__ == "__main__":
    main()
