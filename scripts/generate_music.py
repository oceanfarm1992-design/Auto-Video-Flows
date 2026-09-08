#!/usr/bin/env python3
"""
Generate a cinematic motivational music bed with ffmpeg.

Produces an epic, punchy background track — deep bass pulse, full mid-range
power chord pad, high harmonic sparkle, and a dramatic swell that builds
through the video. Rotates through 6 distinct cinematic chord moods daily.
All synthesis is pure ffmpeg — no audio files, no API, zero cost.

Output: build/music.mp3  (assemble mixes it under the voice)

Usage:
    python scripts/generate_music.py
    python scripts/generate_music.py --duration 50 --mood 2
"""
import argparse
import datetime
import os
import subprocess
import sys

# Cinematic power chords (Hz). Format: [root, fifth, octave, major3rd, fifth2]
# Designed for emotional impact — deeper roots = more physical presence.
CHORDS = [
    # Epic minor — dark, powerful, unstoppable
    {"name": "epic_minor",   "freqs": [41.2, 61.7, 82.4, 98.0, 123.5, 164.8]},
    # Triumphant major — breakthrough, victory
    {"name": "triumphant",   "freqs": [32.7, 49.0, 65.4, 82.4, 98.0, 130.8]},
    # Tension build — D minor, unresolved energy
    {"name": "tension",      "freqs": [36.7, 55.0, 73.4, 87.3, 110.0, 146.8]},
    # Heroic resolve — G major power
    {"name": "heroic",       "freqs": [49.0, 73.4, 98.0, 123.5, 146.8, 196.0]},
    # Soaring — A minor, cinematic rise
    {"name": "soaring",      "freqs": [55.0, 82.4, 110.0, 130.8, 164.8, 220.0]},
    # Raw power — E power chord, gut punch
    {"name": "raw_power",    "freqs": [41.2, 61.7, 82.4, 110.0, 123.5, 164.8]},
]

# Volume weights per layer: bass root is loudest, harmonics taper
LAYER_VOLS  = [0.45, 0.30, 0.22, 0.18, 0.14, 0.10]
DETUNE_RATE = 1.003   # very slight detune for chorus/warmth


def build_filter(freqs: list, duration: float) -> str:
    chains = []
    labels = []

    # ── Tonal layers: in-tune + detuned pairs for chorus warmth ──────────────
    for i, f in enumerate(freqs):
        vol     = LAYER_VOLS[i] if i < len(LAYER_VOLS) else 0.08
        vol_det = vol * 0.75

        chains.append(
            f"sine=frequency={f:.3f}:duration={duration}:sample_rate=44100,"
            f"volume={vol:.3f}[s{i}a]"
        )
        chains.append(
            f"sine=frequency={f * DETUNE_RATE:.4f}:duration={duration}:sample_rate=44100,"
            f"volume={vol_det:.3f}[s{i}b]"
        )
        labels += [f"[s{i}a]", f"[s{i}b]"]

    # ── Sub-bass: one octave below root, pulsing at 60 BPM via volume expr ───
    # abs(sin(PI*t)) gives a smooth 1 Hz positive pulse (0→1→0→1…)
    sub_freq = freqs[0] * 0.5
    chains.append(
        f"sine=frequency={sub_freq:.3f}:duration={duration}:sample_rate=44100,"
        f"volume='0.55*abs(sin(3.14159*1.0*t))'[sub]"
    )
    labels.append("[sub]")

    n = len(labels)
    fade_out = max(0.0, duration - 3.5)

    # ── Swell: starts quiet, builds to full by 20s, holds ────────────────────
    # Uses only basic ffmpeg arithmetic — no tremolo/equalizer filter needed
    swell = "volume='if(lt(t,5),0.15,if(lt(t,20),0.15+0.65*((t-5)/15),0.80))'"

    # ── Pumping pulse on full mix: 2 Hz volume LFO = 120 BPM feel ────────────
    # 0.70 + 0.30*abs(sin(PI*2*t)) keeps volume between 0.70 and 1.00
    pump = "volume='0.70+0.30*abs(sin(3.14159*2.0*t))'"

    mix = (
        "".join(labels) + f"amix=inputs={n}:normalize=0,"
        # Warmth: roll off harsh high-frequency alias noise
        "lowpass=f=3500,"
        # Cinematic space: simple single echo/reverb
        "aecho=0.70:0.80:55:0.30,"
        # Dramatic swell envelope
        f"{swell},"
        # 120 BPM pumping volume LFO
        f"{pump},"
        # Fade in/out
        f"afade=t=in:d=2,afade=t=out:st={fade_out:.2f}:d=3.5,"
        # Normalize to consistent loudness
        "loudnorm=I=-14:TP=-1:LRA=9[out]"
    )

    return ";".join(chains) + ";" + mix


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out",      default="build/music.mp3")
    ap.add_argument("--duration", type=float, default=30.0,
                    help="Loop length in seconds.")
    ap.add_argument("--mood",     type=int, default=None,
                    help="Force chord mood index (0-5).")
    args = ap.parse_args()

    if args.mood is not None:
        chord = CHORDS[args.mood % len(CHORDS)]
    else:
        day   = datetime.date.today().timetuple().tm_yday
        chord = CHORDS[day % len(CHORDS)]

    print(f"[generate_music] mood={chord['name']} root={chord['freqs'][0]}Hz "
          f"duration={args.duration}s")

    filter_complex = build_filter(chord["freqs"], args.duration)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-t", f"{args.duration}",
        "-c:a", "libmp3lame", "-q:a", "3", "-ar", "44100", "-ac", "2",
        args.out,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace")[-3000:])
        raise SystemExit(f"ffmpeg music render failed ({proc.returncode})")
    print(f"[generate_music] wrote {args.out}")


if __name__ == "__main__":
    main()
