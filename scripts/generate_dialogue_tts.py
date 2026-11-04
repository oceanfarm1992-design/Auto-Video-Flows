#!/usr/bin/env python3
"""
Stage 2 (skit format): voice every line with its character's own voice.

Each character in config/niches/<niche>.json -> skit.cast.<id>.voice names a voice per
engine (openai / edge / kokoro / espeak). Lines are synthesised one by one, trying the engines
from config/skit.json -> tts.engines in order; the narrator tries the channel's cloned
voice (StyleTTS2, needs VOICE_REPO_PAT) first. OpenAI's gpt-4o-mini-tts also receives
the line's acting direction ("annoyed, sharp"), which is what makes skits feel acted
rather than read.

Every line is converted to 24 kHz mono and joined with a short gap. The exact start/end
of every line is written to build/skit_timeline.json; the editor cuts the picture on
those times, so each shot lasts exactly as long as its line.

Edge TTS (free Microsoft neural voices) also reports when every word is spoken; those
timings are written to build/word_timings.json so captions are exact without Whisper.

Output: build/voice.wav, build/skit_timeline.json, build/word_timings.json, build/lines/line_NN.wav

Usage:
    python scripts/generate_dialogue_tts.py --niche config/niches/men_psychology.json
    python scripts/generate_dialogue_tts.py --niche ... --engines kokoro,espeak
    python scripts/generate_dialogue_tts.py --niche ... --engines silent      # dry run, no voices
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

from skit_common import cast_of, load_configs, load_script

RATE = 24000
# Engines that failed once in this run are skipped for the remaining lines: an unreachable
# service (no network, rate limit, model that won't load) would otherwise cost its full
# retry/timeout budget on every single line.
_DEAD_ENGINES: set[str] = set()
_RECOVERABLE = {"espeak", "silent"}


def _to_pcm(src: str, dst: str) -> None:
    proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-ac", "1", "-ar", str(RATE),
                           "-sample_fmt", "s16", dst], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not convert {src}: {proc.stderr.strip()[:300]}")


def _silence(dst: str, seconds: float) -> None:
    with wave.open(dst, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"\x00\x00" * int(RATE * seconds))


def _duration(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def engines_for(char: dict, engines: list[str]) -> list[str]:
    voice = char.get("voice", {})
    chain = ["styletts2"] if voice.get("styletts2") else []
    return chain + [e for e in engines if e == "silent" or voice.get(e) or e == "espeak"]


def synth_line(text: str, char: dict, emotion: str, raw: str, engines: list[str], tts_cfg: dict
               ) -> tuple[str, list | None]:
    """Synthesize one line to `raw`; returns (engine that worked, exact word timings or None)."""
    import generate_tts as gt  # local import: pulls requests/openai only when needed
    voice = char.get("voice", {})
    spoken = gt.soften_caps_for_speech(text)
    last = None
    for eng in engines_for(char, engines):
        if eng in _DEAD_ENGINES:
            continue
        words = None
        try:
            if os.path.exists(raw):
                os.remove(raw)
            if eng == "styletts2":
                gt.run_styletts2(spoken, raw)
            elif eng == "openai":
                acting = f"Voice acting for an animated cartoon. Character: {char.get('acting', '')}. " \
                         f"Deliver this line {emotion}. Natural, conversational, not theatrical."
                gt.run_openai_tts(spoken, raw, tts_cfg.get("openai_model", "gpt-4o-mini-tts"),
                                  voice.get("openai", "onyx"), acting)
            elif eng == "edge":
                from tts_edge import synth_edge
                words = synth_edge(spoken, raw, voice["edge"], voice.get("edge_rate", "+0%"),
                                   voice.get("edge_pitch", "+0Hz"))
            elif eng == "kokoro":
                gt.run_kokoro(spoken, raw, voice.get("kokoro", "am_fenrir"), "a")
            elif eng == "espeak":
                subprocess.run(["espeak-ng", "-v", voice.get("espeak", "en-us+m3"), "-s", "150",
                                "-w", raw, spoken], check=True, capture_output=True)
            elif eng == "silent":
                _silence(raw, max(0.8, len(spoken.split()) / 2.7))
            if os.path.exists(raw) and os.path.getsize(raw) > 100:
                return eng, words
        except Exception as exc:  # noqa: BLE001 - try the next engine
            last = exc
            print(f"[dialogue_tts] {eng} failed: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
            if eng not in _RECOVERABLE:
                _DEAD_ENGINES.add(eng)
                print(f"[dialogue_tts] skipping {eng} for the rest of this run", file=sys.stderr)
    raise SystemExit(f"All TTS engines failed for line {text!r}. Last error: {last}")


def line_words(line: str, words: list | None, start: float, dur: float) -> list[list]:
    """Words of one line on the video's clock: the engine's own timings when it gave them
    (edge), otherwise the line's words spread evenly across its measured length."""
    if words:
        return [[w, round(start + a, 3), round(start + min(b, dur), 3)] for w, a, b in words]
    toks = line.split()
    per = dur / max(1, len(toks))
    return [[w, round(start + k * per, 3), round(start + (k + 1) * per, 3)] for k, w in enumerate(toks)]


def concat(parts: list[str], out: str) -> None:
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        for p in parts:
            with wave.open(p, "rb") as r:
                w.writeframes(r.readframes(r.getnframes()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", required=True)
    ap.add_argument("--skit-config", default="config/skit.json")
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--engines", default=None, help="comma list overriding config tts.engines")
    ap.add_argument("--out-dir", default="build")
    args = ap.parse_args()

    niche, skit = load_configs(args.niche, args.skit_config)
    tts_cfg = skit["tts"]
    engines = args.engines.split(",") if args.engines else tts_cfg["engines"]
    if "openai" in engines and not os.environ.get("OPENAI_API_KEY"):
        engines = [e for e in engines if e != "openai"]
    cast = cast_of(niche)
    data = load_script(args.script)

    out = Path(args.out_dir)
    lines_dir = out / "lines"
    lines_dir.mkdir(parents=True, exist_ok=True)
    gap, tail = float(tts_cfg.get("gap_s", 0.22)), float(tts_cfg.get("tail_s", 0.6))
    gap_wav, tail_wav = str(lines_dir / "gap.wav"), str(lines_dir / "tail.wav")
    _silence(gap_wav, gap)
    _silence(tail_wav, tail)

    parts, timeline, t = [], [], 0.0
    all_words: list[list] = []
    exact = True
    for i, shot in enumerate(data["shots"]):
        char = cast.get(shot["speaker"], cast.get("narrator", {}))
        raw = str(lines_dir / f"raw_{i:02d}.wav")
        pcm = str(lines_dir / f"line_{i:02d}.wav")
        eng, words = synth_line(shot["line"], char, shot.get("emotion", "naturally"), raw, engines, tts_cfg)
        _to_pcm(raw, pcm)
        d = _duration(pcm)
        all_words.extend(line_words(shot["line"], words, t, d))
        exact = exact and bool(words)
        timeline.append({"index": i, "speaker": shot["speaker"], "line": shot["line"],
                         "start": round(t, 3), "end": round(t + d, 3), "engine": eng})
        print(f"[dialogue_tts] {i:02d} {shot['speaker']:<14} {d:4.1f}s ({eng}) {shot['line']}")
        parts.append(pcm)
        t += d
        if i < len(data["shots"]) - 1:
            parts.append(gap_wav)
            t += gap
    parts.append(tail_wav)
    t += tail

    concat(parts, str(out / "voice.wav"))
    # Each shot runs from its line's start until the next line starts (the last one to the end).
    for k, item in enumerate(timeline):
        item["shot_start"] = 0.0 if k == 0 else timeline[k]["start"]
        item["shot_end"] = timeline[k + 1]["start"] if k + 1 < len(timeline) else round(t, 3)
    (out / "word_timings.json").write_text(json.dumps({"exact": exact, "words": all_words}, indent=1),
                                           encoding="utf-8")
    (out / "skit_timeline.json").write_text(json.dumps({"duration": round(t, 3), "lines": timeline}, indent=2),
                                            encoding="utf-8")
    print(f"[dialogue_tts] wrote {out / 'voice.wav'} ({t:.1f}s, {len(timeline)} lines)")


if __name__ == "__main__":
    main()
