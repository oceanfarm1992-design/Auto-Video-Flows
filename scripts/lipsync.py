#!/usr/bin/env python3
"""
Cartoon lip sync: when is each mouth shape on screen.

Mouth shapes are the classic 9-shape cartoon set (the one Rhubarb Lip Sync uses):
  A  closed, lips pressed (M B P)        E  rounded, slightly open (O)
  B  slightly open, teeth (most consonants)  F  puckered (U W OO)
  C  open (EH, AE)                        G  teeth on lower lip (F V)
  D  wide open (AA, AH, I)                H  tongue up (L)
  X  rest (silence)

Two sources, best first:
  1. Rhubarb Lip Sync (MIT, github.com/DanielSWolf/rhubarb-lip-sync) when its binary is
     available (env RHUBARB_BIN, or `rhubarb` on PATH). It analyses the audio itself.
  2. Built in, no dependencies: each word's letters become shapes spread over that word's
     spoken time (build/word_timings.json, exact with Edge TTS), and the mouth closes
     wherever the voice track is silent. Good enough for drawn characters at 12 fps.

    track = MouthTrack.build("build/voice.wav", words)   # words = [(word, start, end), ...]
    track.shape_at(3.2)  # -> "D"
"""
from __future__ import annotations

import bisect
import json
import os
import shutil
import subprocess
import wave

import numpy as np

SHAPES = "ABCDEFGHX"

# letter -> shape; digraphs are handled first in word_shapes()
_LETTER = {}
for _chars, _shape in (("mbp", "A"), ("fv", "G"), ("o", "E"), ("uwq", "F"), ("ai", "D"),
                       ("ey", "C"), ("l", "H"), ("cdghjknrstxz", "B")):
    for _ch in _chars:
        _LETTER[_ch] = _shape
_DIGRAPH = {"th": "H", "sh": "F", "ch": "F", "oo": "F", "ou": "E", "ee": "C", "ph": "G", "wh": "F"}


def word_shapes(word: str) -> list[str]:
    """Mouth shapes for one written word, with repeats merged and a silent final 'e' dropped."""
    w = "".join(ch for ch in word.lower() if ch.isalpha())
    if len(w) > 3 and w.endswith("e") and w[-2] not in "aeiouy":
        w = w[:-1]
    out, i = [], 0
    while i < len(w):
        pair = w[i:i + 2]
        if pair in _DIGRAPH:
            s, i = _DIGRAPH[pair], i + 2
        else:
            s, i = _LETTER.get(w[i], "B"), i + 1
        if not out or out[-1] != s:
            out.append(s)
    return out or ["B"]


def from_words(words: list, min_gap: float = 0.08) -> list[tuple[float, str]]:
    """[(word, start, end)] -> sorted cues [(time, shape)]; shape holds until the next cue."""
    cues: list[tuple[float, str]] = [(0.0, "X")]
    for k, (word, start, end) in enumerate(words):
        shapes = word_shapes(word)
        span = max(0.05, end - start)
        step = span / len(shapes)
        for j, s in enumerate(shapes):
            cues.append((start + j * step, s))
        nxt = words[k + 1][1] if k + 1 < len(words) else end + 1.0
        if nxt - end > min_gap:
            cues.append((end, "X"))
    cues.sort(key=lambda c: c[0])
    return cues


def rhubarb_bin() -> str | None:
    path = os.environ.get("RHUBARB_BIN") or shutil.which("rhubarb")
    return path if path and os.path.exists(path) else None


def from_rhubarb(wav: str, dialog_text: str | None = None) -> list[tuple[float, str]] | None:
    """Run Rhubarb on a WAV; returns cues or None when Rhubarb is unavailable/fails."""
    exe = rhubarb_bin()
    if not exe:
        return None
    cmd = [exe, "-f", "json", "--extendedShapes", "GHX", "-q", wav]
    tmp = None
    if dialog_text:
        tmp = wav + ".dialog.txt"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(dialog_text)
        cmd[1:1] = ["--dialogFile", tmp]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            print(f"[lipsync] rhubarb failed: {proc.stderr.strip()[:200]}")
            return None
        cues = [(float(c["start"]), c["value"]) for c in json.loads(proc.stdout)["mouthCues"]]
        return cues or None
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"[lipsync] rhubarb unusable: {exc}")
        return None
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def voice_envelope(wav: str, hop: float = 1 / 48) -> tuple[np.ndarray, float]:
    """RMS loudness per hop (seconds), normalised so the loud parts of speech are ~1."""
    with wave.open(wav, "rb") as w:
        rate, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[sw]
    x = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if sw == 1:
        x -= 128.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    size = max(1, int(rate * hop))
    frames = len(x) // size
    if frames == 0:
        return np.zeros(1, np.float32), hop
    rms = np.sqrt((x[: frames * size].reshape(frames, size) ** 2).mean(axis=1))
    ref = np.percentile(rms[rms > 0], 90) if np.any(rms > 0) else 1.0
    return (rms / max(ref, 1e-6)).astype(np.float32), hop


class MouthTrack:
    def __init__(self, cues: list[tuple[float, str]], env: np.ndarray | None = None, hop: float = 1 / 48,
                 source: str = "text"):
        self.times = [t for t, _ in cues]
        self.shapes = [s for _, s in cues]
        self.env, self.hop, self.source = env, hop, source

    @classmethod
    def build(cls, wav: str | None, words: list, dialog_text: str | None = None) -> "MouthTrack":
        env, hop = (voice_envelope(wav) if wav and os.path.exists(wav) else (None, 1 / 48))
        cues = from_rhubarb(wav, dialog_text) if wav and os.path.exists(wav) else None
        if cues:
            return cls(cues, None, hop, "rhubarb")      # Rhubarb already handles silence
        return cls(from_words(words), env, hop, "text")

    def loudness(self, t: float) -> float:
        if self.env is None:
            return 1.0
        i = int(t / self.hop)
        return float(self.env[i]) if 0 <= i < len(self.env) else 0.0

    def shape_at(self, t: float) -> str:
        i = bisect.bisect_right(self.times, t) - 1
        s = self.shapes[i] if i >= 0 else "X"
        if self.env is not None and s != "X" and self.loudness(t) < 0.06:
            return "X"   # the voice is silent here: close the mouth whatever the text says
        return s

    def talking(self, t: float) -> bool:
        return self.shape_at(t) not in ("X", "A")
