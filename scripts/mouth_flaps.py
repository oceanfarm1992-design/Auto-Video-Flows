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


def detect_mouths(closed: Image.Image, opened: Image.Image, n_chars: int,
                  drop: float = 40.0, scale: int = 4) -> list[list[int]]:
    """Mouth boxes [x0, y0, x1, y1] (closed-image pixels), ordered left to right; at most
    n_chars. Works on a 1/scale copy for speed; boxes are padded to include lips and chin."""
    w, h = closed.size
    small = (w // scale, h // scale)
    a = closed.resize(small, Image.BILINEAR).filter(ImageFilter.GaussianBlur(1))
    b = opened.resize(small, Image.BILINEAR).filter(ImageFilter.GaussianBlur(1))
    darker = (_luma(a) - _luma(b)) > drop                       # the open mouth is a dark hole
    m = Image.fromarray((darker * 255).astype(np.uint8))
    m = m.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))   # drop thin shifted lines
    mask = np.asarray(m) > 0
    sh, sw = mask.shape
    cands = []
    for area, y0, x0, y1, x1 in _components(mask):
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        cy = (y0 + y1) / 2 / sh
        if not (0.00002 * sw * sh <= area <= 0.006 * sw * sh):
            continue
        if not (0.10 <= cy <= 0.80) or not (0.5 <= bw / bh <= 5.0):
            continue
        cands.append((area, x0, y0, x1, y1))
    cands = sorted(cands, reverse=True)[:max(1, n_chars)]
    boxes = []
    for _, x0, y0, x1, y1 in sorted(cands, key=lambda c: c[1]):
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        pad_x, pad_y = bw * 0.6 + 2, bh * 0.8 + 2
        boxes.append([int(max(0, (x0 - pad_x) * scale)), int(max(0, (y0 - pad_y) * scale)),
                      int(min(w, (x1 + 1 + pad_x) * scale)), int(min(h, (y1 + 1 + pad_y) * scale))])
    return boxes


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
    """The closed picture with one mouth patch from the open picture (feathered edges)."""
    if not box:
        return closed
    x0, y0, x1, y1 = box
    alpha = Image.new("L", closed.size, 0)
    feather = max(3, int(min(x1 - x0, y1 - y0) * 0.18))
    alpha.paste(255, (x0 + feather, y0 + feather, x1 - feather, y1 - feather))
    alpha = alpha.filter(ImageFilter.GaussianBlur(feather))
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
