#!/usr/bin/env python3
"""
Scene catalog for the doodle / stick-man engine (see doodle_engine.py).

A scene is a function  scene(canvas, t, dur)  that draws one frame at time t of a
clip that lasts dur seconds. The script writer (GPT) picks a scene id per
narration segment from SCENES below, so every visual is something the engine can
really draw and it always matches what is being said. pick_scene() is the keyword
fallback when a segment has no valid scene id.

Layout: scenes are drawn in DESIGN coordinates (ground at GROUND_Y = 1540) which the
engine magnifies, so keep everything inside x 150-930, y 830-1540. Karaoke captions
sit below the ground line, the hook card above the stage.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import replace

from doodle_engine import (
    ACCENT, BLUE, GOLD, GREY, GROUND_Y, INK, LIGHT, PAPER, SIT, STAND, W,
    Canvas, Figure, Pose, clamp, draw_figure, ease, hip_y_sitting, hip_y_standing,
    lerp, pop, wave,
)

SKY = (214, 226, 238)


# ------------------------------------------------------------------------ helpers
def ground(c: Canvas, scroll: float = 0.0):
    c.line([(50, GROUND_Y), (1030, GROUND_Y)], 7, INK, 2.5)
    for k in range(-1, 12):
        x = (k * 95 + scroll) % 1000 + 40
        c.line([(x, GROUND_Y + 20), (x + 34, GROUND_Y + 20)], 5, GREY, 1.2)


def standing(x, s=1.05, facing=1, expr="neutral", pose=STAND, bob=0.0, color=INK, faceless=False):
    return Figure(x, hip_y_standing(s) - bob, s, facing, color, expr, pose, faceless)


def sitting(x, s=1.05, facing=1, expr="neutral", pose=SIT, color=INK):
    return Figure(x, hip_y_sitting(s), s, facing, color, expr, pose)


def walk_pose(t, speed=0.9, swing=28.0, lean=5.0) -> Pose:
    ph = 2 * math.pi * speed * t
    sl = math.sin(ph)
    return Pose(lean=lean, la=8 - swing * sl, le=14 + 8 * abs(sl), ra=8 + swing * sl, re=14,
                ll=swing * sl, lk=max(0.0, 38 * math.sin(ph + 1.3)),
                rl=-swing * sl, rk=max(0.0, 38 * math.sin(ph + 1.3 + math.pi)))


def walk_bob(t, speed=0.9) -> float:
    return abs(math.sin(2 * math.pi * speed * t)) * 7


def draw_label(c: Canvas, text: str, t: float, dur: float):
    """One hand-lettered key word/phrase for the beat, popping in (retention device)."""
    if not text:
        return
    k = pop((t - 0.2) / 0.35)
    if k <= 0.02:
        return
    c.text(540, 905, text.upper(), size=int(70 * min(1.0, k)), color=ACCENT, angle=-3.5)


# -------------------------------------------------------------------------- props
def coin(c, x, y, r=34, label="$"):
    c.circle(x, y, r, w=6, fill=GOLD)
    c.text(x, y + 2, label, size=int(r * 1.1), color=INK)


def heart(c, x, y, size=40, color=ACCENT):
    pts = []
    for i in range(40):
        a = 2 * math.pi * i / 40
        hx = 16 * math.sin(a) ** 3
        hy = -(13 * math.cos(a) - 5 * math.cos(2 * a) - 2 * math.cos(3 * a) - math.cos(4 * a))
        pts.append((x + hx * size / 16, y + hy * size / 16))
    c.poly(pts, fill=color, w=6)


def cloud(c, x, y, s=1.0, fill=LIGHT):
    blobs = [(-70, 10, 48), (-20, -18, 58), (40, -8, 50), (88, 14, 40), (10, 22, 52)]
    for dx, dy, r in blobs:
        c.circle(x + dx * s, y + dy * s, (r + 5) * s, w=0, fill=INK)
    for dx, dy, r in blobs:
        c.circle(x + dx * s, y + dy * s, (r - 3) * s, w=0, fill=fill)


def lightning(c, x, y, s=1.0, color=ACCENT):
    pts = [(0, 0), (-34, 64), (-4, 60), (-30, 130), (36, 42), (6, 46), (34, 0)]
    c.poly([(x + px * s, y + py * s) for px, py in pts], fill=color, w=5)


def rain(c, x, y, width, t, drops=9, length=34):
    for i in range(drops):
        dx = x + (i / (drops - 1) - 0.5) * width
        off = ((t * 380 + i * 97) % 300)
        c.line([(dx, y + off), (dx - 6, y + off + length)], 5, BLUE, 0.8)


def moon(c, x, y, r=46, bg=SKY):
    c.circle(x, y, r, w=0, fill=GOLD)
    c.circle(x + r * 0.42, y - r * 0.18, r * 0.88, w=0, fill=bg)


def star(c, x, y, size):
    c.line([(x - size, y), (x + size, y)], 4, GOLD, 0.5)
    c.line([(x, y - size), (x, y + size)], 4, GOLD, 0.5)


def sun(c, x, y, r=60, t=0.0):
    c.circle(x, y, r, w=7, fill=GOLD)
    for k in range(12):
        a = 2 * math.pi * k / 12 + t * 0.4
        c.line([(x + math.cos(a) * (r + 18), y + math.sin(a) * (r + 18)),
                (x + math.cos(a) * (r + 46), y + math.sin(a) * (r + 46))], 6, GOLD, 1.0)


def bulb(c, x, y, s=1.0, on=True):
    c.circle(x, y, 52 * s, w=7, fill=(255, 238, 150) if on else PAPER)
    c.rect(x - 24 * s, y + 46 * s, 48 * s, 34 * s, fill=LIGHT, w=6)
    if on:
        for k in range(9):
            a = math.radians(-160 + k * 18)
            c.line([(x + math.cos(a) * 74 * s, y + math.sin(a) * 74 * s),
                    (x + math.cos(a) * 104 * s, y + math.sin(a) * 104 * s)], 6, ACCENT, 1.0)


def clock(c, x, y, r, t, speed=1.0):
    c.circle(x, y, r, w=8, fill=PAPER)
    for k in range(12):
        a = 2 * math.pi * k / 12
        c.line([(x + math.cos(a) * r * 0.82, y + math.sin(a) * r * 0.82),
                (x + math.cos(a) * r * 0.94, y + math.sin(a) * r * 0.94)], 5, INK, 0.5)
    for length, rate, wd in ((r * 0.72, 2.2 * speed, 5), (r * 0.48, 0.18 * speed, 7)):
        a = t * rate - math.pi / 2
        c.line([(x, y), (x + math.cos(a) * length, y + math.sin(a) * length)], wd, ACCENT if wd == 5 else INK, 0.5)


def sweat(c, x, y, s=1.0):
    c.poly([(x, y - 22 * s), (x - 11 * s, y + 4 * s), (x, y + 14 * s), (x + 11 * s, y + 4 * s)],
           fill=BLUE, w=4, color=BLUE, amp=0.4)


def thought_bubble(c, x, y, w, h, tail_from, grow=1.0):
    wg, hg = w * grow, h * grow
    for k, (fx, fy, r) in enumerate([(0.2, 0.55, 9), (0.4, 0.28, 14)]):
        c.circle(tail_from[0] + (x - tail_from[0]) * fx, tail_from[1] + (y - tail_from[1]) * fy,
                 r * grow, w=5, fill=PAPER, amp=0.8)
    c.circle(x, y, 1, w=0)
    pts = [(x + wg / 2 * math.cos(2 * math.pi * i / 30), y + hg / 2 * math.sin(2 * math.pi * i / 30))
           for i in range(30)]
    c.poly(pts, fill=PAPER, w=7)


def x_mark(c, x, y, size, color=ACCENT):
    c.line([(x - size, y - size), (x + size, y + size)], 14, color, 1.0)
    c.line([(x + size, y - size), (x - size, y + size)], 14, color, 1.0)


def scribble(c, cx, cy, r, seed, w=6, color=ACCENT, n=26):
    rng = random.Random(seed)
    pts = []
    for _ in range(n):
        a = rng.uniform(0, 2 * math.pi)
        d = r * math.sqrt(rng.random())
        pts.append((cx + d * math.cos(a), cy + d * math.sin(a)))
    c.line(pts, w, color, 1.2)


def house(c, x, base_y, w=300, h=230, color=INK):
    c.rect(x - w / 2, base_y - h, w, h, fill=PAPER, w=8, color=color)
    c.poly([(x - w / 2 - 22, base_y - h), (x, base_y - h - 130), (x + w / 2 + 22, base_y - h)],
           fill=(238, 200, 180), w=8, color=color)
    c.rect(x - 34, base_y - 118, 68, 118, fill=LIGHT, w=6, color=color)
    c.rect(x + 70, base_y - 170, 56, 56, fill=SKY, w=6, color=color)
    c.rect(x - 126, base_y - 170, 56, 56, fill=SKY, w=6, color=color)


def mountain(c, x, base_y, w, h, snow=True):
    c.poly([(x - w / 2, base_y), (x, base_y - h), (x + w / 2, base_y)], fill=(206, 214, 214), w=8)
    if snow:
        c.poly([(x - w * 0.11, base_y - h * 0.78), (x, base_y - h), (x + w * 0.11, base_y - h * 0.78),
                (x + w * 0.04, base_y - h * 0.72), (x - w * 0.03, base_y - h * 0.8)],
               fill=PAPER, w=5)


def chess_king(c, x, base_y, s=1.0, color=INK):
    c.poly([(x - 46 * s, base_y), (x - 30 * s, base_y - 34 * s), (x + 30 * s, base_y - 34 * s),
            (x + 46 * s, base_y)], fill=(238, 232, 214), w=6, color=color)
    c.poly([(x - 24 * s, base_y - 34 * s), (x - 14 * s, base_y - 110 * s), (x + 14 * s, base_y - 110 * s),
            (x + 24 * s, base_y - 34 * s)], fill=(238, 232, 214), w=6, color=color)
    c.rect(x - 22 * s, base_y - 132 * s, 44 * s, 24 * s, fill=(238, 232, 214), w=6, color=color)
    c.line([(x, base_y - 186 * s), (x, base_y - 134 * s)], 7, color, 0.6)
    c.line([(x - 20 * s, base_y - 164 * s), (x + 20 * s, base_y - 164 * s)], 7, color, 0.6)


def crown(c, x, y, s=1.0, color=GOLD):
    pts = [(-52, 36), (-52, -22), (-26, 10), (0, -34), (26, 10), (52, -22), (52, 36)]
    c.poly([(x + px * s, y + py * s) for px, py in pts], fill=color, w=6)


def barbell(c, left, right, plates=2, plate_r=70):
    c.line([(left[0] - 90, left[1]), (right[0] + 90, right[1])], 11, INK, 1.0)
    for sx, base in ((-1, left), (1, right)):
        for i in range(plates):
            px = base[0] + sx * (105 + i * 26)
            c.rect(px - 12, base[1] - plate_r + i * 12, 24, 2 * (plate_r - i * 12), fill=GREY, w=6)


def wallet(c, x, y, s=1.0):
    c.rect(x - 56 * s, y - 38 * s, 112 * s, 76 * s, fill=(150, 98, 64), w=7)
    c.line([(x - 56 * s, y - 8 * s), (x + 56 * s, y - 8 * s)], 5, INK, 0.8)
    c.circle(x + 30 * s, y + 14 * s, 9 * s, w=0, fill=GOLD)


def shopping_bag(c, x, y, s=1.0):
    c.poly([(x - 60 * s, y - 40 * s), (x + 60 * s, y - 40 * s), (x + 74 * s, y + 70 * s), (x - 74 * s, y + 70 * s)],
           fill=(240, 214, 130), w=7)
    c.arc(x, y - 40 * s, 30 * s, 180, 360, 6)


def question(c, x, y, size=70, color=ACCENT):
    c.text(x, y, "?", size=size, color=color)


# ------------------------------------------------------------------------- scenes
def alone_window(c, t, dur):
    ground(c)
    c.rect(190, 880, 260, 330, fill=SKY, w=9)
    c.line([(320, 880), (320, 1210)], 6)
    c.line([(190, 1045), (450, 1045)], 6)
    moon(c, 385, 950, 34)
    for k, (sx, sy) in enumerate([(225, 925), (270, 985), (410, 1010), (230, 1110), (370, 1130)]):
        star(c, sx, sy, 5 + 4 * max(0.0, wave(t, 0.5, k * 1.1)))
    s, f = 1.05, -1
    hip = (690.0, hip_y_sitting(s))
    c.line([(hip[0] + 70, hip[1] + 16), (hip[0] - 80, hip[1] + 16)], 9)
    c.line([(hip[0] + 70, hip[1] + 16), (hip[0] + 70, GROUND_Y)], 9)
    c.line([(hip[0] - 80, hip[1] + 16), (hip[0] - 80, GROUND_Y)], 9)
    c.line([(hip[0] + 70, hip[1] + 16), (hip[0] + 70, hip[1] - 190)], 9)
    pose = replace(SIT, lean=20 + 2 * wave(t, 0.3), head=16, la=30, le=45, ra=22, re=50)
    j = draw_figure(c, Figure(hip[0], hip[1], s, f, INK, "sad", pose))
    for k in range(3):
        r = 7 + 5 * max(0.0, wave(t, 0.6, -k * 0.9))
        c.dot(j["head"][0] + f * (80 + k * 36), j["head"][1] - 70 - k * 22, r)
    c.focus = (560, 1250)


def work_late(c, t, dur):
    ground(c)
    moon(c, 850, 940, 32, PAPER)
    clock(c, 300, 960, 66, t * 1.4, 1.0)
    s = 1.05
    hip = (350.0, hip_y_sitting(s))
    c.line([(hip[0] - 70, hip[1] + 16), (hip[0] + 60, hip[1] + 16)], 9)
    c.line([(hip[0] - 70, hip[1] + 16), (hip[0] - 70, GROUND_Y)], 9)
    c.line([(hip[0] - 70, hip[1] + 16), (hip[0] - 70, hip[1] - 180)], 9)
    c.rect(540, 1330, 360, 22, fill=(180, 130, 90), w=7)
    c.line([(565, 1352), (565, GROUND_Y)], 9)
    c.line([(875, 1352), (875, GROUND_Y)], 9)
    c.poly([(610, 1330), (820, 1330), (795, 1245), (635, 1245)], fill=LIGHT, w=7)
    pose = replace(SIT, lean=14, head=10, la=62 + 6 * wave(t, 2.2), le=48, ra=58 + 6 * wave(t, 2.2, 1.5), re=50)
    draw_figure(c, Figure(hip[0], hip[1], s, 1, INK, "worried", pose))
    for k in range(3):
        c.text(420 + k * 60, 1100 - ((t * 50 + k * 30) % 90), "z" if k else "Z", size=34 + k * 6, color=GREY)


def carry_weight(c, t, dur):
    ground(c, scroll=-t * 90)
    s = 1.05
    pose = walk_pose(t, 0.7, 14, 10)
    pose = replace(pose, la=172, le=-8, ra=165, re=-6)
    fig = standing(470, s, 1, "worried", pose, walk_bob(t, 0.7) * 0.6)
    j = draw_figure(c, fig)
    top = j["head"][1] - 60
    bx, by, br = j["head"][0] + 25, top - 100, 112
    c.circle(bx, by, br, w=10, fill=(196, 196, 190))
    for a in (200, 250, 300):
        c.line([(bx + 30 * math.cos(math.radians(a)), by + 30 * math.sin(math.radians(a))),
                (bx + 80 * math.cos(math.radians(a + 20)), by + 80 * math.sin(math.radians(a + 20)))], 6, INK, 1.0)
    c.text(bx, by, "!", size=100, color=ACCENT)
    for k in range(3):
        sweat(c, j["head"][0] - 70 - 22 * k, j["head"][1] - 20 + ((t * 90 + k * 40) % 90), 1.0)
    c.focus = (560, 1150)


def give_more(c, t, dur):
    ground(c)
    s = 1.05
    giver = standing(270, s, 1, "happy", Pose(lean=4, la=80, le=8, ra=70, re=10), 0)
    j = draw_figure(c, giver)
    taker = standing(790, 0.8, -1, "happy", Pose(la=95, le=8, ra=85, re=8), 0)
    draw_figure(c, taker)
    for k in range(5):
        u = ((t * 0.55 + k * 0.2) % 1.0)
        x = lerp(j["lhand"][0], 710, u)
        y = lerp(j["lhand"][1], 1260, u) - math.sin(u * math.pi) * 200
        if k % 2:
            heart(c, x, y, 34)
        else:
            coin(c, x, y, 30)
    left = 6 - int(5 * ease(t / dur))
    for i in range(left):
        coin(c, 190 + (i % 3) * 36, GROUND_Y - 22 - (i // 3) * 18, 20)
    c.focus = (520, 1250)


def closed_wallet(c, t, dur):
    ground(c)
    s = 1.05
    pose = replace(STAND, la=70, le=40, head=10 * wave(t, 0.9))
    j = draw_figure(c, standing(330, s, 1, "neutral", pose, 0))
    wallet(c, j["lhand"][0] + 30, j["lhand"][1] - 10)
    bx, by = 620, 975
    thought_bubble(c, bx, by, 290, 210, (j["head"][0] + 40, j["head"][1] - 40), pop((t - 0.2) / 0.6))
    if t > 0.5:
        shopping_bag(c, bx, by + 6, 0.75 * pop((t - 0.5) / 0.5))
    if t > 1.0:
        x_mark(c, bx, by + 6, 56 * pop((t - 1.0) / 0.4))
    house(c, 800, GROUND_Y, 210, 160)
    heart(c, 800, 1250, 34 * (1 + 0.1 * wave(t, 1.4)))
    c.focus = (560, 1200)


def silent_hurt(c, t, dur):
    ground(c)
    s = 1.05
    j = draw_figure(c, standing(540, s, 1, "sad", replace(STAND, lean=3, head=8 + 2 * wave(t, 0.4)), 0))
    hx, hy = j["head"]
    c.line([(hx - 26, hy + 20), (hx + 26, hy + 20)], 8, INK, 0.5)
    for k in range(-2, 3):
        c.line([(hx + k * 10, hy + 14), (hx + k * 10, hy + 26)], 4, INK, 0.4)
    cloud(c, hx, hy - 235, 1.25, (190, 190, 196))
    rain(c, hx, hy - 180, 200, t)
    if (t * 2) % 1.6 < 0.35:
        lightning(c, hx + 70, hy - 215, 0.85)
    c.focus = (540, 1150)


def hide_stress(c, t, dur):
    ground(c)
    s = 1.1
    j = draw_figure(c, standing(540, s, 1, "happy", replace(STAND, la=5, ra=-5), 0))
    cx, cy = (j["hip"][0] + j["neck"][0]) / 2, (j["hip"][1] + j["neck"][1]) / 2
    pulse = 1 + 0.12 * wave(t, 1.2)
    for k in range(3):
        scribble(c, cx, cy, 52 * pulse, k * 17 + int(t * 6) % 5, 6, ACCENT)
    for k in range(3):
        sweat(c, j["head"][0] + 80 + 12 * k, j["head"][1] - 30 + ((t * 80 + k * 50) % 90), 1.0)
    c.text(540, 940, "I'm fine", size=70, color=GREY)
    c.focus = (540, 1200)


def provider_family(c, t, dur):
    ground(c)
    sun(c, 230, 940, 42, t)
    house(c, 790, GROUND_Y, 270, 200)
    for k, kx in enumerate((765, 835)):
        draw_figure(c, standing(kx, 0.46, -1, "happy", Pose(la=165 + 12 * wave(t, 1.6, k), le=0, ra=20), 0))
    u = ease(t / (dur * 0.7))
    x = lerp(200, 520, u)
    walking = u < 0.99
    pose = walk_pose(t, 0.9, 26, 6) if walking else STAND
    j = draw_figure(c, standing(x, 1.05, 1, "happy", pose, walk_bob(t, 0.9) if walking else 0))
    c.rect(j["lhand"][0] - 30, j["lhand"][1] - 4, 64, 46, fill=(150, 98, 64), w=6)
    c.focus = (600, 1250)


def chess_power(c, t, dur):
    ground(c)
    bx, by = 420, GROUND_Y - 130
    c.rect(bx, by, 360, 130, fill=(224, 196, 150), w=8)
    for r in range(2):
        for k in range(6):
            if (r + k) % 2 == 0:
                c.rect(bx + k * 60, by + r * 65, 60, 65, fill=(120, 84, 56), w=0)
    c.line([(bx + 50, by + 130), (bx + 50, GROUND_Y)], 9)
    c.line([(bx + 310, by + 130), (bx + 310, GROUND_Y)], 9)
    chess_king(c, 600, by + 6, 0.85)
    j = draw_figure(c, standing(250, 1.05, 1, "smirk", Pose(la=70, le=40, ra=-8, re=10), 0))
    crown(c, j["head"][0], j["head"][1] - 92 - 6 * wave(t, 1.0), 0.8 * pop(t / 0.6))
    for k, (px, py) in enumerate([(460, 0), (520, 63), (700, 0)]):
        c.circle(px + 28, by + 34 + py * 0.3, 14, w=6, fill=INK)
    c.focus = (520, 1200)


def calm_vs_angry(c, t, dur):
    ground(c)
    angry = standing(270, 0.95, 1, "angry",
                     Pose(lean=8, la=140 + 30 * wave(t, 2.4), le=20, ra=120 + 30 * wave(t, 2.4, 1.6), re=20,
                          ll=12 * wave(t, 2.4), rl=-12 * wave(t, 2.4)), abs(wave(t, 1.2)) * 12)
    j = draw_figure(c, angry)
    if (t * 3) % 1 < 0.6:
        lightning(c, j["head"][0] - 10, j["head"][1] - 235, 0.75)
    for k in range(4):
        a = math.radians(200 + k * 35)
        c.line([(j["head"][0] + math.cos(a) * 90, j["head"][1] + math.sin(a) * 90),
                (j["head"][0] + math.cos(a) * 130, j["head"][1] + math.sin(a) * 130)], 6, ACCENT, 1.0)
    calm_x = 730
    c.circle(calm_x, 1230, 190 + 8 * wave(t, 0.4), w=5, color=(240, 205, 120), fill=None)
    draw_figure(c, standing(calm_x, 1.12, -1, "happy", Pose(lean=0, la=14, le=70, ra=6, re=76), 2 * wave(t, 0.35)))
    c.focus = (640, 1250)


def think_idea(c, t, dur):
    ground(c)
    s = 1.05
    pose = Pose(lean=4, head=-10 + 4 * wave(t, 0.5), la=20, le=150, ra=-6, re=10)
    j = draw_figure(c, standing(370, s, 1, "worried" if t < dur * 0.45 else "happy", pose, 0))
    for k, (qx, qy) in enumerate([(590, 1010), (720, 930), (830, 1040)]):
        question(c, qx, qy + 8 * wave(t, 0.8, k), 74, ACCENT if t < dur * 0.45 else GREY)
    if t > dur * 0.45:
        bulb(c, j["head"][0] + 30, j["head"][1] - 175, 0.85 * pop((t - dur * 0.45) / 0.5))
    c.focus = (520, 1200)


def strong_lift(c, t, dur):
    ground(c)
    s = 1.05
    squat = max(0.0, wave(t, 0.5)) * 1.0
    pose = Pose(lean=0, la=170, le=5, ra=172, re=5, ll=26 * squat, lk=52 * squat, rl=-26 * squat, rk=52 * squat)
    hip_drop = 0.0
    j = draw_figure(c, standing(540, s, 1, "angry" if squat > 0.5 else "happy", pose, -48 * squat * s))
    barbell(c, j["lhand"], j["rhand"])
    for k in range(3):
        a = math.radians(-150 + k * 30)
        c.line([(j["head"][0] + math.cos(a) * 80, j["head"][1] + math.sin(a) * 80),
                (j["head"][0] + math.cos(a) * 118, j["head"][1] + math.sin(a) * 118)], 6, ACCENT, 1.0)
    c.focus = (540, 1100)


def walk_away(c, t, dur):
    horizon = 1010
    c.line([(0, horizon), (1080, horizon)], 6, INK, 2.0)
    c.poly([(420, horizon), (660, horizon), (1020, GROUND_Y + 120), (60, GROUND_Y + 120)], fill=LIGHT, w=6)
    c.line([(540, horizon), (540, GROUND_Y + 120)], 5, GREY, 1.0)
    c.circle(740, horizon - 30, 58, w=7, fill=GOLD)
    u = ease(t / dur)
    s = lerp(1.05, 0.38, u)
    x = lerp(330, 560, u)
    base = lerp(GROUND_Y, horizon + 30, u)
    pose = walk_pose(t, 1.1 + 0.4 * u, 24, 4)
    fig = Figure(x, base - 194 * s - walk_bob(t) * s, s, 1, INK, "sad", pose, faceless=s < 0.55)
    draw_figure(c, fig)
    c.focus = (540, 1150)


def lonely_crowd(c, t, dur):
    ground(c)
    for k in range(8):
        x = 190 + k * 100
        draw_figure(c, standing(x, 0.7, 1 if k % 2 else -1, "neutral",
                                Pose(la=8 + 4 * wave(t, 0.5, k), ra=-6), 2 * wave(t, 0.6, k),
                                color=GREY, faceless=True))
    c.circle(570, 1230, 230, w=5, color=ACCENT, fill=None)
    draw_figure(c, standing(570, 1.15, 1, "sad", replace(STAND, lean=6, head=12 + 2 * wave(t, 0.3)), 0))
    c.focus = (560, 1250)


def mountain_climb(c, t, dur):
    sun(c, 790, 930, 44, t)
    mountain(c, 570, GROUND_Y, 780, 650)
    mountain(c, 250, GROUND_Y, 400, 320)
    ground(c)
    path = [(230, GROUND_Y - 30), (470, GROUND_Y - 140), (380, GROUND_Y - 290), (590, GROUND_Y - 410),
            (500, GROUND_Y - 520), (570, GROUND_Y - 640)]
    c.line(path, 5, GREY, 1.0)
    u = clamp(ease(t / (dur * 0.9)))
    seg = u * (len(path) - 1)
    i = min(int(seg), len(path) - 2)
    p0, p1 = path[i], path[i + 1]
    x, y = lerp(p0[0], p1[0], seg - i), lerp(p0[1], p1[1], seg - i)
    fx = 1 if p1[0] >= p0[0] else -1
    draw_figure(c, Figure(x, y - 70, 0.5, fx, INK, "happy", walk_pose(t, 1.2, 22, 8)))
    if u > 0.97:
        c.line([(570, GROUND_Y - 640), (570, GROUND_Y - 730)], 6, INK, 0.5)
        c.poly([(570, GROUND_Y - 730), (640, GROUND_Y - 706), (570, GROUND_Y - 682)], fill=ACCENT, w=5)
    c.focus = (x, y)


def puppet_strings(c, t, dur):
    ground(c)
    s = 1.05
    sway = 6 * wave(t, 0.5)
    pose = Pose(lean=sway * 0.5, head=sway, la=40 + 16 * wave(t, 0.7), le=20, ra=30 + 16 * wave(t, 0.7, 2), re=20,
                ll=6 * wave(t, 0.7), lk=10, rl=-6 * wave(t, 0.7), rk=6)
    j = draw_figure(c, standing(540, s, 1, "neutral", pose, 0))
    bar_y = 640     # above the visible stage: the strings run off the top of the frame
    for key, bx in (("head", 540), ("lhand", 400), ("rhand", 680), ("lknee", 450), ("rknee", 630)):
        c.line([(bx, bar_y), (j[key][0], j[key][1] - (50 if key == "head" else 0))], 3, GREY, 0.8)
    c.focus = (540, 1150)


def mirror_self(c, t, dur):
    ground(c)
    c.rect(180, 900, 250, 630, fill=SKY, w=10)
    c.rect(198, 918, 214, 594, fill=(228, 238, 246), w=0)
    s = 1.0
    draw_figure(c, standing(305, s * 0.88, 1, "worried", replace(STAND, la=8), 0, color=BLUE))
    draw_figure(c, standing(700, s, -1, "worried", replace(STAND, la=8, head=6 * wave(t, 0.5)), 0))
    for k in range(3):
        question(c, 790 + 26 * k, 1010 - 40 * k + 8 * wave(t, 0.7, k), 60, ACCENT)
    c.focus = (540, 1250)


SCENES = {
    "alone_window": ("a man sits alone by a night window, thinking", alone_window),
    "work_late": ("a man working late at a desk while the clock spins", work_late),
    "carry_weight": ("a man walking while carrying a huge heavy weight (responsibility, burden)", carry_weight),
    "give_more": ("a man handing out coins and hearts to someone else while his own pile shrinks (giving, sacrifice)", give_more),
    "closed_wallet": ("a man holding a closed wallet, refusing to buy something for himself", closed_wallet),
    "silent_hurt": ("a man with a zipped mouth under a storm cloud (silence, hurting quietly)", silent_hurt),
    "hide_stress": ("a smiling man with a tangled scribble of stress in his chest (hiding stress)", hide_stress),
    "provider_family": ("a man walking home with a briefcase to his house and waving children (providing for family)", provider_family),
    "chess_power": ("a calm man with a crown beside a chess board and king piece (strategy, power)", chess_power),
    "calm_vs_angry": ("an angry man with lightning next to a calm glowing man (emotional control)", calm_vs_angry),
    "think_idea": ("a man thinking with question marks, then a lightbulb idea", think_idea),
    "strong_lift": ("a man lifting a heavy barbell overhead (strength, discipline)", strong_lift),
    "walk_away": ("a man walking away down a road toward the horizon (leaving, letting go)", walk_away),
    "lonely_crowd": ("one sad man standing apart from a crowd of grey faceless people (loneliness)", lonely_crowd),
    "mountain_climb": ("a tiny man climbing a winding mountain path to a flag (goals, perseverance)", mountain_climb),
    "puppet_strings": ("a man hanging on strings from a control bar (manipulation, influence)", puppet_strings),
    "mirror_self": ("a man facing his reflection with question marks (self-awareness, identity)", mirror_self),
}
SCENE_FUNCS = {k: v[1] for k, v in SCENES.items()}

KEYWORDS = [  # stems, matched as word prefixes so "buys", "giving", "provider" all hit
    (r"\b(giv|gave|sacrific|generous|shar)", "give_more"),
    (r"\b(buy|bought|spend|spent|wallet|money|afford|purchas|treat)", "closed_wallet"),
    (r"\b(silen|quiet|speak|spoke|words|talk|ask)", "silent_hurt"),
    (r"\b(stress|hide|hid|hidden|pretend|mask|fine|smile)", "hide_stress"),
    (r"\b(provid|family|home|child|kid|father|dad)", "provider_family"),
    (r"\b(power|control|strateg|respect|authorit|command)", "chess_power"),
    (r"\b(calm|emotion|anger|angry|react|patien|temper)", "calm_vs_angry"),
    (r"\b(manipul|influenc|pressure|persuad|puppet)", "puppet_strings"),
    (r"\b(alone|lonel|solitude|isolat)", "lonely_crowd"),
    (r"\b(burden|weigh|responsib|carr|duty|heavy)", "carry_weight"),
    (r"\b(strong|strength|disciplin|train|lift)", "strong_lift"),
    (r"\b(leav|left|walk away|detach|distance|let go|abandon)", "walk_away"),
    (r"\b(goal|climb|journey|persever|ambiti|purpose|dream)", "mountain_climb"),
    (r"\b(work|career|job|hustle|late|tired)", "work_late"),
    (r"\b(self|identit|mirror|reflect|aware)", "mirror_self"),
    (r"\b(think|mind|idea|learn|understand|psycholog|why)", "think_idea"),
]
DEFAULT_ROTATION = ["alone_window", "think_idea", "carry_weight", "calm_vs_angry"]


def scene_menu() -> str:
    return "\n".join(f'  - {sid}: {desc}' for sid, (desc, _) in SCENES.items())


def pick_scene(text: str, used: list[str] | None = None) -> str:
    """Keyword match on the narration text, avoiding scenes already used in this video."""
    used = used or []
    lowered = text.lower()
    for pattern, scene in KEYWORDS:
        if scene not in used and re.search(pattern, lowered):
            return scene
    for scene in DEFAULT_ROTATION + list(SCENES):
        if scene not in used:
            return scene
    return DEFAULT_ROTATION[0]
