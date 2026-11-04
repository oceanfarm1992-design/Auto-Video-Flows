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
import difflib
import json
import os
import re
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
MAX_GROUP_CHARS = 17             # 82 px bold caps: more than this would run off a 1080 px screen

ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
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
    """Groups of up to WORDS_PER_GROUP words and MAX_GROUP_CHARS characters, always ending a
    group at sentence end, so a line of big caps never runs off the screen."""
    groups, cur = [], []
    for w in words:
        chars = sum(len(x[0].strip()) + 1 for x in cur) + len(w[0].strip())
        if cur and chars > MAX_GROUP_CHARS:
            groups.append(cur)
            cur = []
        cur.append(w)
        if len(cur) >= WORDS_PER_GROUP or w[0].rstrip().endswith((".", "!", "?", "…")):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def words_to_ass(words: list[tuple[str, float, float]], hilite: str = HILITE) -> str:
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
                    parts.append(rf"{{\c{hilite}\fscx114\fscy114}}{t}{{\r}}")
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


def timeline_words(timeline_path: str) -> tuple[str, list]:
    """Offline fallback for skits: spread each line's words across that line's own
    measured span (build/skit_timeline.json), so pauses between speakers stay in sync."""
    with open(timeline_path, encoding="utf-8") as f:
        lines = json.load(f)["lines"]
    timed, cues = [], []
    for item in lines:
        words = item["line"].split()
        if not words:
            continue
        per = (item["end"] - item["start"]) / len(words)
        for k, w in enumerate(words):
            timed.append((w, item["start"] + k * per, item["start"] + (k + 1) * per))
        cues.append((item["start"], item["end"], item["line"]))
    print(f"[generate_captions] timeline: {len(timed)} words over {len(cues)} lines")
    return cues_to_srt(cues), timed


# ── Script alignment ─────────────────────────────────────────────────────────
# Technique adopted from MoneyPrinterTurbo (MIT, app/services/subtitle.py `correct`):
# speech recognition mishears words ("Teodor" -> "Theodore", "forty-one" -> "41"), but we
# know exactly what was said. Keep the recogniser's TIMING, show the SCRIPT's words.
# Here it works word by word (difflib) instead of line by line.

def _norm_tok(w: str) -> str:
    return re.sub(r"[^\w]", "", w.lower().replace("’", "'"))


def align_to_script(timed: list, script_text: str, min_ratio: float = 0.5) -> list:
    """timed = [(word, start, end)]. Returns the script's words carrying the recogniser's times.
    Falls back to `timed` unchanged when the two don't resemble each other."""
    script = script_text.split()
    if not timed or not script:
        return timed
    a = [_norm_tok(w) for w, _, _ in timed]
    b = [_norm_tok(w) for w in script]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if sm.ratio() < min_ratio:
        print(f"[generate_captions] transcript differs too much from the script "
              f"(match {sm.ratio():.2f}); keeping the transcript")
        return timed
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out += [(script[j1 + k], timed[i1 + k][1], timed[i1 + k][2]) for k in range(i2 - i1)]
        elif tag == "delete":
            continue
        else:   # replace / insert: spread the script words over the span the recogniser used
            words = script[j1:j2]
            if i2 > i1:
                t0, t1 = timed[i1][1], timed[i2 - 1][2]
            else:
                t0 = timed[i1 - 1][2] if i1 > 0 else timed[0][1]
                t1 = timed[i1][1] if i1 < len(timed) else t0 + 0.3 * len(words)
                if t1 - t0 < 0.1 * len(words) and out:
                    # no audible gap for the missing word(s): share the previous word's time
                    prev_word, t0, _ = out.pop()
                    t1 = max(t1, t0 + 0.1)
                    words = [prev_word] + words
            per = (t1 - t0) / len(words)
            out += [(w, t0 + k * per, t0 + (k + 1) * per) for k, w in enumerate(words)]
    fixed = sum(1 for tag, *_ in sm.get_opcodes() if tag != "equal")
    print(f"[generate_captions] aligned to script (match {sm.ratio():.2f}, {fixed} region(s) corrected)")
    return out


def words_file(path: str) -> tuple[bool, list]:
    """build/word_timings.json from the TTS stage: {"exact": bool, "words": [[w, start, end], ...]}."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return bool(data.get("exact")), [(w, float(a), float(b)) for w, a, b in data.get("words", [])]


def timed_to_srt(timed: list, words_per_cue: int) -> str:
    cues = []
    for i in range(0, len(timed), words_per_cue):
        chunk = timed[i:i + words_per_cue]
        cues.append((chunk[0][1], chunk[-1][2], " ".join(w for w, _, _ in chunk)))
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
    ap.add_argument("--words", default="build/word_timings.json",
                    help="Word timings written by the TTS stage; used first when they are exact (Edge TTS).")
    ap.add_argument("--timeline", default=None,
                    help="build/skit_timeline.json: per-line timing used when Whisper is unavailable.")
    ap.add_argument("--highlight", default=None,
                    help="ASS BGR colour of the active word, e.g. '&H0033E633&' (green). Default warm yellow.")
    args = ap.parse_args()

    wps = 2.6
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            wps = json.load(f).get("video", {}).get("words_per_second", wps)

    srt, timed = None, None
    script_txt = Path(args.script).read_text(encoding="utf-8") if os.path.exists(args.script) else ""

    tts_exact, tts_words = False, []
    if args.words and os.path.exists(args.words):
        try:
            tts_exact, tts_words = words_file(args.words)
        except (ValueError, KeyError, TypeError) as e:
            print(f"[generate_captions] ignoring unreadable {args.words}: {e}")
    # Only trust TTS word timings that belong to THIS voice track (an older run's file
    # may still be lying around when the voice came from another engine).
    if tts_words and os.path.exists(args.words) and os.path.exists(args.audio) \
            and os.path.getmtime(args.words) + 600 < os.path.getmtime(args.audio):
        tts_exact, tts_words = False, []

    # 1. exact word timings straight from the TTS engine (Edge): no transcription needed
    if tts_exact and tts_words:
        timed = align_to_script(tts_words, script_txt) if script_txt else tts_words
        srt = timed_to_srt(timed, args.words_per_cue)
        print(f"[generate_captions] exact TTS word timings: {len(timed)} words")

    # 2. Whisper, then snap its words back to the script
    if srt is None and not args.no_whisper and os.environ.get("OPENAI_API_KEY"):
        try:
            srt, timed = whisper_srt(args.audio, args.words_per_cue)
            if script_txt:
                timed = align_to_script(timed, script_txt)
                srt = timed_to_srt(timed, args.words_per_cue)
        except Exception as e:
            print(f"[generate_captions] Whisper failed ({e}), using estimation fallback")

    # 3. approximate TTS word timings (lines spread evenly)
    if srt is None and tts_words:
        timed = tts_words
        srt = timed_to_srt(timed, args.words_per_cue)
        print(f"[generate_captions] approximate TTS word timings: {len(timed)} words")

    if srt is None and args.timeline and os.path.exists(args.timeline):
        srt, timed = timeline_words(args.timeline)

    if srt is None:
        srt, timed = estimated_srt(script_txt, args.audio, args.words_per_cue, wps)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    Path(args.out).write_text(srt, encoding="utf-8")
    ass_path = os.path.splitext(args.out)[0] + ".ass"
    Path(ass_path).write_text(words_to_ass(timed, args.highlight or HILITE), encoding="utf-8")
    print(f"[generate_captions] wrote {args.out} and {ass_path}")


if __name__ == "__main__":
    main()
