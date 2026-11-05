#!/usr/bin/env python3
"""
Cartoon animation on a still AI keyframe: the "limited animation" toolbox of old TV cartoons.

Given where each character's head, eyes, mouth and hand are (a vision model finds them, see
generate_keyframes.mouth_hints), every shot gets:
  - lip sync: the speaker's mouth from the open-mouth picture (mouth_flaps.py), timed to the voice
  - head motion: the speaker nods/tilts with the loudness of the voice, listeners sway slowly
    (a smooth "puppet warp" around the head: no cut-out edges)
  - eye blinks for everyone at random natural intervals (eyelid in the character's own skin colour)
  - hand gestures: the speaker's hand bobs on stressed syllables
  - old TV techniques: animation "on twos" (12 poses/s), snap zoom (punch-in) and camera shake on
    shocked/angry lines, pop-up emotion symbols (! ? anger vein, sweat drop)
Iris-in / iris-out transitions are added by assemble_skit.py.

Pure numpy + Pillow + ffmpeg, CPU only.
"""
from __future__ import annotations

import math
import random
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from mouth_flaps import OPEN_SHAPES, aligned_open, mouth_states, talking_frame

ON_TWOS = 2                     # draw a new pose every 2 frames (12 poses/s at 24 fps)


# ── emotion → TV-cartoon effects ─────────────────────────────────────────────

def effects_for(emotion: str) -> dict:
    e = (emotion or "").lower()
    fx = {"symbol": None, "punch": False, "shake": 0.0, "nod": 1.0}
    if any(k in e for k in ("shock", "surpris", "gasp", "stunned", "disbelief")):
        fx.update(symbol="!", punch=True)
    elif any(k in e for k in ("angry", "shout", "furious", "yell", "red-faced", "exasperat", "frustrat")):
        fx.update(symbol="vein", punch=True, shake=7.0, nod=1.5)
    elif any(k in e for k in ("confus", "puzzl", "curious", "unsure", "doubt")):
        fx.update(symbol="?")
    elif any(k in e for k in ("nervous", "worried", "anxious", "awkward", "embarrass", "sheepish")):
        fx.update(symbol="sweat")
    elif any(k in e for k in ("quiet", "sad", "tired", "hurt", "soft", "gentle", "calm")):
        fx.update(nod=0.6)
    return fx


# ── geometry helpers ─────────────────────────────────────────────────────────

def _bilinear(img: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    sx = np.clip(sx, 0, w - 1.001)
    sy = np.clip(sy, 0, h - 1.001)
    x0, y0 = sx.astype(np.int32), sy.astype(np.int32)
    fx, fy = (sx - x0)[..., None], (sy - y0)[..., None]
    a = img[y0, x0]
    b = img[y0, x0 + 1]
    c = img[y0 + 1, x0]
    d = img[y0 + 1, x0 + 1]
    return (a * (1 - fx) * (1 - fy) + b * fx * (1 - fy) + c * (1 - fx) * fy + d * fx * fy)


def puppet_warp(arr: np.ndarray, cx: float, cy: float, radius: float, dx: float, dy: float,
                rot_deg: float = 0.0) -> None:
    """In place: move the region around (cx, cy) by (dx, dy) and rotate it by rot_deg, fading
    smoothly to no movement at `radius` (Gaussian falloff), so there is no visible seam."""
    if abs(dx) < 0.05 and abs(dy) < 0.05 and abs(rot_deg) < 0.01:
        return
    h, w = arr.shape[:2]
    r = int(radius * 1.6)
    x0, x1 = max(0, int(cx - r)), min(w, int(cx + r))
    y0, y1 = max(0, int(cy - r)), min(h, int(cy + r))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    px, py = xx - cx, yy - cy
    wgt = np.exp(-(px * px + py * py) / (radius * radius))
    th = math.radians(rot_deg) * wgt
    c, s = np.cos(th), np.sin(th)
    # inverse map: where does each output pixel come from
    sx = cx + c * px + s * py - dx * wgt
    sy = cy - s * px + c * py - dy * wgt
    arr[y0:y1, x0:x1] = _bilinear(arr, sx, sy)


def _skin_colour(arr: np.ndarray, cx: float, cy: float, r: float) -> np.ndarray:
    """Median colour of the bright, saturated-ish pixels in a ring around an eye (the face skin)."""
    h, w = arr.shape[:2]
    pts = []
    for k in range(48):
        a = 2 * math.pi * k / 48
        for rr in (1.5 * r, 1.9 * r):
            x, y = int(cx + rr * math.cos(a)), int(cy + 0.8 * rr * math.sin(a))
            if 0 <= x < w and 0 <= y < h:
                pts.append(arr[y, x])
    if not pts:
        return np.array([220, 180, 150], dtype=np.float32)
    p = np.array(pts, dtype=np.float32)
    lum = p.mean(axis=1)
    keep = p[(lum > np.percentile(lum, 40))]
    return np.median(keep if len(keep) else p, axis=0)


def draw_blink(img: Image.Image, eye: tuple[float, float], size: float, skin: np.ndarray) -> None:
    """Closed eye: skin-coloured lid over the eye plus a dark curved lash line."""
    d = ImageDraw.Draw(img)
    x, y = eye
    rx, ry = size, size * 0.8
    col = tuple(int(v) for v in skin)
    d.ellipse([x - rx, y - ry, x + rx, y + ry], fill=col)
    lw = max(2, int(size * 0.18))
    d.arc([x - rx * 0.95, y - ry * 0.9, x + rx * 0.95, y + ry * 0.55], start=15, end=165, fill=(25, 20, 20), width=lw)


def draw_symbol(img: Image.Image, kind: str, at: tuple[float, float], size: float, pop: float) -> None:
    """TV-cartoon emotion symbol near the head, scaled by `pop` (0..1, with overshoot)."""
    if pop <= 0.02:
        return
    d = ImageDraw.Draw(img)
    x, y = at
    s = size * pop
    ink = (25, 20, 20)
    if kind == "!":
        d.polygon([(x - 0.18 * s, y - s), (x + 0.18 * s, y - s), (x + 0.08 * s, y + 0.25 * s), (x - 0.08 * s, y + 0.25 * s)],
                  fill=(250, 220, 40), outline=ink, width=max(2, int(s * 0.07)))
        d.ellipse([x - 0.13 * s, y + 0.42 * s, x + 0.13 * s, y + 0.68 * s], fill=(250, 220, 40), outline=ink,
                  width=max(2, int(s * 0.07)))
    elif kind == "?":
        w = max(3, int(s * 0.16))
        d.arc([x - 0.35 * s, y - s, x + 0.35 * s, y - 0.3 * s], start=180, end=60, fill=(60, 120, 230), width=w)
        d.line([(x + 0.18 * s, y - 0.42 * s), (x, y - 0.2 * s), (x, y + 0.15 * s)], fill=(60, 120, 230), width=w)
        d.ellipse([x - 0.1 * s, y + 0.35 * s, x + 0.1 * s, y + 0.55 * s], fill=(60, 120, 230))
    elif kind == "vein":
        w = max(3, int(s * 0.14))
        red = (220, 40, 40)
        for ang in (45, 135, 225, 315):
            a = math.radians(ang)
            ox, oy = x + 0.22 * s * math.cos(a), y + 0.22 * s * math.sin(a)
            d.arc([ox - 0.28 * s, oy - 0.28 * s, ox + 0.28 * s, oy + 0.28 * s],
                  start=ang + 90, end=ang + 270, fill=red, width=w)
    elif kind == "sweat":
        d.polygon([(x, y - 0.55 * s), (x - 0.28 * s, y + 0.05 * s), (x + 0.28 * s, y + 0.05 * s)], fill=(150, 200, 250))
        d.ellipse([x - 0.28 * s, y - 0.25 * s, x + 0.28 * s, y + 0.3 * s], fill=(150, 200, 250),
                  outline=(60, 110, 180), width=max(2, int(s * 0.06)))


def _pop(t: float, t_in: float = 0.25) -> float:
    """Scale-in with a little overshoot, then hold."""
    if t <= 0:
        return 0.0
    if t >= t_in:
        return 1.0
    u = t / t_in
    return 1 + 0.25 * math.sin(math.pi * u) - (1 - u) ** 2


# ── the shot ─────────────────────────────────────────────────────────────────

def render_cartoon(closed_path: str, open_path: str | None, rig: dict, speaker: str, emotion: str,
                   dest: str, duration: float, fps: int, focus: float, zoom: float, track, t0: float,
                   seed: int = 0, size=(1080, 1920)) -> dict:
    """rig: {char_id: {"head": [x, y], "left_eye": [x, y], "right_eye": [x, y], "mouth_box": [...],
    "hand": [x, y] | None}} in keyframe pixels. Returns counters of what was animated."""
    rng = random.Random(seed)
    W, H = size
    base = Image.open(closed_path).convert("RGB")
    scale = max(2 * W / base.width, 2 * H / base.height)
    cw, ch = int(round(base.width * scale)), int(round(base.height * scale))
    closed = base.resize((cw, ch), Image.LANCZOS)
    opened = None
    if open_path:
        opened = aligned_open(base, Image.open(open_path)).resize((cw, ch), Image.LANCZOS)

    def S(p):
        return None if not p else (p[0] * scale, p[1] * scale)

    chars = {}
    for cid, f in rig.items():
        le, re_ = S(f.get("left_eye")), S(f.get("right_eye"))
        eye_d = math.dist(le, re_) if le and re_ else 0.035 * cw
        head = S(f.get("head")) or (((le[0] + re_[0]) / 2, (le[1] + re_[1]) / 2) if le and re_ else None)
        hand = S(f.get("hand"))
        rad = max(30.0, 2.6 * eye_d)
        # a gesturing hand is within arm's reach of the head and not down at the floor (run #60: a sitting
        # character's "hand" point landed on his sneakers, so the gesture moved his shoes)
        if hand and head and not (math.dist(hand, head) <= 5.0 * rad and hand[1] <= head[1] + 4.0 * rad):
            hand = None
        chars[cid] = {"head": head, "eyes": [e for e in (le, re_) if e], "eye_size": max(4.0, 0.3 * eye_d),
                      "radius": rad, "hand": hand,
                      "mouth_box": [int(round(v * scale)) for v in f["mouth_box"]] if f.get("mouth_box") else None,
                      "blinks": [], "phase": rng.uniform(0, 6.28)}
        t = rng.uniform(0.4, 2.0)
        while t < duration + 1.5:                                    # natural blink times
            chars[cid]["blinks"].append(t)
            t += rng.uniform(2.2, 5.0)
        if chars[cid]["eyes"]:
            ex, ey = chars[cid]["eyes"][0]
            chars[cid]["skin"] = _skin_colour(np.asarray(closed), ex, ey, chars[cid]["eye_size"])

    spk = chars.get(speaker)
    talk_img = talking_frame(closed, opened, spk["mouth_box"]) if (spk and opened is not None and spk["mouth_box"]) else None
    fx = effects_for(emotion)
    n = max(2, int(round(duration * fps)) + fps)
    states = mouth_states(track, t0, n, fps) if talk_img is not None else [False] * n
    left, top = (cw - 2 * W) / 2, (ch - 2 * H) / 2
    z0 = max(1.0, zoom - 0.06)
    yc0 = 0.5 - (zoom - 1.0) * 0.2
    counts = {"open": 0, "blinks": 0, "nods": 0, "hands": 0, "symbol": fx["symbol"] or "", "punch": fx["punch"],
              "poses": 0, "frames": n}

    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", dest]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    pose = None
    try:
        for k in range(n):
            t = k / fps
            if k % ON_TWOS == 0 or pose is None:                    # new drawing every 2 frames ("on twos")
                counts["poses"] += 1
                src = talk_img if states[k] else closed
                img = src.copy()
                counts["open"] += int(states[k])
                for cid, c in chars.items():                        # blinks (drawn before the warp so they move with the head)
                    if c["eyes"] and "skin" in c and any(b <= t < b + 0.12 for b in c["blinks"]):
                        for e in c["eyes"]:
                            draw_blink(img, e, c["eye_size"], c["skin"])
                        counts["blinks"] += 1
                arr = np.asarray(img, dtype=np.float32).copy()
                loud = min(1.2, track.loudness(t0 + t)) if track else 0.0
                for cid, c in chars.items():
                    if not c["head"]:
                        continue
                    if cid == speaker:                               # talking head: nods + tilt with the voice
                        amp = (2.0 + 9.0 * loud) * fx["nod"] * scale / 2
                        dy = amp * math.sin(2 * math.pi * 2.2 * t + c["phase"])
                        rot = (0.6 + 1.6 * loud) * fx["nod"] * math.sin(2 * math.pi * 0.9 * t + c["phase"])
                        counts["nods"] += int(loud > 0.15)
                    else:                                            # listeners: slow idle sway
                        dy = 1.5 * scale / 2 * math.sin(2 * math.pi * 0.35 * t + c["phase"])
                        rot = 0.5 * math.sin(2 * math.pi * 0.25 * t + c["phase"])
                    puppet_warp(arr, c["head"][0], c["head"][1], c["radius"], 0.0, dy, rot)
                    if cid == speaker and c["hand"] and loud > 0.2:  # gesture beats on stressed syllables
                        g = (6 + 14 * loud) * scale / 2
                        puppet_warp(arr, c["hand"][0], c["hand"][1], c["radius"] * 0.8,
                                    0.4 * g * math.sin(2 * math.pi * 1.7 * t), -g * abs(math.sin(2 * math.pi * 1.7 * t)))
                        counts["hands"] += 1
                img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
                if fx["symbol"] and spk and spk["head"]:               # pop-up emotion symbol
                    hx, hy = spk["head"]
                    draw_symbol(img, fx["symbol"], (hx + spk["radius"] * 0.75, hy - spk["radius"] * 0.95 +
                                                     6 * math.sin(2 * math.pi * 1.2 * t)),
                                spk["radius"] * 0.85, _pop(t - 0.15))
                pose = img
            # camera: push-in toward focus, snap zoom on dramatic lines, shake when shouting
            z = z0 + (zoom - z0) * k / n
            if fx["punch"]:
                z *= 1.0 + 0.16 * min(1.0, t / 0.12)
            vw, vh = 2 * W / z, 2 * H / z
            x0 = min(max(left + 2 * W * focus - vw / 2, left), left + 2 * W - vw)
            y0 = min(max(top + 2 * H * yc0 - vh / 2, top), top + 2 * H - vh)
            if fx["shake"] and t < 0.6:
                amp = fx["shake"] * (1 - t / 0.6) * 2
                x0 += rng.uniform(-amp, amp)
                y0 += rng.uniform(-amp, amp)
            x0 = min(max(x0, 0.0), cw - vw)                          # shake never leaves the picture
            y0 = min(max(y0, 0.0), ch - vh)
            frame = pose.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + vw, y0 + vh))
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed ({rc})")
    return counts
