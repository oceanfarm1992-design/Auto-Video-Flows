#!/usr/bin/env python3
"""
Sad solo-piano music generator: composes AND synthesizes a new piece for every video.

No samples, no API, no copyright: every note is synthesized with numpy and the piece is
composed from music-theory rules, so each video gets its own original track in the same mood.

Sound (a simple physical-ish piano):
  - 9 partials per note with string inharmonicity (upper partials slightly sharp)
  - two strings per note detuned by ~1 cent (the slow "beating" of a real piano)
  - soft hammer thump at the attack, two-stage decay (fast then long), brighter when louder
  - sustain pedal: notes ring until the chord changes, then damp over ~0.35 s
  - warm low-pass, stereo placement by pitch (low left, high right), room reverb

Music:
  - slow tempo (52-62 BPM), minor key, one chord per bar from sad progressions
    (i-VI-III-VII, i-VI-iv-V, i-v-VI-iv, i-III-VII-VI)
  - left hand: low root + broken-chord arpeggio in eighths
  - right hand: a 2-bar motif built on chord tones, repeated and varied, with rests
  - form: intro (left hand only) -> theme -> theme an octave higher -> fading outro
    ending on the tonic with a long ring

Output: build/music.mp3 (what assemble_video.py / assemble_skit.py mix under the voice)

Usage:
    python scripts/generate_piano.py                     # 95 s, seed from today's date
    python scripts/generate_piano.py --duration 70 --seed 7 --out build/music.mp3
"""
from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
import wave

import numpy as np

SR = 44100
A4 = 440.0

# Sad progressions as scale degrees in natural minor (0 = i). Each chord = (degree, quality)
PROGRESSIONS = [
    [(0, "m"), (5, "M"), (2, "M"), (6, "M")],      # i  VI III VII   (Am F C G)
    [(0, "m"), (5, "M"), (3, "m"), (4, "M")],      # i  VI iv  V     (Am F Dm E)
    [(0, "m"), (4, "m"), (5, "M"), (3, "m")],      # i  v  VI  iv    (Am Em F Dm)
    [(0, "m"), (2, "M"), (6, "M"), (5, "M")],      # i  III VII VI   (Am C G F)
]
MINOR_SCALE = [0, 2, 3, 5, 7, 8, 10]               # natural minor, semitones from the tonic
KEYS = {"A": 57, "D": 50, "E": 52, "C": 48, "B": 59, "F#": 54}   # MIDI of the tonic (octave 3)


def midi_hz(m: float) -> float:
    return A4 * 2 ** ((m - 69) / 12)


# ── synthesis ────────────────────────────────────────────────────────────────

def piano_note(midi: int, vel: float, ring: float, rng: np.random.Generator) -> np.ndarray:
    """Mono samples for one note held (pedal down) for `ring` seconds, then damped."""
    f0 = midi_hz(midi)
    damp = 0.35
    n = int(SR * (ring + damp))
    t = np.arange(n) / SR
    out = np.zeros(n)
    B = 0.00012 * (f0 / 261.6) ** 0.5                       # inharmonicity
    base_decay = 7.0 * (261.6 / f0) ** 0.55                   # long ring for low notes
    bright = 0.55 + 0.6 * vel
    for k in range(1, 10):
        fk = k * f0 * np.sqrt(1 + B * k * k)
        if fk > SR / 2 - 500:
            break
        amp = (1.0 / k ** 1.25) * (bright ** (k - 1)) * (1.0 if k == 1 else 0.85)
        tau_slow = base_decay / (1 + 0.45 * (k - 1))
        tau_fast = 0.35 / (1 + 0.3 * (k - 1))
        env = 0.55 * np.exp(-t / tau_fast) + 0.45 * np.exp(-t / tau_slow)
        cents = rng.uniform(0.6, 1.4)
        ph = rng.uniform(0, 2 * np.pi)
        tone = np.sin(2 * np.pi * fk * t + ph) + 0.9 * np.sin(2 * np.pi * fk * 2 ** (cents / 1200) * t + ph * 1.3)
        out += amp * env * tone
    # hammer: a short, soft, low-passed noise thump
    hn = int(0.025 * SR)
    noise = rng.standard_normal(hn) * np.exp(-np.arange(hn) / (0.006 * SR))
    noise = np.convolve(noise, np.ones(12) / 12, mode="same")
    out[:hn] += 0.03 * vel * noise
    # attack (3 ms) and damper release after `ring`
    a = int(0.003 * SR)
    out[:a] *= np.linspace(0, 1, a)
    r0 = int(ring * SR)
    if r0 < n:
        out[r0:] *= np.exp(-np.arange(n - r0) / (0.09 * SR))
    return out * vel


def lowpass_fast(x: np.ndarray, cutoff: float) -> np.ndarray:
    """One-pole low-pass via FFT filtering (much faster than a python loop)."""
    n = len(x)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    H = 1 / np.sqrt(1 + (f / cutoff) ** 2)
    return np.fft.irfft(X * H, n)


def reverb(stereo: np.ndarray, rng: np.random.Generator, seconds: float = 2.8, wet: float = 0.32) -> np.ndarray:
    """Convolution with a synthetic hall impulse (decaying, darkening noise), per channel."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.empty((stereo.shape[0] + n - 1, 2))
    for ch in range(2):
        ir = rng.standard_normal(n) * np.exp(-t * 6.9 / seconds)
        ir = lowpass_fast(ir, 4500)
        ir[: int(0.012 * SR)] = 0                                   # pre-delay
        ir /= np.sqrt((ir ** 2).sum()) + 1e-9
        size = 1 << int(np.ceil(np.log2(stereo.shape[0] + n)))
        wetsig = np.fft.irfft(np.fft.rfft(stereo[:, ch], size) * np.fft.rfft(ir, size), size)[: out.shape[0]]
        dry = np.pad(stereo[:, ch], (0, n - 1))
        out[:, ch] = (1 - wet) * dry + wet * wetsig * 2.2
    return out


# ── composition ──────────────────────────────────────────────────────────────

def chord_notes(tonic: int, degree: int, quality: str) -> list[int]:
    """Root-position triad (MIDI) for a scale degree; 'M' raises the third where the scale is minor."""
    root = tonic + MINOR_SCALE[degree]
    third = root + (4 if quality == "M" else 3)
    if degree == 4 and quality == "M":          # harmonic-minor V: major third (leading tone)
        third = root + 4
    return [root, third, root + 7]


def roman(degree: int, quality: str) -> str:
    r = ["i", "ii", "iii", "iv", "v", "vi", "vii"][degree]
    return r.upper() if quality == "M" else r


def compose(seed: int, duration: float) -> tuple[list[tuple[float, int, float, float]], dict]:
    """Return notes [(start_s, midi, velocity, ring_s)] and info about the piece."""
    rng = np.random.default_rng(seed)
    key = list(KEYS)[seed % len(KEYS)]
    tonic = KEYS[key]
    prog = PROGRESSIONS[(seed // len(KEYS)) % len(PROGRESSIONS)]
    bpm = float(rng.uniform(52, 62))
    beat = 60.0 / bpm
    bar = 4 * beat
    n_bars = max(4, int(np.ceil((duration - 4.0) / bar)))
    notes: list[tuple[float, int, float, float]] = []

    # a 2-bar right-hand motif: (beat position, scale-step offset from the chord's top note, length in beats)
    motif = []
    pos = 0.0
    while pos < 8:
        step = int(rng.choice([0, 0, 1, -1, 2, -2, 3]))
        length = float(rng.choice([1, 1.5, 2, 2, 3]))
        if rng.random() < 0.18 and pos > 0:
            pos += float(rng.choice([1, 2]))                         # a rest
            continue
        motif.append((pos, step, min(length, 8 - pos)))
        pos += length

    def scale_note(near: int, steps: int) -> int:
        """Move `steps` scale degrees from the scale note nearest to `near`."""
        degs = [tonic + 12 * o + s for o in range(-2, 4) for s in MINOR_SCALE]
        degs.sort()
        idx = int(np.argmin([abs(d - near) for d in degs]))
        return degs[max(0, min(len(degs) - 1, idx + steps))]

    intro_bars, outro_bars = 2, 3
    for b in range(n_bars):
        t0 = b * bar
        deg, q = prog[b % len(prog)]
        triad = chord_notes(tonic, deg, q)
        last = b == n_bars - 1
        if last:                                                    # end on the tonic
            triad = chord_notes(tonic, 0, "m")
        fade = 1.0 if b < n_bars - outro_bars else 0.75 - 0.15 * (b - (n_bars - outro_bars))
        # left hand: low root, then broken chord in eighths (root 5th octave 3rd 5th octave ...)
        root = triad[0] - 12
        pattern = [root, root + 7, root + 12, triad[1] + 12, root + 19, root + 12, root + 7, triad[1]]
        for i, m in enumerate(pattern if not last else pattern[:4]):
            st = t0 + i * beat / 2 + rng.normal(0, 0.008)
            vel = (0.42 if i == 0 else 0.26 + 0.05 * rng.random()) * fade
            ring = bar - i * beat / 2 + (2.5 if last else 0.05)
            notes.append((max(0.0, st), int(m), vel, ring))
        # right hand melody (not in the intro), an octave higher in the second half
        if intro_bars <= b < n_bars - 1:
            lift = 12 if (b - intro_bars) >= (n_bars - intro_bars - outro_bars) // 2 + 1 else 0
            half = (b - intro_bars) % 2
            top = triad[2] + 12 + lift
            for p, step, length in motif:
                if not (half * 4 <= p < half * 4 + 4):
                    continue
                vary = int(rng.choice([0, 0, 0, 1, -1])) if (b - intro_bars) >= 4 else 0
                m = scale_note(top, step + vary)
                st = t0 + (p - half * 4) * beat + rng.normal(0, 0.012)
                vel = (0.5 + 0.12 * rng.random()) * fade * (0.9 if p % 1 else 1.0)
                notes.append((max(0.0, st), int(m), vel, length * beat + 0.4))
        if last:                                                    # final tonic note high, very soft
            notes.append((t0 + 0.02, tonic + 24, 0.32, bar + 3.0))
    info = {"key": f"{key} minor", "bpm": round(bpm, 1), "bars": n_bars,
            "progression": "-".join(roman(d, q) for d, q in prog)}
    return notes, info


def render(notes, duration: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed + 1)
    total = int((duration + 4) * SR)
    mix = np.zeros((total, 2))
    for st, m, vel, ring in notes:
        s = int(st * SR)
        if s >= total:
            continue
        x = piano_note(m, vel, ring, rng)
        x = x[: total - s]
        pan = np.clip((m - 60) / 30, -0.8, 0.8)                     # low left, high right
        mix[s:s + len(x), 0] += x * np.sqrt((1 - pan) / 2)
        mix[s:s + len(x), 1] += x * np.sqrt((1 + pan) / 2)
    for ch in range(2):
        mix[:, ch] = lowpass_fast(mix[:, ch], 5200)
    mix = reverb(mix, rng)[:total]
    mix = mix[: int(duration * SR)]
    fade = int(min(4.0, duration / 4) * SR)
    mix[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 1.5
    peak = np.abs(mix).max() or 1.0
    return mix / peak * 0.89


def write_wav(path: str, stereo: np.ndarray) -> None:
    pcm = (np.clip(stereo, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="build/music.mp3")
    ap.add_argument("--duration", type=float, default=95.0)
    ap.add_argument("--seed", type=int, default=None, help="default: today's date (a new piece every day)")
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else int(datetime.date.today().strftime("%Y%m%d"))
    notes, info = compose(seed, args.duration)
    print(f"[generate_piano] seed={seed} {info['key']}, {info['bpm']} BPM, {info['bars']} bars, "
          f"progression {info['progression']}, {len(notes)} notes")
    audio = render(notes, args.duration, seed)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    wav = os.path.splitext(args.out)[0] + ".piano.wav"
    write_wav(wav, audio)
    if args.out.endswith(".wav"):
        os.replace(wav, args.out)
    else:
        proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", wav, "-af", "loudnorm=I=-20:TP=-2:LRA=11",
                               "-c:a", "libmp3lame", "-q:a", "2", "-ar", "44100", args.out],
                              capture_output=True, text=True)
        os.remove(wav)
        if proc.returncode != 0:
            sys.exit(f"ffmpeg mp3 encode failed: {proc.stderr[-500:]}")
    print(f"[generate_piano] wrote {args.out}")


if __name__ == "__main__":
    main()
