"""Offline tests for lip sync (lipsync.py) and the free doodle-skit renderer (doodle_skit.py).

Run from the repo root:  python tests/test_lipsync.py
"""
import json
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import doodle_skit as ds  # noqa: E402
import lipsync as ls  # noqa: E402
import skit_common as sc  # noqa: E402

FAILED = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILED.append(name)


check("M/B/P close the lips", ls.word_shapes("mom")[0] == "A" and ls.word_shapes("bob")[-1] == "A")
check("vowels open the mouth", "D" in ls.word_shapes("cat") and "E" in ls.word_shapes("go"))
check("digraphs: th -> tongue, oo -> pucker", ls.word_shapes("the")[0] == "H" and "F" in ls.word_shapes("food"))
check("f/v -> teeth on lip", ls.word_shapes("five")[0] == "G")
check("punctuation and case ignored", ls.word_shapes("Hello!") == ls.word_shapes("hello"))

cues = ls.from_words([("hello", 0.5, 1.0), ("there", 1.6, 2.0)])
track = ls.MouthTrack(cues)
check("mouth rests before speech", track.shape_at(0.2) == "X")
check("mouth moves during a word", track.shape_at(0.6) != "X")
check("mouth rests in a pause between words", track.shape_at(1.3) == "X")
check("mouth rests after the last word", track.shape_at(2.5) == "X")

with tempfile.TemporaryDirectory() as tmp:
    wav = os.path.join(tmp, "v.wav")
    rate = 24000
    t = np.arange(int(rate * 2.0)) / rate
    sig = np.where((t > 0.5) & (t < 1.0), 0.5 * np.sin(2 * np.pi * 220 * t), 0.0)
    with wave.open(wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes((sig * 32767).astype(np.int16).tobytes())
    os.environ["RHUBARB_BIN"] = os.path.join(tmp, "missing")      # force the built-in method
    tr = ls.MouthTrack.build(wav, [("hello", 0.5, 1.0), ("there", 1.2, 1.8)])
    check("built-in method used when Rhubarb is absent", tr.source == "text")
    check("loud audio keeps the mouth moving", tr.shape_at(0.7) != "X")
    check("silent audio closes the mouth even mid-word", tr.shape_at(1.5) == "X")

niche, _ = sc.load_configs("config/niches/men_psychology.json")
cast = sc.cast_of(niche)
check("cap colour read from the description (navy)", ds.cap_color(cast["the_provider"]["look"], 0) == ds.COLOR_WORDS["navy"])
check("light colours are lightened", ds.cap_color("a light blue baseball cap", 0) != ds.COLOR_WORDS["blue"])
check("unknown cap colour falls back to the palette", ds.cap_color("a hat", 2) == ds.FALLBACK_COLORS[2])
check("acting notes map to faces", ds.expression("red-faced, shouting") == "angry"
      and ds.expression("quiet, avoiding") == "sad" and ds.expression("proud") == "happy")
check("props come from the location text", ds._sofa in ds.props_for(sc.locations_of(niche)["living_room"]))
check("car interior gets the car prop only", ds.props_for(sc.locations_of(niche)["car"]) == [ds._car])

shot = {"speaker": "the_wife", "line": "Hello there.", "emotion": "annoyed", "location": "kitchen",
        "characters": ["the_wife", "the_provider"], "framing": "medium two-shot"}
with tempfile.TemporaryDirectory() as tmp:
    out = os.path.join(tmp, "s.mp4")
    ds.render_shot(niche, shot, 0.4, 1.0, out, 0, track)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height:format=duration",
                            "-of", "json", out], capture_output=True, text=True).stdout
    info = json.loads(probe)
    check("doodle skit shot renders 1080x1920 for the shot length + 1 s",
          info["streams"][0]["width"] == 1080 and abs(float(info["format"]["duration"]) - 2.0) < 0.2)
    close = dict(shot, framing="close-up on The Wife")
    ds.render_shot(niche, close, 0.4, 0.5, out, 1, None)
    check("close-up renders without a mouth track", os.path.getsize(out) > 0)

sys.exit(1 if FAILED else 0)
