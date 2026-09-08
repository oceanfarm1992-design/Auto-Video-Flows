#!/usr/bin/env python3
"""
Generate a cinematic motivational music bed with ffmpeg.

Produces a deep, powerful background track with dramatic swell using only
universally supported ffmpeg filters (no tremolo/equalizer expressions).
6 chord moods rotate daily. Zero cost, zero assets.

Output: build/music.mp3
"""
import argparse
import datetime
import os
import subprocess
import sys

# Cinematic power chords (Hz) — deeper roots = more physical presence
CHORDS = [
    {"name": "epic_minor",  "freqs": [41.2, 61.7, 82.4,  98.0, 123.5, 164.8]},
    {"name": "triumphant",  "freqs": [32.7, 49.0, 65.4,  82.4,  98.0, 130.8]},
    {"name": "tension",     "freqs": [36.7, 55.0, 73.4,  87.3, 110.0, 146.8]},
    {"name": "heroic",      "freqs": [49.0, 73.4, 98.0, 123.5, 146.8, 196.0]},
    {"name": "soaring",     "freqs": [55.0, 82.4, 110.0, 130.8, 164.8, 220.0]},
    {"name": "raw_power",   "freqs": [41.2, 61.7, 82.4, 110.0, 123.5, 164.8]},
]

LAYER_VOLS  = [0.45, 0.30, 0.22, 0.18, 0.14, 0.10]
DETUNE_RATE = 1.003


def build_filter(freqs: list, duration: float) -> str:
    chains = []
    labels = []

    # Tonal layers: in-tune + detuned pairs for chorus warmth
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

    # Sub-bass: one octave below root at constant volume (keeps it simple/stable)
    sub_freq = freqs[0] * 0.5
    chains.append(
        f"sine=frequency={sub_freq:.3f}:duration={duration}:sample_rate=44100,"
        f"volume=0.50[sub]"
    )
    labels.append("[sub]")

    n        = len(labels)
    fade_out = max(0.0, duration - 5.0)

    mix = (
        "".join(labels) + f"amix=inputs={n}:normalize=0,"
        # Warmth: roll off alias noise from sine sources
        "lowpass=f=3500,"
        # Cinematic space: echo
        "aecho=0.70:0.80:55:0.30,"
        # Dramatic 14-second swell-in (builds energy as the video opens)
        "afade=t=in:st=0:d=14,"
        # Fade out near the end
        f"afade=t=out:st={fade_out:.2f}:d=3.5,"
        # Normalize to consistent loudness
        "loudnorm=I=-14:TP=-1:LRA=9[out]"
    )

    return ";".join(chains) + ";" + mix


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out",      default="build/music.mp3")
    ap.add_argument("--duration", type=float, default=90.0)
    ap.add_argument("--mood",     type=int,   default=None)
    args = ap.parse_args()

    if args.mood is not None:
        chord = CHORDS[args.mood % len(CHORDS)]
    else:
        day   = datetime.date.today().timetuple().tm_yday
        chord = CHORDS[day % len(CHORDS)]

    print(f"[generate_music] mood={chord['name']} root={chord['freqs'][0]}Hz "
          f"duration={args.duration}s")

    fc = build_filter(chord["freqs"], args.duration)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-filter_complex", fc,
        "-map", "[out]",
        "-t", f"{args.duration}",
        "-c:a", "libmp3lame", "-q:a", "3", "-ar", "44100", "-ac", "2",
        args.out,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace")[-2000:])
        raise SystemExit(f"ffmpeg music render failed ({proc.returncode})")
    print(f"[generate_music] wrote {args.out}")


if __name__ == "__main__":
    main()
