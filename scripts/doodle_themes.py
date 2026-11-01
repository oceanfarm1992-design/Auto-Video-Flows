#!/usr/bin/env python3
"""
Colour themes for the doodle engine, so every video does not look like the same template.

Scenes keep drawing with the canonical palette below (INK, PAPER, ACCENT ...). A Theme maps
each canonical colour to its own at draw time (Canvas._c), so one scene catalog renders as
cream paper, a school notebook, a chalkboard, a blueprint, neon at night, kraft paper or a
sticky note. Colours a scene writes as literal tuples pass through unchanged.

pick_theme() rotates by calendar day: the two series alternate days, and with an odd number
of themes each series still walks through all of them.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

# canonical palette (what the scenes are written in)
INK = (30, 30, 34)
ACCENT = (222, 70, 48)
BLUE = (66, 126, 196)
GOLD = (232, 182, 48)
GREY = (176, 172, 162)
LIGHT = (226, 222, 210)
PAPER = (248, 244, 232)
SKY = (214, 226, 238)


@dataclass(frozen=True)
class Theme:
    name: str
    bg: tuple
    colors: dict = field(default_factory=dict)   # canonical colour -> theme colour; PAPER must map to bg
    pattern: str = ""                            # "", "ruled", "grid" or "dots", drawn on the background
    pattern_color: tuple = (0, 0, 0)
    texture: bool = True                         # multiply the paper grain over the frame
    halo: tuple | None = None                    # dark outline under every stroke (for drawing over footage)


def _palette(ink, paper, accent, blue, gold, grey, light, sky) -> dict:
    return {INK: ink, PAPER: paper, ACCENT: accent, BLUE: blue, GOLD: gold,
            GREY: grey, LIGHT: light, SKY: sky}


CLASSIC = Theme("paper", PAPER)

THEMES = [
    CLASSIC,
    Theme("notebook", (250, 250, 246),
          _palette((26, 44, 108), (250, 250, 246), (214, 52, 60), (52, 120, 210), (238, 176, 30),
                   (150, 160, 182), (222, 228, 240), (206, 224, 246)),
          pattern="ruled", pattern_color=(168, 196, 228)),
    Theme("chalkboard", (40, 60, 52),
          _palette((238, 238, 230), (40, 60, 52), (255, 122, 108), (138, 192, 240), (250, 214, 110),
                   (150, 168, 160), (70, 94, 84), (58, 86, 92))),
    Theme("blueprint", (24, 70, 136),
          _palette((236, 244, 255), (24, 70, 136), (255, 206, 84), (140, 210, 255), (255, 206, 84),
                   (128, 166, 210), (44, 96, 164), (36, 86, 156)),
          pattern="grid", pattern_color=(52, 98, 164)),
    Theme("neon", (18, 18, 30),
          _palette((244, 244, 252), (18, 18, 30), (255, 64, 140), (64, 214, 255), (255, 214, 64),
                   (120, 120, 150), (44, 44, 66), (34, 30, 62)),
          pattern="dots", pattern_color=(40, 40, 62), texture=False),
    Theme("kraft", (204, 168, 122),
          _palette((44, 30, 22), (204, 168, 122), (176, 36, 28), (40, 86, 140), (248, 214, 120),
                   (150, 118, 84), (218, 188, 146), (196, 206, 200))),
    Theme("sticky", (255, 234, 136),
          _palette((34, 30, 30), (255, 234, 136), (214, 54, 42), (40, 108, 196), (240, 150, 30),
                   (196, 170, 90), (250, 222, 140), (236, 240, 214))),
]
THEMES_BY_NAME = {t.name: t for t in THEMES}

# For marks drawn straight over stock footage: white marker with a dark halo, so it reads on
# any picture. Heads are filled dark so the white face shows.
OVERLAY = Theme("overlay", (0, 0, 0),
                _palette((250, 250, 250), (36, 36, 42), (255, 214, 64), (120, 200, 255), (255, 214, 64),
                         (210, 210, 210), (70, 70, 80), (60, 60, 70)),
                texture=False, halo=(0, 0, 0))


def pick_theme(name: str | None = None, day: dt.date | None = None) -> Theme:
    """The named theme, else one per calendar day (UTC)."""
    if name:
        if name not in THEMES_BY_NAME:
            raise ValueError(f"unknown theme {name!r}; choose from {sorted(THEMES_BY_NAME)}")
        return THEMES_BY_NAME[name]
    day = day or dt.datetime.now(dt.timezone.utc).date()
    return THEMES[day.toordinal() % len(THEMES)]
