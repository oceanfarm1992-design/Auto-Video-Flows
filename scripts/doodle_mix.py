#!/usr/bin/env python3
"""
Mix real stock footage with the doodle style, so a video is neither all stick men nor
all stock.

Layouts (one per beat, chosen by plan_layouts):
  doodle     a stick-man scene from doodle_scenes, in the video's colour theme
  framed     the stock clip plays inside a hand-drawn TV, phone, photo or picture frame on
             the themed background, with a stick-man presenter or an audience reacting to it
  annotated  the stock clip fills the screen and white marker doodles are drawn over it:
             the key word in a highlight box, a circle drawing itself on, an arrow, sparks,
             a small commentator figure

Network-free: fetch_footage_multi.py downloads the stock file and hands it here.

Manual preview (any local clip or photo):
    python scripts/doodle_mix.py --stock some.mp4 --layout framed --out build/mix_test.mp4
"""
from __future__ import annotations

import argparse
import math
import random
import re
import subprocess

from PIL import Image, ImageOps

from doodle_engine import (
    ACCENT, ANIM_FPS, CAMERA_MOVES, GOLD, GREY, GROUND_Y, H, INK, LIGHT, OUT_FPS, PAPER, W,
    Canvas, Figure, Pose, _ffmpeg, clamp, draw_figure, ease, finish, hip_y_sitting,
    hip_y_standing, pop, render_clip, wave,
)
from doodle_scenes import draw_label, ground, star
from doodle_themes import OVERLAY, Theme

LAYOUTS = ("doodle", "framed", "annotated")
LAYOUT_WEIGHTS = {"doodle": 4, "framed": 3, "annotated": 3}
FRAME_STYLES = ("tv", "phone", "polaroid", "board")
OVERLAY_STYLES = ("ring", "commentator", "frame", "clean")
HOOK_CARD_SECONDS = 4.3          # the hook card owns the top of the screen until then

MOODS = [
    (r"\b(sad|alone|lonel|hurt|cry|lost|miss|empty|quiet|tired)", "sad"),
    (r"\b(angr|rage|fight|hate|furious|snap)", "angry"),
    (r"\b(never|secret|truth|actually|surpris|shock|nobody|real reason)", "shocked"),
    (r"\b(happy|love|smil|laugh|win|proud|joy)", "happy"),
    (r"\b(power|control|calm|respect|strateg|quietly wins)", "smirk"),
    (r"\b(worr|stress|afraid|fear|anxious|pressure)", "worried"),
]


def mood(text: str) -> str:
    low = text.lower()
    return next((expr for pattern, expr in MOODS if re.search(pattern, low)), "neutral")


def plan_layouts(n: int, seed: int) -> list[str]:
    """One layout per beat: the hook opens on real footage, the close is a doodle, and no two
    neighbouring beats share a layout."""
    rng = random.Random(seed)
    plan: list[str] = []
    for i in range(n):
        if i == 0:
            plan.append("annotated")
        elif i == n - 1:
            plan.append("doodle")
        else:
            # the beat before the closing doodle must not be a doodle itself
            options = [lay for lay in LAYOUTS if lay != plan[-1] and not (i == n - 2 and lay == "doodle")]
            plan.append(rng.choices(options, [LAYOUT_WEIGHTS[o] for o in options])[0])
    return plan


def plan_moves(n: int, seed: int) -> list[str]:
    """A camera move per beat: the hook snaps in, and no two neighbouring beats move alike."""
    rng = random.Random(seed ^ 0x5EED)
    moves: list[str] = []
    for i in range(n):
        if i == 0:
            moves.append("punch")
        else:
            moves.append(rng.choice([m for m in CAMERA_MOVES if m != moves[-1]]))
    return moves


# ----------------------------------------------------------------- stock frames
class StockFrames:
    """Frames of a stock clip (or a photo with a slow zoom), cover-cropped to `size`, one per
    drawn frame (ANIM_FPS). Frames must be requested in order."""

    def __init__(self, path: str, is_photo: bool, size: tuple[int, int], duration: float):
        self.size, self.duration = size, duration
        self.idx, self.last, self.proc, self.photo = -1, None, None, None
        if is_photo:
            self.photo = ImageOps.fit(Image.open(path).convert("RGB"), size, Image.LANCZOS)
            return
        w, h = size
        cmd = [_ffmpeg(), "-loglevel", "error", "-stream_loop", "-1", "-i", path, "-t", f"{duration + 0.5:.2f}",
               "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={ANIM_FPS}",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)

    def at(self, t: float) -> Image.Image:
        target = int(round(t * ANIM_FPS))
        if self.photo is not None:
            zoom = 1.0 + 0.10 * clamp(t / max(self.duration, 0.1))
            w, h = self.size
            vw, vh = w / zoom, h / zoom
            return self.photo.resize(self.size, Image.BILINEAR,
                                     box=((w - vw) / 2, (h - vh) / 2, (w + vw) / 2, (h + vh) / 2))
        nbytes = self.size[0] * self.size[1] * 3
        while self.idx < target:
            buf = self.proc.stdout.read(nbytes)
            if len(buf) < nbytes:          # clip ran out: hold the last frame
                break
            self.last = Image.frombytes("RGB", self.size, buf)
            self.idx += 1
        return self.last if self.last is not None else Image.new("RGB", self.size, (20, 20, 24))

    def close(self):
        if self.proc:
            self.proc.stdout.close()
            self.proc.kill()
            self.proc.wait()


# ----------------------------------------------------------------- framed layout
# picture window per frame style, in design coordinates (x0, y0, x1, y1)
WINDOWS = {
    "tv": (240, 880, 840, 1260),
    "phone": (400, 840, 710, 1340),
    "polaroid": (270, 850, 810, 1200),
    "board": (240, 860, 840, 1270),
}


def _frame_body(c: Canvas, style: str, box, t: float):
    """The object around the picture, drawn before the picture is pasted in."""
    x0, y0, x1, y1 = box
    if style == "tv":
        c.line([(540, y0 - 30), (480, y0 - 95)], 7)
        c.line([(540, y0 - 30), (610, y0 - 90 + 6 * wave(t, 0.7))], 7)
        c.line([(x0 + 90, y1 + 30), (x0 + 60, GROUND_Y)], 9)
        c.line([(x1 - 90, y1 + 30), (x1 - 60, GROUND_Y)], 9)
        c.rect(x0 - 34, y0 - 34, x1 - x0 + 68, y1 - y0 + 68, fill=LIGHT, w=10)
    elif style == "phone":
        c.rect(x0 - 26, y0 - 60, x1 - x0 + 52, y1 - y0 + 120, fill=INK, w=8)
    elif style == "polaroid":
        c.rect(x0 - 30, y0 - 30, x1 - x0 + 60, y1 - y0 + 140, fill=(252, 252, 248), w=7)
    else:
        c.rect(x0 - 32, y0 - 32, x1 - x0 + 64, y1 - y0 + 64, fill=(150, 98, 64), w=8)


def _frame_trim(c: Canvas, style: str, box, t: float):
    """Details drawn over the picture's edges."""
    x0, y0, x1, y1 = box
    c.rect(x0, y0, x1 - x0, y1 - y0, w=7)
    if style == "phone":
        c.rect(520, y0 - 40, 100, 16, fill=GREY, w=0)
        c.circle(570, y1 + 32, 16, w=5)
    elif style == "polaroid":
        for tx in (x0 + 40, x1 - 140):
            c.rect(tx, y0 - 52, 100, 40, fill=GOLD, w=0)
    elif style == "tv":
        c.dot(x1 - 20, y1 + 18, 7, ACCENT if int(t * 2) % 2 else GREY)


def _presenter(c: Canvas, box, expr: str, t: float, left: bool):
    """A stick man beside the screen pointing at it."""
    s = 0.95 if box[2] - box[0] < 400 else 0.8
    x = 230 if left else 850
    if box[2] - box[0] < 400:
        x = 260 if left else 820
    point = Pose(lean=4, la=118 + 8 * wave(t, 0.8), le=-6, ra=-8, re=14)
    j = draw_figure(c, Figure(x, hip_y_standing(s), s, 1 if left else -1, INK, expr, point))
    mark = {"shocked": "!", "angry": "!", "worried": "?", "sad": "...", "neutral": "?"}.get(expr)
    if mark:
        k = pop((t - 0.4) / 0.4)
        if k > 0.05:
            c.text(j["head"][0] + (40 if left else -40), j["head"][1] - 95, mark, size=int(70 * k), color=ACCENT)


def _audience(c: Canvas, t: float):
    """Two figures seen from behind, watching the screen."""
    for k, x in enumerate((420, 660)):
        pose = Pose(lean=-4, head=-8 + 5 * wave(t, 0.3, k), la=10, le=30, ra=-4, re=30,
                    ll=88, lk=88, rl=96, rk=95)
        draw_figure(c, Figure(x, hip_y_sitting(0.72), 0.72, 1 if k else -1, INK, "neutral", pose, faceless=True))


def render_framed(stock: str, is_photo: bool, dest: str, duration: float, segment: dict,
                  theme: Theme, seed: int, start_s: float, move: str = "push"):
    rng = random.Random(seed)
    style = rng.choice(FRAME_STYLES)
    crowd = style == "tv" and rng.random() < 0.5
    left = rng.random() < 0.5
    box = WINDOWS[style]
    x0, y0, x1, y1 = Canvas.box_px(box)
    frames = StockFrames(stock, is_photo, (x1 - x0, y1 - y0), duration + 1.0)
    expr, label = mood(segment.get("text", "")), str(segment.get("label", "") or "")

    def scene(c: Canvas, t: float, dur: float):
        ground(c)
        _frame_body(c, style, box, t)
        c.paste(frames.at(t), box)
        _frame_trim(c, style, box, t)
        if crowd:
            _audience(c, t)
        else:
            _presenter(c, box, expr, t, left)
        if start_s + t > HOOK_CARD_SECONDS:
            draw_label(c, label, t, dur)
        c.focus = (540, 1150)

    try:
        render_clip(scene, duration + 1.0, dest, seed=seed, theme=theme, move=move)
    finally:
        frames.close()
    return {"layout": "framed", "style": "audience" if crowd else style}


# -------------------------------------------------------------- annotated layout
RING_C, RING_RX, RING_RY = (540, 1290), 300, 215     # design coords: around the middle of the frame


def _ring(c: Canvas, t: float, tilt: float):
    k = ease((t - 0.25) / 0.6)
    if k <= 0.01:
        return
    sweep = 380 * k
    pts = []
    for i in range(int(8 + sweep / 6)):
        a = math.radians(-100 + sweep * i / max(1, int(8 + sweep / 6) - 1))
        x, y = RING_RX * math.cos(a) * (1 + 0.03 * i / 40), RING_RY * math.sin(a)
        ca, sa = math.cos(tilt), math.sin(tilt)
        pts.append((RING_C[0] + x * ca - y * sa, RING_C[1] + x * sa + y * ca))
    c.line(pts, 9, ACCENT, 1.2)


def _arrow(c: Canvas, t: float, flip: bool):
    """A curved marker arrow that draws itself from a top corner to the ring."""
    k = ease((t - 0.8) / 0.4)
    if k <= 0.01:
        return
    sx = -1 if flip else 1
    p0, p1, p2 = (540 + sx * 320, 860), (540 + sx * 360, 1000), (540 + sx * 250, 1100)
    pts = []
    for i in range(13):
        u = k * i / 12
        pts.append(((1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * p1[0] + u * u * p2[0],
                    (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * p1[1] + u * u * p2[1]))
    c.line(pts, 9, INK, 1.0)
    if k > 0.95:
        ang = math.atan2(pts[-1][1] - pts[-2][1], pts[-1][0] - pts[-2][0])
        tip = pts[-1]
        for da in (2.5, -2.5):
            c.line([tip, (tip[0] + 44 * math.cos(ang + da), tip[1] + 44 * math.sin(ang + da))], 9, INK, 0.6)


def _sparks(c: Canvas, t: float):
    for k, (x, y) in enumerate([(230, 870), (850, 1600), (870, 900), (210, 1650)]):
        size = 14 + 10 * max(0.0, wave(t, 0.9, k * 1.3))
        star(c, x, y, size)


def _commentator(c: Canvas, expr: str, t: float, left: bool):
    s, x = 0.82, (220 if left else 860)
    pose = Pose(lean=4, la=125 + 10 * wave(t, 0.8), le=-8, ra=-8, re=12)
    j = draw_figure(c, Figure(x, hip_y_standing(s), s, 1 if left else -1, INK, expr, pose))
    k = pop((t - 0.3) / 0.4)
    if k > 0.05:
        bx, by = j["head"][0] + (70 if left else -70), j["head"][1] - 120
        c.circle(bx, by, 52 * k, w=6, fill=PAPER)
        c.text(bx, by, "!" if expr in ("shocked", "angry") else "?", size=int(64 * k), color=GOLD)


def _overlay_drawer(style: str, segment: dict, start_s: float, rng: random.Random):
    expr, label = mood(segment.get("text", "")), str(segment.get("label", "") or "").upper()
    tilt, flip, left = rng.uniform(-0.12, 0.12), rng.random() < 0.5, rng.random() < 0.5

    def draw(c: Canvas, t: float):
        if style == "ring":
            _ring(c, t, tilt)
            _arrow(c, t, flip)
        elif style == "commentator":
            _commentator(c, expr, t, left)
        elif style == "frame":
            c.rect(172, 640, 736, 1310, w=8, amp=3.0)
            _sparks(c, t)
        if label and start_s + t > HOOK_CARD_SECONDS:
            k = pop((t - 0.2) / 0.35)
            if k > 0.02:
                c.text(540, 705, label, size=int(70 * min(1.0, k)), color=PAPER, angle=-3.0, box=GOLD)
    return draw


def render_annotated(stock: str, is_photo: bool, dest: str, duration: float, segment: dict,
                     seed: int, start_s: float):
    """Stock footage full screen with marker doodles composited over it (one ffmpeg pass)."""
    rng = random.Random(seed)
    style = rng.choice(OVERLAY_STYLES)
    draw = _overlay_drawer(style, segment, start_s, rng)
    dur = duration + 1.0
    n = max(1, int(math.ceil(dur * ANIM_FPS)))
    if is_photo:
        src = ["-i", stock]
        bg = (f"scale={int(W * 1.2)}:{int(H * 1.2)}:force_original_aspect_ratio=increase,"
              f"crop={int(W * 1.2)}:{int(H * 1.2)},"
              f"zoompan=z='min(zoom+0.0012,1.2)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
              f"d={int(dur * OUT_FPS) + 1}:fps={OUT_FPS}:s={W}x{H}")
    else:
        src = ["-stream_loop", "-1", "-i", stock]
        bg = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={OUT_FPS}"
    cmd = [_ffmpeg(), "-y", "-loglevel", "error", *src,
           "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(ANIM_FPS), "-i", "-",
           "-filter_complex",
           f"[0:v]{bg},setsar=1,eq=contrast=1.05:saturation=1.1[bg];[1:v]fps={OUT_FPS}[ov];"
           "[bg][ov]overlay=0:0:format=auto,format=yuv420p[v]",
           "-map", "[v]", "-t", f"{dur:.2f}", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-movflags", "+faststart", dest]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(n):
            c = Canvas(seed * 1000 + i, OVERLAY, transparent=True)
            draw(c, i / ANIM_FPS)
            try:
                proc.stdin.write(c.to_frame().tobytes())
            except BrokenPipeError:      # ffmpeg reached -t and stopped reading: the clip is complete
                break
    finally:
        finish(proc)
    return {"layout": "annotated", "style": style}


def main():
    from doodle_themes import pick_theme
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", required=True, help="a local video or photo")
    ap.add_argument("--layout", choices=("framed", "annotated"), default="framed")
    ap.add_argument("--theme", default="paper")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--duration", type=float, default=4.0)
    ap.add_argument("--text", default="He never tells anyone the real reason.")
    ap.add_argument("--label", default="THE REAL REASON")
    ap.add_argument("--out", default="build/mix_test.mp4")
    args = ap.parse_args()
    seg = {"text": args.text, "label": args.label}
    is_photo = args.stock.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    if args.layout == "framed":
        info = render_framed(args.stock, is_photo, args.out, args.duration, seg, pick_theme(args.theme),
                             args.seed, start_s=10.0)
    else:
        info = render_annotated(args.stock, is_photo, args.out, args.duration, seg, args.seed, start_s=10.0)
    print(f"wrote {args.out} {info}")


if __name__ == "__main__":
    main()
