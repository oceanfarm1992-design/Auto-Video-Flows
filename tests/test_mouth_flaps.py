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



# a bearded character: the open mouth is NOT darker than the beard (teeth/tongue are lighter)
def bearded(open_mouth: bool, shift=(0, 0)) -> Image.Image:
    im = picture(False)
    d = ImageDraw.Draw(im)
    fx, fy = FACES[0]
    d.ellipse([fx - 100, fy + 10, fx + 100, fy + 125], fill=(55, 40, 35))      # dark beard over the jaw
    if open_mouth:
        d.ellipse([fx - 38, fy + 40, fx + 38, fy + 92], fill=(170, 60, 70), outline=(20, 20, 20), width=5)
        d.rectangle([fx - 28, fy + 44, fx + 28, fy + 58], fill=(245, 245, 240))  # teeth
        fx2, fy2 = FACES[1]
        d.ellipse([fx2 - 38, fy2 + 35, fx2 + 38, fy2 + 85], fill=(60, 15, 20), outline=(20, 20, 20), width=6)
    a = np.roll(np.asarray(im), shift, axis=(0, 1))
    return Image.fromarray(a)


bc, bo = bearded(False), bearded(True, shift=(3, 2))
bboxes = mf.detect_mouths(bc, mf.aligned_open(bc, bo), 2)
bm = mf.assign_mouths(bboxes, ["the_provider", "the_wife"], W)
check("bearded character's mouth is found (teeth in a dark beard)",
      "the_provider" in bm and contains(bm["the_provider"], 330, 830))
check("...and the other character is still found", "the_wife" in bm and contains(bm["the_wife"], 820, 845))
with tempfile.TemporaryDirectory() as tmp:
    out = os.path.join(tmp, "dbg.jpg")
    mf.debug_overlay(bc, bm, out)
    check("debug overlay saved for the run preview", os.path.getsize(out) > 1000)



# run #56: the edit redrew the bearded character's EYES (wider) and opened the mouth only a little
def eyes_and_mouth(open_mouth: bool, mouth: bool = True) -> Image.Image:
    im = bearded(False)
    d = ImageDraw.Draw(im)
    fx, fy = FACES[0]
    if open_mouth:
        for ex in (fx - 40, fx + 40):                                   # both eyes redrawn bigger
            d.ellipse([ex - 22, fy - 45, ex + 22, fy + 5], fill=(250, 250, 250), outline=(20, 20, 20), width=5)
            d.ellipse([ex - 8, fy - 28, ex + 8, fy - 12], fill=(20, 20, 20))
        if mouth:
            d.ellipse([fx - 22, fy + 55, fx + 22, fy + 80], fill=(165, 60, 70))
            d.rectangle([fx - 16, fy + 57, fx + 16, fy + 63], fill=(245, 245, 240))
    return im


ec = eyes_and_mouth(False)
eo = eyes_and_mouth(True)
b1 = mf.detect_mouths(ec, mf.aligned_open(ec, eo), 2)
check("redrawn eye pair is NOT taken for the mouth; the mouth below is",
      len(b1) >= 1 and contains(b1[0], 330, 857) and not contains(b1[0], 330, 770))
only_eyes = eyes_and_mouth(True, mouth=False)
b2 = mf.detect_mouths(ec, mf.aligned_open(ec, only_eyes), 2)
check("if only the eyes changed, no mouth is invented for that character",
      not any(contains(b, 330, 770) for b in b2))
b3 = mf.detect_mouths(ec, mf.aligned_open(ec, eo), 2, hints=[(330 / W, 867 / H), None])
check("a vision hint at the mouth picks the mouth", len(b3) >= 1 and contains(b3[0], 330, 857))
b4 = mf.detect_mouths(ec, mf.aligned_open(ec, only_eyes), 2, hints=[(330 / W, 867 / H), None])
check("a hint where nothing changed gives no box", not any(contains(b, 330, 867) for b in b4))



# the patch must not drag in changes from right above the mouth (the eye ghost from run #57)
eb = mf.detect_mouths(ec, mf.aligned_open(ec, eo), 2, hints=[(330 / W, 867 / H), None])
tf = np.asarray(mf.talking_frame(ec, mf.aligned_open(ec, eo), eb[0])).astype(int)
base_e = np.asarray(ec).astype(int)
check("mouth box stays below the eyes", bool(eb) and eb[0][1] > 765)
# synthetic face: eyes at y 715-765 (x 290 / 370), mouth at y 815-840 (x 330)
check("talking frame leaves the eyes untouched", np.abs(tf[740, 290] - base_e[740, 290]).sum() < 20
      and np.abs(tf[740, 370] - base_e[740, 370]).sum() < 20)
check("...but opens the mouth", np.abs(tf[832, 330] - base_e[832, 330]).sum() > 100)

sys.exit(1 if FAILED else 0)
