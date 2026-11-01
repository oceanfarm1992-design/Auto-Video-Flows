#!/usr/bin/env python3
"""
Procedural doodle / stick-figure animation engine.

No assets, no network, CPU only: everything is drawn with Pillow and encoded by
ffmpeg, so it works on a GitHub Actions runner and never depends on a stock
library. Scenes live in doodle_scenes.py; this module is the toolbox:

  Canvas        wobbly "marker" strokes, circles, arcs, polygons, text. A new
                Canvas per drawn frame (different wobble seed) gives the classic
                hand-drawn "boiling line" look at ANIM_FPS (12 fps) while the
                encoder duplicates frames up to 30 fps.
  Pose/Figure   a joint-angle stick man (torso, two arms, two legs, a face with
                expressions) that scenes pose and animate frame by frame.
  render_clip   run a scene function over time and encode it to an mp4.

Layout (matches assemble_video.py and generate_captions.py): the hook title card sits
at ~13% height for the first 4 s, karaoke captions at ~77% height (below the ground
line, on clear paper), CTA at ~87%. Scenes are drawn in a DESIGN coordinate system
(ground at GROUND_Y) that Canvas magnifies by VIEW_K so the figure fills the screen:
keep props inside the visible design region x 140-940, y 830-1540.

Manual preview:
    python scripts/doodle_engine.py --sheet build/doodle_sheet.png
    python scripts/doodle_engine.py --scene alone_window --duration 6 --out build/doodle_test.mp4
"""
from __future__ import annotations

import argparse
import math
import os
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass, fields, replace

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

W, H, SS = 1080, 1920, 2          # output size, supersample factor for anti-aliasing
ANIM_FPS = 12                     # drawn frames per second
OUT_FPS = 30
GROUND_Y = 1540                   # DESIGN ground line; scenes are drawn in this coordinate system
CX = W / 2
VIEW_K = 1.35                     # the design stage is magnified by this when drawn into the frame
VIEW_GROUND = 1290                # frame y that the design ground line lands on
# Visible design region: x 140-940, y ~830-1540 (a 800 x 710 stage). Props must stay inside it.

from doodle_themes import (  # noqa: E402  canonical palette, re-exported for the scenes
    ACCENT, BLUE, CLASSIC, GOLD, GREY, INK, LIGHT, PAPER, Theme,
)

CAMERA_MOVES = ("push", "pull", "pan_left", "pan_right", "punch", "drift")

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/segoeprb.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
_font_cache: dict[int, ImageFont.FreeTypeFont] = {}


def font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _font_cache:
        for path in _FONT_PATHS:
            if os.path.exists(path):
                _font_cache[size] = ImageFont.truetype(path, size * SS)
                break
        else:
            _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


# ------------------------------------------------------------------------ easing
def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease(t: float) -> float:
    t = clamp(t)
    return t * t * (3 - 2 * t)


def pop(t: float) -> float:
    """0 -> 1 with a small overshoot, for props appearing."""
    t = clamp(t)
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def wave(t: float, freq: float = 1.0, phase: float = 0.0) -> float:
    return math.sin(2 * math.pi * freq * t + phase)


# ------------------------------------------------------------------------ canvas
_texture: Image.Image | None = None


def paper_texture() -> Image.Image:
    """Subtle blotchy paper tone + fine grain as an RGB image to multiply onto frames."""
    global _texture
    if _texture is None:
        rng = np.random.default_rng(7)
        low = rng.random((H // 90 + 2, W // 90 + 2)).astype(np.float32)
        low_img = Image.fromarray((low * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
        blotch = np.asarray(low_img, dtype=np.float32) / 255.0
        grain = rng.normal(0.0, 0.006, (H, W)).astype(np.float32)
        tone = np.clip((0.955 + 0.045 * blotch + grain) * 255.0, 0, 255).astype(np.uint8)
        _texture = Image.fromarray(np.stack([tone] * 3, axis=-1))
    return _texture


class Canvas:
    """One drawn frame. Scenes draw in design coordinates (see _sp) with the canonical
    palette; the theme recolours every stroke. transparent=True draws on a clear RGBA
    layer, used for marks laid over stock footage."""

    def __init__(self, seed: int, theme: Theme | None = None, transparent: bool = False):
        self.theme = theme or CLASSIC
        self.transparent = transparent
        if transparent:
            self.img = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
        else:
            self.img = Image.new("RGB", (W * SS, H * SS), self.theme.bg)
        self.d = ImageDraw.Draw(self.img)
        rng = random.Random(seed)
        self.ph1 = rng.uniform(0, 6.28)
        self.ph2 = rng.uniform(0, 6.28)
        self._focus = (W / 2, 1000.0)     # zoom centre in FRAME pixels
        if not transparent and self.theme.pattern:
            self._pattern(self.theme.pattern, self.theme.pattern_color)

    def _c(self, color):
        """Canonical palette colour -> this theme's colour (literal colours pass through)."""
        return None if color is None else self.theme.colors.get(color, color)

    def _pattern(self, kind: str, color):
        step, lw = 64 * SS, 2 * SS
        if kind == "ruled":
            for y in range(3 * step, H * SS, step):
                self.d.line([(0, y), (W * SS, y)], fill=color, width=lw)
            self.d.line([(120 * SS, 0), (120 * SS, H * SS)], fill=(232, 150, 150), width=lw)
        elif kind == "grid":
            for x in range(0, W * SS, step):
                self.d.line([(x, 0), (x, H * SS)], fill=color, width=lw)
            for y in range(0, H * SS, step):
                self.d.line([(0, y), (W * SS, y)], fill=color, width=lw)
        elif kind == "dots":
            r = 3 * SS
            for x in range(step // 2, W * SS, step):
                for y in range(step // 2, H * SS, step):
                    self.d.ellipse((x - r, y - r, x + r, y + r), fill=color)

    @property
    def focus(self):
        return self._focus

    @focus.setter
    def focus(self, design_xy):
        """Scenes give the zoom centre in design coordinates."""
        px, py = self._sp(design_xy)
        self._focus = (px / SS, py / SS)

    # -- helpers
    @staticmethod
    def _sp(p):
        """Design coordinates -> supersampled frame pixels (stage magnification)."""
        return ((CX + (p[0] - CX) * VIEW_K) * SS, (VIEW_GROUND + (p[1] - GROUND_Y) * VIEW_K) * SS)

    def _boil(self, pts, amp):
        return [(x + amp * math.sin(self.ph1 + i * 1.9 + y * 0.021),
                 y + amp * math.cos(self.ph2 + i * 2.3 + x * 0.017))
                for i, (x, y) in enumerate(pts)]

    @staticmethod
    def _subdivide(pts, step=45.0):
        out = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            n = max(1, int(math.dist(a, b) // step))
            for k in range(1, n + 1):
                out.append((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n))
        return out

    def _stroke(self, sp, width, color):
        width = max(1, int(width * VIEW_K * SS))
        passes = [(width + 6 * SS, self.theme.halo)] if self.theme.halo else []
        for wd, col in passes + [(width, self._c(color))]:
            self.d.line(sp, fill=col, width=wd, joint="curve")
            r = wd / 2
            for x, y in (sp[0], sp[-1]):
                self.d.ellipse((x - r, y - r, x + r, y + r), fill=col)

    def _fill(self, sp, fill):
        if fill is not None:
            self.d.polygon(sp, fill=self._c(fill))

    # -- primitives
    def line(self, pts, w=10, color=INK, amp=2.0, closed=False):
        pts = list(pts)
        if closed:
            pts.append(pts[0])
        sp = [self._sp(p) for p in self._boil(self._subdivide(pts), amp)]
        self._stroke(sp, w, color)

    def circle(self, cx, cy, r, w=9, color=INK, fill=None, amp=1.8):
        n = max(18, int(r / 2.5))
        pts = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
               for i in range(n)]
        sp = [self._sp(p) for p in self._boil(pts, amp)]
        self._fill(sp, fill)
        if w:
            self._stroke(sp + [sp[0]], w, color)

    def dot(self, x, y, r, color=INK):
        px, py = self._sp((x, y))
        rr = r * VIEW_K * SS
        if self.theme.halo:
            h = rr + 3 * SS
            self.d.ellipse((px - h, py - h, px + h, py + h), fill=self.theme.halo)
        self.d.ellipse((px - rr, py - rr, px + rr, py + rr), fill=self._c(color))

    def arc(self, cx, cy, r, a0, a1, w=9, color=INK, amp=1.5):
        n = max(8, int(abs(a1 - a0) / 6))
        pts = [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
                cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]
        self.line(pts, w, color, amp)

    def poly(self, pts, fill=None, w=8, color=INK, amp=1.8):
        sp = [self._sp(p) for p in self._boil(self._subdivide(list(pts) + [pts[0]], 60)[:-1], amp)]
        self._fill(sp, fill)
        if w:
            self._stroke(sp + [sp[0]], w, color)

    def rect(self, x, y, w_, h_, fill=None, w=8, color=INK, amp=1.8):
        self.poly([(x, y), (x + w_, y), (x + w_, y + h_), (x, y + h_)], fill, w, color, amp)

    def text(self, x, y, s, size=60, color=INK, anchor="mm", angle=0.0, box=None):
        """Hand-lettered text; box = a canonical colour to draw a marker highlight behind it."""
        px, py = self._sp((x, y))
        fnt = font(int(size * VIEW_K))
        color = self._c(color)
        halo = {"stroke_width": 4 * SS, "stroke_fill": self.theme.halo} if self.theme.halo and box is None else {}
        if not angle and box is None:
            self.d.text((px, py), s, font=fnt, fill=color, anchor=anchor, **halo)
            return
        # rotated / boxed hand-lettered text: draw on a transparent layer, rotate, paste centred
        bb = self.d.textbbox((0, 0), s, font=fnt, anchor="lt", **halo)
        pad = 20 * SS
        layer = Image.new("RGBA", (bb[2] + 2 * pad, bb[3] + 2 * pad), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        if box is not None:
            ld.rounded_rectangle((pad * 0.4, pad * 0.6, layer.width - pad * 0.4, layer.height - pad * 0.4),
                                 radius=pad, fill=self._c(box) + (235,))
        ld.text((pad, pad), s, font=fnt, fill=color + (255,), anchor="lt", **halo)
        if angle:
            layer = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
        self.img.paste(layer, (int(px - layer.width / 2), int(py - layer.height / 2)), layer)

    def paste(self, im: Image.Image, design_box):
        """Paste a picture (e.g. a stock-footage frame) into a design-coordinate box; the
        image must already be the box's supersampled size (see box_px)."""
        x0, y0, _, _ = self.box_px(design_box)
        self.img.paste(im.convert(self.img.mode), (x0, y0))

    @classmethod
    def box_px(cls, design_box) -> tuple[int, int, int, int]:
        (x0, y0), (x1, y1) = cls._sp(design_box[:2]), cls._sp(design_box[2:])
        return int(x0), int(y0), int(x1), int(y1)

    # -- output
    def to_frame(self, zoom: float = 1.0, pan: tuple = (0.0, 0.0)) -> Image.Image:
        im = self.img.reduce(SS)
        if zoom > 1.001:
            vw, vh = W / zoom, H / zoom
            x0 = clamp(self.focus[0] + pan[0] - vw / 2, 0, W - vw)
            y0 = clamp(self.focus[1] + pan[1] - vh / 2, 0, H - vh)
            im = im.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + vw, y0 + vh))
        if self.transparent or not self.theme.texture:
            return im
        return ImageChops.multiply(im, paper_texture())


# ------------------------------------------------------------------------ figure
@dataclass(frozen=True)
class Pose:
    """Angles in degrees. Limbs: 0 = hanging straight down, + = swung toward the
    way the figure faces, 180 = straight up. Elbow/knee are bends relative to the
    upper limb (elbow folds forward, knee folds the shin backward)."""
    lean: float = 0.0     # torso lean from vertical, + = forward
    head: float = 0.0     # head tilt on top of the lean
    la: float = 8.0       # near arm: shoulder angle
    le: float = 12.0      # near arm: elbow bend
    ra: float = -6.0      # far arm
    re: float = 10.0
    ll: float = 3.0       # near leg: hip angle
    lk: float = 0.0       # near leg: knee bend
    rl: float = -3.0
    rk: float = 0.0

    def mix(self, other: "Pose", t: float) -> "Pose":
        return Pose(**{f.name: lerp(getattr(self, f.name), getattr(other, f.name), t)
                       for f in fields(self)})


STAND = Pose()
SIT = Pose(ll=88, lk=88, rl=96, rk=95, la=28, le=50, ra=18, re=55)


@dataclass(frozen=True)
class Figure:
    x: float                 # hip position
    y: float
    s: float = 1.0
    facing: int = 1          # 1 = right, -1 = left
    color: tuple = INK
    expr: str = "neutral"
    pose: Pose = STAND
    faceless: bool = False

    def with_pose(self, **kw) -> "Figure":
        return replace(self, pose=replace(self.pose, **kw))


TORSO, HEAD_R, UARM, FARM, THIGH, SHIN = 150, 46, 78, 74, 98, 96
STAND_HIP = SHIN + THIGH      # hip height above the feet at s = 1


def hip_y_standing(s: float) -> float:
    return GROUND_Y - STAND_HIP * s


def hip_y_sitting(s: float) -> float:
    return GROUND_Y - SHIN * s


def _dir(angle_deg: float, facing: int):
    r = math.radians(angle_deg)
    return (math.sin(r) * facing, math.cos(r))


def _add(p, d, length):
    return (p[0] + d[0] * length, p[1] + d[1] * length)


def _rot(px, py, deg):
    r = math.radians(deg)
    return px * math.cos(r) - py * math.sin(r), px * math.sin(r) + py * math.cos(r)


def draw_figure(c: Canvas, fig: Figure) -> dict:
    """Draw a stick man; return key joint positions so scenes can attach props."""
    p, s, f = fig.pose, fig.s, fig.facing
    w = 11 * s
    hip = (fig.x, fig.y)
    tdir = (math.sin(math.radians(p.lean)) * f, -math.cos(math.radians(p.lean)))
    neck = _add(hip, tdir, TORSO * s)
    shoulder = _add(hip, tdir, TORSO * 0.9 * s)
    hd = (math.sin(math.radians(p.lean + p.head)) * f, -math.cos(math.radians(p.lean + p.head)))
    head = _add(neck, hd, (HEAD_R + 9) * s)

    def leg(a, k):
        knee = _add(hip, _dir(a, f), THIGH * s)
        return knee, _add(knee, _dir(a - k, f), SHIN * s)

    def arm(a, e):
        elbow = _add(shoulder, _dir(a, f), UARM * s)
        return elbow, _add(elbow, _dir(a + e, f), FARM * s)

    rknee, rfoot = leg(p.rl, p.rk)
    lknee, lfoot = leg(p.ll, p.lk)
    relbow, rhand = arm(p.ra, p.re)
    lelbow, lhand = arm(p.la, p.le)

    col = fig.color
    c.line([hip, rknee, rfoot], w, col)
    c.line([hip, lknee, lfoot], w, col)
    c.line([hip, neck], w, col)
    c.line([shoulder, relbow, rhand], w, col)
    c.line([shoulder, lelbow, lhand], w, col)
    c.circle(head[0], head[1], HEAD_R * s, w=w * 0.9, color=col, fill=PAPER)
    if not fig.faceless:
        _face(c, head, HEAD_R * s, fig.expr, (p.lean + p.head) * f, col, s)
    return {"hip": hip, "neck": neck, "shoulder": shoulder, "head": head,
            "lhand": lhand, "rhand": rhand, "lelbow": lelbow, "relbow": relbow,
            "lfoot": lfoot, "rfoot": rfoot, "lknee": lknee, "rknee": rknee}


def _face(c: Canvas, head, r, expr, tilt, col, s):
    def P(dx, dy):
        rx, ry = _rot(dx * r, dy * r, tilt)
        return head[0] + rx, head[1] + ry

    ew = 4.6 * s
    big = expr in ("shocked",)
    for sx in (-0.36, 0.36):
        ex, ey = P(sx, -0.1)
        c.dot(ex, ey, ew * (1.35 if big else 1.0), col)

    my = 0.42
    lw = 5.5 * s
    if expr == "happy":
        c.line([P(t * 0.4, my - 0.06 + 0.22 * (1 - t * t)) for t in np.linspace(-1, 1, 9)], lw, col, 0.8)
    elif expr in ("sad", "worried"):
        c.line([P(t * 0.36, my + 0.1 + 0.2 * (1 - t * t) * -1) for t in np.linspace(-1, 1, 9)], lw, col, 0.8)
        for sx, sy in ((-1, 1), (1, 1)):
            b0, b1 = P(sx * 0.62, -0.42 - 0.12 * sy), P(sx * 0.18, -0.42 - 0.34 * sy)
            c.line([b0, b1], lw * 0.8, col, 0.6)
    elif expr == "angry":
        c.line([P(-0.34, my + 0.04), P(0.34, my + 0.04)], lw, col, 0.8)
        for sx in (-1, 1):
            c.line([P(sx * 0.66, -0.5), P(sx * 0.14, -0.3)], lw * 1.1, ACCENT, 0.6)
    elif expr == "shocked":
        c.circle(*P(0, my + 0.05), 0.12 * r, w=lw * 0.8, color=col, amp=0.5)
    elif expr == "smirk":
        c.line([P(-0.3, my), P(0.15, my + 0.02), P(0.4, my - 0.12)], lw, col, 0.6)
    else:
        c.line([P(-0.3, my), P(0.3, my)], lw, col, 0.6)


# ----------------------------------------------------------------------- encoding
def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found")


def camera(move: str, t: float, dur: float) -> tuple[float, tuple[float, float]]:
    """(zoom, pan offset in frame px) for a camera move at time t, so cuts don't all feel alike."""
    u = clamp(t / max(dur, 0.1))
    if move == "pull":
        return 1.14 - 0.12 * ease(u), (0.0, 0.0)
    if move in ("pan_left", "pan_right"):
        sign = -1 if move == "pan_left" else 1
        return 1.12, (sign * lerp(-50, 50, ease(u)), 0.0)
    if move == "punch":                       # quick snap-in, then a slow drift
        return 1.0 + 0.10 * pop(t / 0.35) + 0.01 * t, (0.0, 0.0)
    if move == "drift":
        return 1.06 + 0.02 * wave(t, 0.15), (18 * wave(t, 0.11), 12 * wave(t, 0.09, 1.0))
    return 1.0 + 0.02 * t, (0.0, 0.0)         # push


def ffmpeg_rgb_sink(out: str, size: tuple[int, int] = (W, H)) -> subprocess.Popen:
    """An ffmpeg process that encodes rgb24 frames written to its stdin at ANIM_FPS."""
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    cmd = [_ffmpeg(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", str(ANIM_FPS), "-i", "-",
           "-vf", f"fps={OUT_FPS}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-preset", "veryfast", "-crf", "20", "-movflags", "+faststart", out]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def finish(proc: subprocess.Popen):
    try:
        proc.stdin.close()
    except BrokenPipeError:
        pass                    # ffmpeg already stopped reading; its exit code below decides
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed with code {rc}")


def render_clip(scene, duration: float, out: str, seed: int = 0, theme: Theme | None = None,
                move: str = "push"):
    """Render scene(canvas, t, duration) for `duration` seconds into an mp4."""
    n = max(1, int(math.ceil(duration * ANIM_FPS)))
    proc = ffmpeg_rgb_sink(out)
    try:
        for i in range(n):
            t = i / ANIM_FPS
            canvas = Canvas(seed * 1000 + i, theme)
            scene(canvas, t, duration)
            zoom, pan = camera(move, t, duration)
            proc.stdin.write(canvas.to_frame(zoom, pan).tobytes())
    finally:
        finish(proc)


def contact_sheet(scenes: dict, out: str, at: float = 0.6, cols: int = 4, dur: float = 6.0,
                  theme: Theme | None = None):
    """One tile per scene at time at*dur, for eyeballing the catalog."""
    tw, th = W // 4, H // 4
    rows = math.ceil(len(scenes) / cols)
    sheet = Image.new("RGB", (cols * tw, rows * th), (255, 255, 255))
    for i, (name, fn) in enumerate(scenes.items()):
        c = Canvas(i, theme)
        fn(c, at * dur, dur)
        tile = c.to_frame(1.0).resize((tw, th), Image.LANCZOS)
        sheet.paste(tile, ((i % cols) * tw, (i // cols) * th))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    sheet.save(out)


def main():
    from doodle_scenes import SCENE_FUNCS
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", choices=sorted(SCENE_FUNCS))
    ap.add_argument("--duration", type=float, default=6.0)
    ap.add_argument("--out", default="build/doodle_test.mp4")
    ap.add_argument("--sheet", help="write a contact sheet PNG of every scene and exit")
    ap.add_argument("--at", type=float, default=0.6, help="time fraction for --sheet tiles")
    ap.add_argument("--theme", default="paper", help="colour theme (doodle_themes.THEMES)")
    ap.add_argument("--move", default="push", choices=CAMERA_MOVES)
    args = ap.parse_args()
    from doodle_themes import pick_theme
    theme = pick_theme(args.theme)
    if args.sheet:
        contact_sheet(SCENE_FUNCS, args.sheet, at=args.at, theme=theme)
        print(f"wrote {args.sheet}")
        return
    if not args.scene:
        ap.error("--scene or --sheet required")
    render_clip(SCENE_FUNCS[args.scene], args.duration, args.out, theme=theme, move=args.move)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
