"""Offline tests for the sad piano generator (scripts/generate_piano.py) and its wiring.

Run from the repo root:  python tests/test_piano.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import generate_music as gm  # noqa: E402
import generate_piano as gp  # noqa: E402

FAILED = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILED.append(name)


wrong = 0
for seed in range(60):
    notes, info = gp.compose(seed, 60)
    tonic = gp.KEYS[list(gp.KEYS)[seed % len(gp.KEYS)]]
    allowed = {(tonic + s) % 12 for s in gp.MINOR_SCALE} | {(tonic + 11) % 12}   # + V's leading tone
    wrong += sum(1 for _, m, _, _ in notes if m % 12 not in allowed)
check("every note is in the minor key (60 pieces)", wrong == 0)

n1, i1 = gp.compose(7, 60)
n2, _ = gp.compose(7, 60)
n3, _ = gp.compose(8, 60)
check("same seed -> same piece", n1 == n2)
check("different seed -> different piece", n1 != n3)
check("slow, sad tempo", 52 <= i1["bpm"] <= 62 and "minor" in i1["key"])
check("piece fills the requested length", max(s for s, *_ in n1) > 45)
check("ends on the tonic", n1[-1][1] % 12 == gp.KEYS[list(gp.KEYS)[7 % len(gp.KEYS)]] % 12)
check("velocities soft (no loud hits)", all(0 < v <= 0.7 for _, _, v, _ in n1))

note = gp.piano_note(60, 0.5, 1.0, np.random.default_rng(0))
check("a note decays (rings, then fades)", np.abs(note[:2000]).max() > 5 * np.abs(note[-2000:]).max())
audio = gp.render(gp.compose(3, 12)[0], 12, 3)
check("render: stereo, right length, no clipping, no NaN",
      audio.shape == (12 * gp.SR, 2) and np.isfinite(audio).all() and np.abs(audio).max() <= 0.9)

with tempfile.TemporaryDirectory() as tmp:
    out = os.path.join(tmp, "m.mp3")
    sys.argv = ["x", "--out", out, "--duration", "10", "--seed", "5"]
    gp.main()
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                               capture_output=True, text=True).stdout or 0)
    check(f"writes a playable mp3 of the right length ({dur:.1f}s)", abs(dur - 10) < 0.3)

    # generate_music: sad_piano is the default and falls back to the pad if the piano fails
    check("config default style is sad_piano", gm.music_config().get("style") == "sad_piano")
    os.environ["GITHUB_RUN_ID"] = "123456"
    check("seed follows the GitHub run (new piece per run)", gm.run_seed() == 123456)
    real = gp.main
    gp.main = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    out2 = os.path.join(tmp, "pad.mp3")
    sys.argv = ["generate_music.py", "--out", out2, "--duration", "6", "--style", "sad_piano"]
    gm.main()
    gp.main = real
    check("piano failure falls back to the ambient pad", os.path.getsize(out2) > 1000)

sys.exit(1 if FAILED else 0)
