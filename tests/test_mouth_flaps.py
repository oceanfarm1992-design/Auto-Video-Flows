"""Offline tests for mouth flaps on AI keyframes (scripts/mouth_flaps.py). Synthetic pictures, no network.

Run from the repo root:  python tests/test_mouth_flaps.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import mouth_flaps as mf  # noqa: E402
from lipsync import MouthTrack  # noqa: E402

FAILED = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILED.append(name)


W, H = 1152, 2048
FACES = [(330, 760), (820, 790)]          # two characters, left and right


def picture(open_mouth: bool, shift=(0, 0), noise=0.0, seed=0) -> Image.Image:
    im = Image.new("RGB", (W, H), (205, 190, 165))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 1500, W, H], fill=(150, 110, 70))            # floor
    d.rectangle([80, 200, 420, 600], fill=(170, 200, 230), outline=(30, 30, 30), width=8)   # window
    for fx, fy in FACES:
        d.rectangle([fx - 110, fy + 130, fx + 110, fy + 700], fill=(40, 60, 120), outline=(20, 20, 20), width=8)
        d.ellipse([fx - 110, fy - 120, fx + 110, fy + 120], fill=(240, 200, 160), outline=(20, 20, 20), width=8)
        d.ellipse([fx - 50, fy - 30, fx - 30, fy - 10], fill=(20, 20, 20))
        d.ellipse([fx + 30, fy - 30, fx + 50, fy - 10], fill=(20, 20, 20))
        if open_mouth:
            d.ellipse([fx - 38, fy + 35, fx + 38, fy + 85], fill=(60, 15, 20), outline=(20, 20, 20), width=6)
        else:
            d.line([fx - 35, fy + 55, fx + 35, fy + 55], fill=(20, 20, 20), width=7)
    a = np.asarray(im).astype(np.float32)
    if noise:
        a += np.random.default_rng(seed).normal(0, noise, a.shape)
    a = np.roll(a, shift, axis=(0, 1))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


closed = picture(False)
opened = picture(True, shift=(5, -4), noise=4.0)
check("alignment recovers the edit's shift", mf.estimate_shift(
    mf._luma(closed.resize((W // 2, H // 2))), mf._luma(opened.resize((W // 2, H // 2)))) in ((-2, 2), (-3, 2), (-2, 3), (-3, 3)))
aligned = mf.aligned_open(closed, opened)
boxes = mf.detect_mouths(closed, aligned, 2)
check("finds exactly two mouths", len(boxes) == 2)


def contains(box, x, y):
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


check("each box covers its character's mouth, left to right",
      len(boxes) == 2 and contains(boxes[0], 330, 815) and contains(boxes[1], 820, 845))
check("boxes stay small (a mouth, not a face)",
      all((b[2] - b[0]) < 260 and (b[3] - b[1]) < 220 for b in boxes))
m = mf.assign_mouths(boxes, ["the_calm", "the_loud"], W)
check("left mouth -> first character", set(m) == {"the_calm", "the_loud"} and m["the_calm"][0] < m["the_loud"][0])
check("one box goes to the nearest character", list(mf.assign_mouths([boxes[1]], ["a", "b"], W)) == ["b"])
check("no change -> no mouths", mf.detect_mouths(closed, closed, 2) == [])

frame = np.asarray(mf.talking_frame(closed, aligned, m["the_calm"])).astype(int)
base = np.asarray(closed).astype(int)
check("talking frame changes the speaker's mouth", np.abs(frame[803, 330] - base[803, 330]).sum() > 100)
check("...and nothing at the listener's mouth", np.abs(frame[845, 820] - base[845, 820]).sum() < 30)
check("...and nothing elsewhere", np.abs(frame[300, 200] - base[300, 200]).sum() < 30)

track = MouthTrack([(0.0, "X"), (0.5, "D"), (0.6, "B"), (0.7, "E"), (0.9, "X")])
states = mf.mouth_states(track, 0.0, 30, 24)
check("mouth opens while talking and is closed in silence", any(states) and not states[0] and not states[-1])
runs = [len(r) for r in "".join("1" if s else "0" for s in states).replace("0", " ").split()]
check("flaps hold at least two frames", all(r >= mf.MIN_HOLD_FRAMES for r in runs))

with tempfile.TemporaryDirectory() as tmp:
    c, o, out = (os.path.join(tmp, n) for n in ("c.png", "o.png", "s.mp4"))
    closed.save(c)
    opened.save(o)
    n_open = mf.render_talking(c, o, m["the_calm"], out, 1.0, 24, 0.29, 1.35, track, 0.0)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,nb_frames",
                            "-of", "csv=p=0", out], capture_output=True, text=True).stdout.strip()
    check(f"talking clip renders 1080x1920 with open-mouth frames ({probe}, open={n_open})",
          probe.startswith("1080,1920") and n_open > 0)

sys.exit(1 if FAILED else 0)
