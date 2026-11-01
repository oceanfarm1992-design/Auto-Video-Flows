"""Tests for the stock + doodle mix (scripts/doodle_mix.py, doodle_themes.py) and the camera moves.
Run from the repo root: python tests/test_mix.py"""
import datetime as dt
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from PIL import Image

import doodle_engine as E
import doodle_mix as M
import doodle_themes as T
import fetch_footage_multi as F

FAILED = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILED.append(name)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0


# themes
check("every theme maps PAPER to its background", all(t.colors.get(T.PAPER, t.bg) == t.bg for t in T.THEMES))
check("theme names are unique", len(T.THEMES_BY_NAME) == len(T.THEMES))
check("odd theme count so alternating series see every theme", len(T.THEMES) % 2 == 1)
days = [T.pick_theme(day=dt.date(2026, 11, 1) + dt.timedelta(days=2 * k)).name for k in range(len(T.THEMES))]
check("one series (every other day) cycles through all themes", len(set(days)) == len(T.THEMES))
check("pick_theme by name", T.pick_theme("chalkboard").name == "chalkboard")
try:
    T.pick_theme("nope")
    check("unknown theme raises", False)
except ValueError:
    check("unknown theme raises", True)
c = E.Canvas(1, T.pick_theme("chalkboard"))
check("canvas recolours canonical ink", c._c(E.INK) == T.pick_theme("chalkboard").colors[E.INK])
check("literal colours pass through", c._c((1, 2, 3)) == (1, 2, 3))
check("themed background", c.to_frame().getpixel((5, 5))[1] < 90)

# layout and camera plans
for seed in range(30):
    plan = M.plan_layouts(10, seed)
    if plan[0] != "annotated" or plan[-1] != "doodle" or any(a == b for a, b in zip(plan, plan[1:])):
        check(f"layout plan rules (seed {seed}: {plan})", False)
        break
else:
    check("layout plan: stock hook, doodle close, no repeats side by side", True)
mixes = [M.plan_layouts(10, s) for s in range(30)]
check("plans use all three layouts", {x for p in mixes for x in p} == set(M.LAYOUTS))
check("plans differ between videos", len({tuple(p) for p in mixes}) > 5)
moves = M.plan_moves(10, 3)
check("camera plan: punch-in hook, no repeats", moves[0] == "punch" and all(a != b for a, b in zip(moves, moves[1:])))
bad = [mv for mv in E.CAMERA_MOVES
       if not all(1.0 <= E.camera(mv, t / 10, 4.0)[0] <= 1.2 for t in range(0, 61))]
check(f"camera zoom stays in 1.0-1.2 for every move ({bad})", not bad)

check("mood: sad", M.mood("He sits alone every night") == "sad")
check("mood: default", M.mood("The bus was late") == "neutral")

# rendering (local, no network)
with tempfile.TemporaryDirectory() as tmp:
    photo = os.path.join(tmp, "p.jpg")
    Image.new("RGB", (600, 900), (90, 140, 200)).save(photo)
    seg = {"text": "Nobody knows the real reason.", "label": "REAL REASON"}
    out = os.path.join(tmp, "framed.mp4")
    info = M.render_framed(photo, True, out, 1.0, seg, T.pick_theme("neon"), seed=2, start_s=6)
    check(f"framed clip renders ({info})", os.path.exists(out) and 1.8 < duration(out) < 2.4)
    out = os.path.join(tmp, "annot.mp4")
    info = M.render_annotated(photo, True, out, 1.0, seg, seed=4, start_s=6)
    check(f"annotated clip renders ({info})", os.path.exists(out) and 1.8 < duration(out) < 2.4)

    # no API keys -> no stock: the beat must fall back to a doodle, never fail
    for key in ("PEXELS_API_KEY", "PIXABAY_API_KEY"):
        os.environ.pop(key, None)
    info = F.mix_segment(0, seg, 1.0, tmp, "man alone", "framed",
                         {"theme": T.CLASSIC, "move": "pull"}, [], 0.0)
    check("no stock falls back to a doodle", info["layout"] == "doodle" and os.path.getsize(info["file"]) > 1000)

sys.exit(1 if FAILED else 0)
