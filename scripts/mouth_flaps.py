#!/usr/bin/env python3
"""
Mouth flaps for AI keyframes: the cheap "limited animation" lip sync TV cartoons use.

For every keyframe we have two pictures: the original (mouths closed) and an image-edit of
it with every mouth open. This module
  1. aligns the two (an image edit can shift the whole picture by a few pixels),
  2. finds each character's mouth: the compact regions that turned much darker (an open
     mouth is a dark hole where there was skin and a line), inside the face band,
  3. renders a shot: the camera move of local_motion() plus, while the speaker is talking
     (lip-sync track: Rhubarb on CI, else the built-in method), only THEIR mouth patch pasted
     from the open picture with soft edges. Everything else stays pixel-identical, so nothing
     flickers; listeners keep their mouths closed.

Pure numpy + Pillow + ffmpeg. No GPU, no network.
"""
from __future__ import annotations

import subprocess
from collections import deque

import numpy as np
from PIL import Image, ImageFilter

OPEN_SHAPES = set("CDEFGH")          # lip-sync shapes drawn with the open mouth; A, B, X stay closed
MIN_HOLD_FRAMES = 2                  # never flap faster than every 2 frames (12 Hz at 24 fps)


def _luma(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("L"), dtype=np.float32)


def estimate_shift(a: np.ndarray, b: np.ndarray, max_shift: int = 24) -> tuple[int, int]:
    """(dy, dx) to apply to b so it lines up with a (phase correlation)."""
    fa, fb = np.fft.rfft2(a - a.mean()), np.fft.rfft2(b - b.mean())
    cross = fa * np.conj(fb)
    cross /= np.abs(cross) + 1e-6
    corr = np.fft.irfft2(cross, s=a.shape)
    dy, dx = np.unravel_index(int(np.argmax(corr)), corr.shape)
    dy = dy - a.shape[0] if dy > a.shape[0] // 2 else dy
    dx = dx - a.shape[1] if dx > a.shape[1] // 2 else dx
    if abs(dy) > max_shift or abs(dx) > max_shift:
        return 0, 0
    return int(dy), int(dx)


def aligned_open(closed: Image.Image, opened: Image.Image) -> Image.Image:
    """The open picture resized to the closed one and shifted onto it."""
    opened = opened.convert("RGB").resize(closed.size, Image.LANCZOS)
    small = (closed.width // 2, closed.height // 2)
    dy, dx = estimate_shift(_luma(closed.resize(small)), _luma(opened.resize(small)))
    if dy or dx:
        opened = Image.fromarray(np.roll(np.asarray(opened), (dy * 2, dx * 2), axis=(0, 1)))
    return opened


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """Connected regions of a boolean mask: (area, y0, x0, y1, x1). 4-connectivity, BFS."""
    seen = np.zeros_like(mask, dtype=bool)
    h, w = mask.shape
    out = []
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        q = deque([(y, x)])
        seen[y, x] = True
        area, y0, x0, y1, x1 = 0, y, x, y, x
        while q:
            cy, cx = q.popleft()
            area += 1
            y0, x0, y1, x1 = min(y0, cy), min(x0, cx), max(y1, cy), max(x1, cx)
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        out.append((area, y0, x0, y1, x1))
    return out


def _change_mask(closed: Image.Image, opened: Image.Image, scale: int, thresh: float) -> np.ndarray:
    """Pixels whose colour changed strongly in either direction (an open mouth can be darker than
    skin, or lighter than a dark beard: white teeth, red tongue), thin shifted lines removed."""
    small = (closed.width // scale, closed.height // scale)
    a = np.asarray(closed.resize(small, Image.BILINEAR).filter(ImageFilter.GaussianBlur(1)), dtype=np.float32)
    b = np.asarray(opened.resize(small, Image.BILINEAR).filter(ImageFilter.GaussianBlur(1)), dtype=np.float32)
    changed = np.abs(a - b).max(axis=2) > thresh
    m = Image.fromarray((changed * 255).astype(np.uint8))
    m = m.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    return np.asarray(m) > 0


def _candidates(mask: np.ndarray, x_lo: int, x_hi: int) -> list[tuple[int, int, int, int, int]]:
    """Mouth-sized, compact changed regions inside columns [x_lo, x_hi): (area, x0, y0, x1, y1)."""
    sh, sw = mask.shape
    sub = np.zeros_like(mask)
    sub[:, x_lo:x_hi] = mask[:, x_lo:x_hi]
    out = []
    for area, y0, x0, y1, x1 in _components(sub):
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        cy = (y0 + y1) / 2 / sh
        if not (0.00002 * sw * sh <= area <= 0.006 * sw * sh):
            continue
        if not (0.10 <= cy <= 0.80) or not (0.5 <= bw / bh <= 5.0):
            continue
        out.append((area, x0, y0, x1, y1))
    return out


def _eye_like(c, others) -> bool:
    """Eyes come in a pair: a similar-sized region at the same height, a little to the side.
    (An edit that opens mouths often redraws the eyes too; those must not be taken for a mouth.)"""
    area, x0, y0, x1, y1 = c
    cx, cy, bw, bh = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0 + 1, y1 - y0 + 1
    for o in others:
        if o is c:
            continue
        oa, ox0, oy0, ox1, oy1 = o
        ocx, ocy = (ox0 + ox1) / 2, (oy0 + oy1) / 2
        if abs(ocy - cy) < 0.6 * max(bh, oy1 - oy0 + 1) and 0 < abs(ocx - cx) < 5 * max(bw, ox1 - ox0 + 1) \
                and max(area, oa) / max(1, min(area, oa)) < 2.5:
            return True
    return False


def pick_mouth(cands: list, hint: tuple[float, float] | None, shape: tuple[int, int]):
    """The mouth among the candidates: the one nearest the vision hint (x, y in 0..1) when there is
    one close by; else not part of an eye pair, preferring the lowest (mouths sit below the eyes)
    and filled shapes. Returns (area, x0, y0, x1, y1) or None."""
    if not cands:
        return None
    sh, sw = shape
    if hint:
        hx, hy = hint[0] * sw, hint[1] * sh
        near = min(cands, key=lambda c: ((c[1] + c[3]) / 2 - hx) ** 2 + ((c[2] + c[4]) / 2 - hy) ** 2)
        d = (((near[1] + near[3]) / 2 - hx) ** 2 + ((near[2] + near[4]) / 2 - hy) ** 2) ** 0.5
        if d < 0.08 * sw:
            return near
    pool = [c for c in cands if not _eye_like(c, cands)]
    if not pool:
        return None           # only an eye pair changed: animating it would look wrong

    def score(c):
        area, x0, y0, x1, y1 = c
        fill = area / ((x1 - x0 + 1) * (y1 - y0 + 1))
        return (y1, area * (0.5 + fill))          # lowest first, then biggest filled
    if len(pool) > 1:
        lowest = max(pool, key=lambda c: c[4])
        # only prefer "lowest" among regions on the same face (within ~3 mouth widths vertically)
        same_face = [c for c in pool if lowest[4] - c[4] < 3 * (lowest[3] - lowest[1] + 1)]
        pool = same_face or pool
    return max(pool, key=score)


def detect_mouths(closed: Image.Image, opened: Image.Image, n_chars: int,
                  thresh: float = 45.0, scale: int = 4,
                  hints: list | None = None) -> list[list[int]]:
    """Mouth boxes [x0, y0, x1, y1] (closed-image pixels), left to right, one per character found.
    Characters stand in n_chars equal columns (left-to-right order is pinned in the prompt); each
    column is searched on its own. hints: optional per-character (x, y) mouth points in 0..1 from a
    vision model; when a hint has no changed region near it, a box is placed on the hint if the
    picture changed there at all."""
    w, h = closed.size
    mask = _change_mask(closed, opened, scale, thresh)
    sh, sw = mask.shape
    n = max(1, n_chars)
    boxes = []
    for k in range(n):
        hint = hints[k] if hints and k < len(hints) else None
        lo = int(max(0, (k - 0.15) * sw / n))
        hi = int(min(sw, (k + 1.15) * sw / n))
        best = pick_mouth(_candidates(mask, lo, hi), hint, (sh, sw))
        if best and hint:
            bx, by = (best[1] + best[3]) / 2 / sw, (best[2] + best[4]) / 2 / sh
            if abs(bx - hint[0]) > 0.08 or abs(by - hint[1]) > 0.06:
                best = None
        if best is None and hint:
            hw, hh = int(0.035 * sw), int(0.02 * sh)
            cx, cy = int(hint[0] * sw), int(hint[1] * sh)
            box_s = (max(0, cx - hw), max(0, cy - hh), min(sw - 1, cx + hw), min(sh - 1, cy + hh))
            if mask[box_s[1]:box_s[3] + 1, box_s[0]:box_s[2] + 1].mean() > 0.02:
                best = (0, *box_s)
        if not best:
            continue
        _, x0, y0, x1, y1 = best
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        # little room ABOVE the mouth (the eyes are there), more below for the jaw/chin
        pad_x, pad_up, pad_down = bw * 0.5 + 2, bh * 0.25 + 1, bh * 0.9 + 2
        box = [int(max(0, (x0 - pad_x) * scale)), int(max(0, (y0 - pad_up) * scale)),
               int(min(w, (x1 + 1 + pad_x) * scale)), int(min(h, (y1 + 1 + pad_down) * scale))]
        if not boxes or box != boxes[-1]:
            boxes.append(box)
    return boxes


def debug_overlay(closed: Image.Image, boxes: dict, dest: str) -> None:
    """The keyframe with each detected mouth outlined and labelled (saved for the run preview)."""
    from PIL import ImageDraw
    im = closed.convert("RGB").copy()
    d = ImageDraw.Draw(im)
    for name, (x0, y0, x1, y1) in boxes.items():
        d.rectangle([x0, y0, x1, y1], outline=(0, 255, 0), width=6)
        d.text((x0, max(0, y0 - 24)), name, fill=(0, 255, 0))
    im.thumbnail((720, 1280))
    im.save(dest, "JPEG", quality=85)


def assign_mouths(boxes: list[list[int]], chars: list[str], width: int) -> dict[str, list[int]]:
    """Characters stand left to right in `chars` order. With one box per character, map in order;
    with fewer, give each box to the character whose expected position is nearest."""
    if not boxes or not chars:
        return {}
    if len(boxes) == len(chars):
        return dict(zip(chars, boxes))
    out = {}
    for b in boxes:
        cx = (b[0] + b[2]) / 2 / width
        k = min(range(len(chars)), key=lambda i: abs((i + 0.5) / len(chars) - cx))
        out.setdefault(chars[k], b)
    return out


def talking_frame(closed: Image.Image, opened: Image.Image, box: list[int] | None) -> Image.Image:
    """The closed picture with ONE mouth from the open picture: only the pixels inside the mouth box
    that really changed (grown a little so the lips come along), with soft edges. Pixels the edit
    left alone stay exactly as they were, so nothing ghosts around the mouth."""
    if not box:
        return closed
    x0, y0, x1, y1 = box
    a = np.asarray(closed.crop(box), dtype=np.int16)
    b = np.asarray(opened.crop(box), dtype=np.int16)
    changed = (np.abs(a - b).max(axis=2) > 30).astype(np.uint8) * 255
    grow = max(3, int(min(x1 - x0, y1 - y0) * 0.08))
    m = Image.fromarray(changed).filter(ImageFilter.MaxFilter(2 * (grow // 2) + 1))
    # fade towards the box edges so the patch never shows a hard border
    edge = Image.new("L", m.size, 0)
    f = max(2, int(min(m.size) * 0.12))
    edge.paste(255, (f, f, m.size[0] - f, m.size[1] - f))
    edge = edge.filter(ImageFilter.GaussianBlur(f))
    local = Image.fromarray((np.asarray(m, dtype=np.float32) * np.asarray(edge, dtype=np.float32) / 255).astype(np.uint8))
    local = local.filter(ImageFilter.GaussianBlur(max(1, grow // 2)))
    alpha = Image.new("L", closed.size, 0)
    alpha.paste(local, (x0, y0))
    return Image.composite(opened, closed, alpha)


def mouth_states(track, t0: float, n: int, fps: int) -> list[bool]:
    """True = mouth open, per frame, with a minimum hold so flaps never jitter."""
    raw = [track.shape_at(t0 + k / fps) in OPEN_SHAPES for k in range(n)] if track else [False] * n
    out, hold = [], 0
    for k, s in enumerate(raw):
        if out and s != out[-1] and hold < MIN_HOLD_FRAMES:
            s = out[-1]
        hold = hold + 1 if out and s == out[-1] else 1
        out.append(s)
    return out


def render_talking(closed_path: str, open_path: str, box: list[int] | None, dest: str, duration: float,
                   fps: int, focus: float, zoom: float, track, t0: float, size=(1080, 1920)) -> int:
    """Same camera move as animate_shots.local_motion (push-in toward `focus`) with mouth flaps.
    Returns how many frames show the open mouth."""
    closed = Image.open(closed_path).convert("RGB")
    W, H = size
    # work at a canvas that covers 9:16 at 2x the output, like local_motion
    scale = max(2 * W / closed.width, 2 * H / closed.height)
    cw, ch = int(round(closed.width * scale)), int(round(closed.height * scale))
    closed = closed.resize((cw, ch), Image.LANCZOS)
    opened = aligned_open(Image.open(closed_path).convert("RGB"), Image.open(open_path)).resize((cw, ch), Image.LANCZOS) \
        if open_path else closed
    if box:
        box = [int(round(v * scale)) for v in box]
    talk = talking_frame(closed, opened, box) if box else closed
    left, top = (cw - 2 * W) / 2, (ch - 2 * H) / 2         # centre-crop to 9:16 like local_motion
    n = max(2, int(round(duration * fps)) + fps)
    states = mouth_states(track, t0, n, fps) if box else [False] * n
    z0 = max(1.0, zoom - 0.06)
    yc = 0.5 - (zoom - 1.0) * 0.2
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", dest]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for k in range(n):
            z = z0 + (zoom - z0) * k / n
            vw, vh = 2 * W / z, 2 * H / z
            x0 = min(max(left + 2 * W * focus - vw / 2, left), left + 2 * W - vw)
            y0 = min(max(top + 2 * H * yc - vh / 2, top), top + 2 * H - vh)
            src = talk if states[k] else closed
            frame = src.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + vw, y0 + vh))
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed ({rc})")
    return sum(states)
