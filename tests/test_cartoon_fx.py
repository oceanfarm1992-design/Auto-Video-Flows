"""Offline tests for the cartoon toolbox (scripts/cartoon_fx.py) on a synthetic keyframe.

Run from the repo root:  python tests/test_cartoon_fx.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))
import cartoon_fx as cf  # noqa: E402
from lipsync import MouthTrack  # noqa: E402

FAILED = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILED.append(name)


# reuse the synthetic two-character picture from the mouth-flap tests
src = open(Path(__file__).parent / "test_mouth_flaps.py").read().split("closed = picture(False)")[0]
ns: dict = {"__file__": str(Path(__file__).parent / "test_mouth_flaps.py")}
exec(src.replace("sys.exit(1 if FAILED else 0)", ""), ns)
picture, FACES, W, H = ns["picture"], ns["FACES"], ns["W"], ns["H"]

check("shouting -> anger vein, snap zoom, shake", cf.effects_for("red-faced, shouting") ==
      {"symbol": "vein", "punch": True, "shake": 7.0, "nod": 1.5})
check("surprise -> !", cf.effects_for("stunned")["symbol"] == "!")
check("nervous -> sweat drop", cf.effects_for("nervous laugh")["symbol"] == "sweat")
check("calm line -> no symbol, gentler nods", cf.effects_for("calm")["symbol"] is None and cf.effects_for("calm")["nod"] < 1)

arr = np.asarray(picture(False), dtype=np.float32).copy()
before = arr.copy()
cf.puppet_warp(arr, 330, 760, 120, 0, 12, 4)
moved = np.abs(arr - before).sum(axis=2)
check("puppet warp moves the head", moved[700:820, 230:430].mean() > 2)
check("...and leaves far-away pixels alone", moved[1700:1900, 800:1100].max() < 1e-3)

img = picture(False)
skin = cf._skin_colour(np.asarray(img), 290, 740, 12)
check("skin colour sampled from the face", abs(skin[0] - 240) < 25 and abs(skin[1] - 200) < 25)

rig = {"a": {"head": [330, 760], "left_eye": [370, 740], "right_eye": [290, 740], "hand": [330, 1150],
             "mouth_box": [280, 790, 380, 870]},
       "b": {"head": [820, 790], "left_eye": [860, 770], "right_eye": [780, 770]}}
track = MouthTrack([(0.0, "X"), (0.2, "D"), (0.45, "B"), (0.6, "E"), (0.9, "X")])
track.env = np.full(200, 0.8, dtype=np.float32)                   # loud voice the whole time
with tempfile.TemporaryDirectory() as tmp:
    c, o, out = (os.path.join(tmp, n) for n in ("c.png", "o.png", "s.mp4"))
    picture(False).save(c)
    picture(True).save(o)
    counts = cf.render_cartoon(c, o, rig, "a", "red-faced, shouting", out, 3.0, 24, 0.3, 1.2, track, 0.0, seed=3)
    check(f"counters: lips, blinks, nods, hand, symbol ({counts})",
          counts["open"] > 0 and counts["blinks"] > 0 and counts["nods"] > 0 and counts["hands"] > 0
          and counts["symbol"] == "vein" and counts["punch"])
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,nb_frames",
                            "-of", "csv=p=0", out], capture_output=True, text=True).stdout.strip()
    check(f"renders a 1080x1920 clip ({probe})", probe.startswith("1080,1920"))
    check(f"animated on twos: one new pose every 2 frames ({counts['poses']} poses / {counts['frames']} frames)",
          counts["poses"] == (counts["frames"] + 1) // 2)

# deterministic blink schedule: everyone blinks within a few seconds
check("pop-in overshoots then settles", cf._pop(0.18) > 1.0 and cf._pop(1.0) == 1.0 and cf._pop(0) == 0)

sys.exit(1 if FAILED else 0)
