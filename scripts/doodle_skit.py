#!/usr/bin/env python3
"""
Free "doodle skit" renderer: the skit's own cast acted out by stick figures.

Used by animate_shots.py for every shot that has no AI keyframe (no image provider, or the
image failed). Unlike the generic doodle scenes, it draws THE SHOT ITSELF:
  - the characters in the shot, each with a cap in their own colour and their name above
    their head (same identity trick as the AI version), facing each other
  - the speaker's mouth lip-synced to the voice (lipsync.py: Rhubarb when installed, else
    the built-in text + loudness method), plus talking gestures that follow the loudness
  - listeners breathe, blink and react with the expression from the shot's acting note
  - a few background props picked from the location's description (sofa, window, desk...)
  - close-ups when the shot's framing asks for one

No network, no GPU, no paid service.

Manual preview after the TTS stage:
    python scripts/doodle_skit.py --niche config/niches/men_psychology.json --shot 0
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

from doodle_engine import (ACCENT, BLUE, GOLD, GREY, GROUND_Y, INK, LIGHT, PAPER, STAND, Canvas, Figure,
                           Pose, draw_figure, hip_y_standing, render_clip, wave)
from lipsync import MouthTrack
from skit_common import cast_of, label, locations_of

SKY = (214, 226, 238)
WOOD = (196, 160, 118)

COLOR_WORDS = {
    "navy": (44, 62, 120), "pink": (236, 150, 172), "orange": (236, 134, 52), "grey": (150, 150, 150),
    "gray": (150, 150, 150), "black": (40, 40, 44), "white": (238, 238, 232), "red": (214, 58, 48),
    "purple": (132, 78, 170), "blue": (66, 126, 196), "green": (70, 150, 90), "yellow": (232, 196, 60),
    "brown": (130, 90, 60), "maroon": (128, 36, 48),
}
FALLBACK_COLORS = [ACCENT, BLUE, GOLD, (70, 150, 90), (132, 78, 170), (236, 134, 52)]


# ── identity ──────────────────────────────────────────────────────────────────

def cap_color(look: str, idx: int) -> tuple:
    """Colour of the character's cap, read from their config description."""
    m = re.search(r"((?:light |dark |bright )?[a-z]+)(?:[- ][a-z]+)? baseball cap", look.lower())
    if m:
        words = m.group(0).replace("-", " ").split()
        for w in words:
            if w in COLOR_WORDS:
                col = COLOR_WORDS[w]
                if "light" in words:
                    col = tuple(int(v + (255 - v) * 0.45) for v in col)
                return col
    return FALLBACK_COLORS[idx % len(FALLBACK_COLORS)]


def expression(emotion: str) -> str:
    e = emotion.lower()
    for keys, expr in ((("angry", "shout", "frustrat", "red-faced", "furious", "yell", "exasperat"), "angry"),
                       (("shock", "surpris", "stunned", "gasp"), "shocked"),
                       (("smirk", "amused", "unbothered", "sly", "teasing"), "smirk"),
                       (("happy", "proud", "smil", "laugh", "warm", "relieved", "gentle"), "happy"),
                       (("sad", "hurt", "tired", "defeat", "deflat", "quiet", "avoid", "ashamed"), "sad"),
                       (("worr", "nervous", "anxious", "whisper"), "worried")):
        if any(k in e for k in keys):
            return expr
    return "neutral"


# ── face with a talking mouth ────────────────────────────────────────────────

def _rot(px, py, deg):
    r = math.radians(deg)
    return px * math.cos(r) - py * math.sin(r), px * math.sin(r) + py * math.cos(r)


def draw_face(c: Canvas, head, r, s, tilt, facing, expr, mouth: str | None, blink: bool, col=INK):
    """Eyes/brows from the expression; the mouth is the lip-sync shape while talking."""
    look = 0.1 * facing

    def P(dx, dy):
        rx, ry = _rot((dx + look) * r, dy * r, tilt)
        return head[0] + rx, head[1] + ry

    lw = 5.5 * s
    for sx in (-0.34, 0.34):
        ex, ey = P(sx, -0.1)
        if blink:
            c.line([(ex - 7 * s, ey), (ex + 7 * s, ey)], lw * 0.7, col, 0.4)
        else:
            c.dot(ex, ey, 4.6 * s * (1.35 if expr == "shocked" else 1.0), col)
    if expr == "angry":
        for sx in (-1, 1):
            c.line([P(sx * 0.62, -0.5), P(sx * 0.14, -0.3)], lw * 1.1, ACCENT, 0.6)
    elif expr in ("sad", "worried"):
        for sx in (-1, 1):
            c.line([P(sx * 0.6, -0.4), P(sx * 0.18, -0.52)], lw * 0.8, col, 0.6)

    my = 0.42
    if mouth and mouth != "X":
        mx, mmy = P(0, my)
        ow = {"A": 0.30, "B": 0.26, "C": 0.30, "D": 0.32, "E": 0.20, "F": 0.13, "G": 0.26, "H": 0.26}[mouth] * r
        oh = {"A": 0.0, "B": 0.07, "C": 0.15, "D": 0.24, "E": 0.17, "F": 0.11, "G": 0.05, "H": 0.16}[mouth] * r
        if oh <= 0.01:
            c.line([P(-0.28, my), P(0.28, my)], lw, col, 0.5)
        else:
            pts = [(mx + ow * math.cos(a), mmy + oh * math.sin(a)) for a in [k * math.pi / 9 for k in range(18)]]
            c.poly(pts, fill=(70, 30, 34), w=lw * 0.8, color=col, amp=0.4)
            if mouth in ("B", "G"):
                c.line([(mx - ow * 0.7, mmy - oh * 0.2), (mx + ow * 0.7, mmy - oh * 0.2)], lw * 0.9, PAPER, 0.2)
            if mouth == "H":
                c.dot(mx, mmy - oh * 0.2, oh * 0.45, (220, 110, 120))
        return
    if expr == "happy":
        c.line([P(t * 0.38, my - 0.05 + 0.2 * (1 - t * t)) for t in (-1, -0.5, 0, 0.5, 1)], lw, col, 0.6)
    elif expr in ("sad", "worried"):
        c.line([P(t * 0.32, my + 0.12 - 0.16 * (1 - t * t)) for t in (-1, -0.5, 0, 0.5, 1)], lw, col, 0.6)
    elif expr == "shocked":
        c.circle(*P(0, my + 0.05), 0.12 * r, w=lw * 0.8, color=col, amp=0.4)
    elif expr == "smirk":
        c.line([P(-0.28, my), P(0.12, my + 0.02), P(0.36 * facing if facing > 0 else 0.36, my - 0.12)], lw, col, 0.5)
    elif expr == "angry":
        c.line([P(-0.3, my + 0.05), P(0.3, my + 0.05)], lw, col, 0.6)
    else:
        c.line([P(-0.26, my), P(0.26, my)], lw, col, 0.5)


def draw_cap(c: Canvas, head, r, s, tilt, facing, color, name: str):
    def P(dx, dy):
        rx, ry = _rot(dx * r, dy * r, tilt)
        return head[0] + rx, head[1] + ry

    dome = [P(math.cos(a) * 1.06, math.sin(a) * 1.06 - 0.06) for a in
            [math.pi + k * math.pi / 10 for k in range(11)]]
    c.poly(dome + [P(1.06, -0.3), P(-1.06, -0.3)], fill=color, w=8 * s, color=INK, amp=0.8)
    c.line([P(-0.2 * facing, -0.34), P(1.55 * facing, -0.3)], 11 * s, INK, 0.6)
    if name:
        top = P(0, -1.75)
        c.text(top[0], top[1], name, size=int(30 * max(1.0, s * 0.9)), color=INK)


# ── background props from the location description ───────────────────────────

def _window(c, x, t):
    c.rect(x - 70, 870, 140, 170, fill=SKY, w=7, color=GREY)
    c.line([(x, 870), (x, 1040)], 5, GREY)
    c.line([(x - 70, 955), (x + 70, 955)], 5, GREY)


def _sofa(c, x, t):
    c.rect(x - 170, 1330, 340, 120, fill=(150, 178, 140), w=8, color=GREY)
    c.rect(x - 190, 1420, 380, 120, fill=(150, 178, 140), w=8, color=GREY)


def _table(c, x, t):
    c.rect(x - 150, 1380, 300, 26, fill=WOOD, w=8, color=GREY)
    c.line([(x - 130, 1406), (x - 130, 1540)], 9, GREY)
    c.line([(x + 130, 1406), (x + 130, 1540)], 9, GREY)


def _clock(c, x, t):
    c.circle(x, 940, 58, w=7, color=GREY, fill=(250, 248, 240))
    a = t * 30
    c.line([(x, 940), (x + 38 * math.sin(math.radians(a)), 940 - 38 * math.cos(math.radians(a)))], 6, ACCENT)
    c.line([(x, 940), (x + 26, 940)], 7, GREY)


def _door(c, x, t):
    c.rect(x - 85, 1150, 170, 390, fill=(222, 206, 184), w=8, color=GREY)
    c.dot(x + 55, 1350, 9, GREY)


def _shelves(c, x, t):
    c.rect(x - 110, 960, 220, 580, fill=LIGHT, w=8, color=GREY)
    for y in (1100, 1240, 1380):
        c.line([(x - 110, y), (x + 110, y)], 6, GREY)
        for k in range(4):
            c.rect(x - 90 + k * 48, y - 70, 34, 70, fill=(200, 190, 220) if k % 2 else (230, 200, 160), w=4,
                   color=GREY)


def _whiteboard(c, x, t):
    c.rect(x - 150, 900, 300, 200, fill=(250, 250, 250), w=8, color=GREY)
    c.line([(x - 110, 960), (x + 60, 960)], 5, BLUE, 1.0)
    c.line([(x - 110, 1010), (x + 100, 1010)], 5, ACCENT, 1.0)


def _tree(c, x, t):
    c.line([(x, 1540), (x, 1200)], 22, (120, 90, 60))
    for dx, dy, r in ((0, 1100, 120), (-80, 1160, 85), (80, 1160, 85)):
        c.circle(x + dx, dy + 4 * wave(t, 0.3), r, w=6, color=GREY, fill=(150, 196, 140))


def _lamp(c, x, t):
    c.line([(x, 1540), (x, 960)], 10, GREY)
    c.circle(x, 940, 34, w=6, color=GREY, fill=(250, 226, 150))


def _car(c, x, t):
    c.rect(150, 1300, 780, 240, fill=(90, 90, 100), w=8, color=INK)
    c.circle(380, 1300, 95, w=14, color=INK)


def _counter(c, x, t):
    c.rect(x - 200, 1360, 400, 180, fill=WOOD, w=8, color=GREY)


def _plant(c, x, t):
    c.rect(x - 35, 1460, 70, 80, fill=(200, 120, 80), w=6, color=GREY)
    for a in (-35, -10, 15, 40):
        c.line([(x, 1460), (x + 90 * math.sin(math.radians(a)), 1460 - 110 * math.cos(math.radians(a)))],
               9, (80, 150, 90))


PROPS = [  # (keywords in the location description, drawer)
    (("window", "skyline", "glass"), _window), (("sofa", "couch"), _sofa),
    (("table", "desk"), _table), (("clock",), _clock), (("door", "porch", "washroom"), _door),
    (("shel", "rack", "cabinet", "filing"), _shelves), (("whiteboard",), _whiteboard),
    (("tree", "park"), _tree), (("lamp", "light"), _lamp), (("counter", "bar", "register"), _counter),
    (("plant",), _plant),
]


WALL = {_window, _clock, _whiteboard}


def props_for(desc: str) -> list:
    d = desc.lower()
    if "inside of an old car" in d or "car parked" in d:
        return [_car]
    found = [fn for keys, fn in PROPS if any(k in d for k in keys)]
    return found[:3]


# ── the shot ─────────────────────────────────────────────────────────────────

def layout(n: int) -> list[float]:
    return {1: [540], 2: [330, 750], 3: [250, 540, 830]}.get(n, [540])


def make_scene(niche: dict, shot: dict, t0: float, track: MouthTrack | None):
    cast = cast_of(niche)
    ids = [c for c in cast if c != "narrator"]
    chars = [c for c in shot["characters"] if c in cast and c != "narrator"] or ids[:1]
    speaker = shot["speaker"]
    close = "close" in shot.get("framing", "").lower()
    if close:   # one character, bigger: the speaker if visible, else the first named
        chars = [speaker] if speaker in chars else chars[:1]
    xs = layout(len(chars))
    s = 1.95 if close else {1: 1.45, 2: 1.38, 3: 1.18}.get(len(chars), 1.18)
    expr_speaker = expression(shot.get("emotion", ""))
    props = [] if close else props_for(locations_of(niche).get(shot["location"], ""))
    wall_x, floor_x = [540, 150, 930], [930, 150, 540]
    placed, wi, fi = [], 0, 0
    for fn in props:   # wall items hang between the heads, floor items stand at the sides
        if fn in WALL:
            placed.append((fn, wall_x[wi % 3])); wi += 1
        else:
            placed.append((fn, floor_x[fi % 3])); fi += 1
    colors = {cid: cap_color(cast[cid].get("look", ""), i) for i, cid in enumerate(ids)}
    angry = expr_speaker == "angry"

    def scene(c: Canvas, t: float, dur: float):
        gt = t0 + t
        for fn, px in placed:
            fn(c, px, t)
        c.line([(50, GROUND_Y), (1030, GROUND_Y)], 7, INK, 2.5)
        for i, cid in enumerate(chars):
            x = xs[i]
            if len(chars) == 1:
                facing = 1 if i % 2 == 0 else -1
            else:
                facing = 1 if x < 540 else -1 if x > 540 else (1 if speaker in chars and xs[chars.index(speaker)] > 540 else -1)
            is_speaker = cid == speaker
            loud = track.loudness(gt) if (track and is_speaker) else 0.0
            mouth = track.shape_at(gt) if (track and is_speaker) else None
            breathe = 3 * wave(t, 0.35, i)
            if is_speaker:
                swing = 22 * min(1.2, loud) * math.sin(t * 7.0 + i)
                pose = Pose(lean=4 + 3 * loud, head=-4 * loud + 2 * wave(t, 1.3),
                            la=(78 if angry else 30) + swing, le=(8 if angry else 55) + 0.5 * swing,
                            ra=-6 + 0.3 * swing, re=12)
                expr = expr_speaker
            else:
                pose = Pose(lean=1, head=3 * wave(t, 0.4, i), la=8, le=14, ra=-6, re=10)
                expr = "neutral" if expr_speaker != "angry" else "worried"
                if speaker == "narrator":
                    expr = expr_speaker if expr_speaker != "angry" else "neutral"
            fig = Figure(x, hip_y_standing(s) - breathe, s, facing, INK, expr, pose, faceless=True)
            j = draw_figure(c, fig)
            r = 46 * s
            tilt = (pose.lean + pose.head) * facing
            blink = (gt * 0.31 + i * 0.37) % 1.0 < 0.045
            draw_face(c, j["head"], r, s, tilt, facing, expr, mouth, blink)
            draw_cap(c, j["head"], r, s, tilt, facing, colors.get(cid, ACCENT), label(niche, cid))
        c.focus = (xs[0] if close else 540, 1250)

    return scene


def render_shot(niche: dict, shot: dict, t0: float, duration: float, dest: str, idx: int,
                track: MouthTrack | None) -> None:
    render_clip(make_scene(niche, shot, t0, track), duration + 1.0, dest, seed=idx + 1, zoom_per_sec=0.006)


def load_track(build: str = "build") -> MouthTrack | None:
    """Mouth timing for the whole voice track (build/voice.wav + build/word_timings.json)."""
    b = Path(build)
    words = []
    if (b / "word_timings.json").exists():
        words = [tuple(w) for w in json.loads((b / "word_timings.json").read_text())["words"]]
    wav = str(b / "voice.wav") if (b / "voice.wav").exists() else None
    if not words and not wav:
        return None
    dialog = (b / "script.txt").read_text(encoding="utf-8") if (b / "script.txt").exists() else None
    track = MouthTrack.build(wav, words, dialog)
    print(f"[doodle_skit] lip sync source: {track.source}")
    return track


def main() -> None:
    from skit_common import load_configs, load_script
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", required=True)
    ap.add_argument("--shot", type=int, default=0)
    ap.add_argument("--out", default="build/doodle_skit_preview.mp4")
    args = ap.parse_args()
    niche, _ = load_configs(args.niche)
    data = load_script()
    tl = json.loads(Path("build/skit_timeline.json").read_text())["lines"][args.shot]
    render_shot(niche, data["shots"][args.shot], tl["shot_start"], tl["shot_end"] - tl["shot_start"],
                args.out, args.shot, load_track())
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
