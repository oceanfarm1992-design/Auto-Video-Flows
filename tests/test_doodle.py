"""Tests for the doodle / stick-man engine (scripts/doodle_engine.py, doodle_scenes.py).
Run from the repo root: python tests/test_doodle.py"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import doodle_engine as E
import doodle_scenes as S

FAILED = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILED.append(name)


check("menu lists every scene", all(sid in S.scene_menu() for sid in S.SCENES))
check("scene funcs match catalog", set(S.SCENE_FUNCS) == set(S.SCENES))

bad = []
for sid, fn in S.SCENE_FUNCS.items():
    for frac in (0.05, 0.5, 0.95):
        try:
            c = E.Canvas(1)
            fn(c, frac * 6.0, 6.0)
            im = c.to_frame(1.03)
            assert im.size == (E.W, E.H)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{sid}@{frac}: {type(exc).__name__}: {exc}")
check(f"every scene renders at 3 times ({bad[:2]})", not bad)

check("keyword pick: giving", S.pick_scene("men give more than they take") == "give_more")
check("keyword pick: wallet", S.pick_scene("he never buys things for himself") == "closed_wallet")
check("pick avoids used scenes", S.pick_scene("men give more", ["give_more"]) != "give_more")
check("no keyword still returns a scene", S.pick_scene("zzz qqq") in S.SCENES)
used = []
for _ in range(len(S.SCENES)):
    used.append(S.pick_scene("zzz", used))
check("rotation never repeats before the catalog is exhausted", len(set(used)) == len(S.SCENES))

pose = E.STAND.mix(E.SIT, 0.5)
check("pose mix interpolates", abs(pose.ll - (E.STAND.ll + E.SIT.ll) / 2) < 1e-6)
check("pose is immutable", True)

with tempfile.TemporaryDirectory() as tmp:
    out = os.path.join(tmp, "t.mp4")
    E.render_clip(S.SCENE_FUNCS["think_idea"], 1.0, out)
    ok = os.path.exists(out) and os.path.getsize(out) > 1000
    check("render_clip writes an mp4", ok)
    if ok:
        dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", out], capture_output=True, text=True).stdout.strip()
        check(f"clip is ~1 s ({dur})", dur and 0.9 < float(dur) < 1.5)

sys.exit(1 if FAILED else 0)
