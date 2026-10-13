#!/usr/bin/env python3
"""
Stage 4: Build burned-in captions from the voiceover audio.

Primary (when OPENAI_API_KEY is set):
  Whisper API with word-level timestamps → precise subtitle sync.

Fallback:
  Distribute words proportionally across the measured WAV duration.

Output: build/captions.srt (plain) and build/captions.ass (karaoke style, preferred by assemble_video.py)

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


# ── Karaoke-style ASS captions (the retention format) ──────────────────────────
# Big bold uppercase words, three at a time, the word being spoken highlighted and
# slightly enlarged. Thick outline so it reads over paper, stock footage or both.
CAPTION_FONT = os.environ.get("CAPTION_FONT", "DejaVu Sans")   # bold weight is requested in the style
CAPTION_SIZE = 82
CAPTION_MARGIN_V = 430          # distance of the caption baseline from the bottom edge (px at 1080x1920)
HILITE = "&H0000D2FF&"          # ASS colours are BGR: this is warm yellow
WORDS_PER_GROUP = 3

ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{CAPTION_FONT},{CAPTION_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,8,3,2,50,50,{CAPTION_MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def fmt_ass_ts(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _group_words(words: list[tuple[str, float, float]]) -> list[list[tuple[str, float, float]]]:
    """Groups of up to WORDS_PER_GROUP words, always ending a group at sentence end."""
    groups, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= WORDS_PER_GROUP or w[0].rstrip().endswith((".", "!", "?", "…")):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def words_to_ass(words: list[tuple[str, float, float]]) -> str:
    """words = (text, start, end) in seconds. One event per word: the whole group is on
    screen and the active word is highlighted and enlarged."""
    events = []
    groups = _group_words(words)
    for gi, group in enumerate(groups):
        next_group_start = groups[gi + 1][0][1] if gi + 1 < len(groups) else float("inf")
        for i, (_, start, end) in enumerate(group):
            nxt = group[i + 1][1] if i + 1 < len(group) else min(end + 0.12, next_group_start)
            parts = []
            for j, (text, _, _) in enumerate(group):
                t = text.strip().upper().replace("{", "(").replace("}", ")")
                if j == i:
                    parts.append(rf"{{\c{HILITE}\fscx114\fscy114}}{t}{{\r}}")
                else:
                    parts.append(t)
            events.append(f"Dialogue: 0,{fmt_ass_ts(start)},{fmt_ass_ts(max(nxt, start + 0.05))},Cap,,0,0,0,,{' '.join(parts)}")
    return ASS_HEADER + "\n".join(events) + "\n"


# ── Whisper path ──────────────────────────────────────────────────────────────

def whisper_srt(audio_path: str, words_per_cue: int) -> tuple[str, list]:
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
    timed = [(w.word, w.start, w.end) for w in words]
    return cues_to_srt(cues), timed


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
                  words_per_cue: int, wps_fallback: float) -> tuple[str, list]:
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
    timed = [(w, k * per_word, (k + 1) * per_word) for k, w in enumerate(words)]
    return cues_to_srt(cues), timed


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

    srt, timed = None, None

    # Try Whisper if key present and not disabled
    if not args.no_whisper and os.environ.get("OPENAI_API_KEY"):
        try:
            srt, timed = whisper_srt(args.audio, args.words_per_cue)
        except Exception as e:
            print(f"[generate_captions] Whisper failed ({e}), using estimation fallback")

    if srt is None:
        script_txt = Path(args.script).read_text(encoding="utf-8")
        srt, timed = estimated_srt(script_txt, args.audio, args.words_per_cue, wps)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    Path(args.out).write_text(srt, encoding="utf-8")
    ass_path = os.path.splitext(args.out)[0] + ".ass"
    Path(ass_path).write_text(words_to_ass(timed), encoding="utf-8")
    print(f"[generate_captions] wrote {args.out} and {ass_path}")


if __name__ == "__main__":
    main()
