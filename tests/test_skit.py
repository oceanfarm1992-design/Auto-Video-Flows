"""Offline tests for the cartoon-skit format (no network, no API keys).

Run from the repo root:  python tests/test_skit.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import animate_shots as an
import assemble_skit as asm
import generate_captions as gc
import generate_dialogue_tts as gd
import generate_skit as gs
import skit_common as sc

FAILED = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILED.append(name)


def shot(spk, line, loc, chars):
    return {"speaker": spk, "line": line, "emotion": "calm", "location": loc, "characters": chars,
            "framing": "medium two-shot", "action": "standing", "motion": "nods", "keyword": ""}


for niche_id in ("men_psychology", "power_psychology"):
    niche, skit = sc.load_configs(f"config/niches/{niche_id}.json")
    cast, locs = sc.cast_of(niche), list(sc.locations_of(niche))
    hero = niche["skit"]["hero"]
    other = next(c for c in cast if c not in (hero, "narrator"))

    check(f"{niche_id}: hero is in the cast", hero in cast)
    check(f"{niche_id}: narrator present with cloned voice first", cast["narrator"]["voice"].get("styletts2") is True)
    check(f"{niche_id}: every character has a cap label and look",
          all(c["label"] and c["look"] for k, c in cast.items() if k != "narrator"))
    check(f"{niche_id}: cap labels are unique",
          len({c["label"] for k, c in cast.items() if k != "narrator"}) == len(cast) - 1)
    check(f"{niche_id}: live format is skit", niche.get("format") == "skit")

    prompt = gs.build_prompt(niche, skit, niche["topics"][0], ["kw one"])
    check(f"{niche_id}: prompt lists cast, locations and topic",
          all(x in prompt for x in (hero, locs[0], niche["topics"][0], "narrator")))

    good = {"title": "HE NEVER BUYS ANYTHING", "shots": (
        [shot(hero if i % 2 else other, "Short natural line of dialogue right here, okay.", locs[0], [hero, other])
         for i in range(11)]
        + [shot("narrator", "This is what is really happening in his head and why.", locs[0], [hero])])}
    check(f"{niche_id}: valid skit passes", gs.validate(good, niche, skit) == [])

    bad = json.loads(json.dumps(good))
    bad["shots"][-1]["speaker"] = hero
    bad["shots"][2]["location"] = "moon_base"
    bad["shots"][3]["line"] = "We paid 400 dollars for it."
    bad["title"] = "WHY MEN DO THIS"
    issues = " | ".join(gs.validate(bad, niche, skit))
    check(f"{niche_id}: catches narrator, location, digits, title",
          all(x in issues for x in ("narrator", "moon_base", "digits", "WHY")))

    norm = gs.normalise(good, niche, niche["topics"][0])
    check(f"{niche_id}: normalise keeps one segment per shot",
          len(norm["segments"]) == len(norm["shots"]) == 12 and norm["format"] == "skit")
    check(f"{niche_id}: narration is every line joined",
          norm["narration"].count("Short natural line") == 11)

    sp = sc.shot_prompt(niche, skit, norm["shots"][0], ["the location", "the character X"])
    check(f"{niche_id}: shot prompt locks style and cap names",
          "cap" in sp and sc.label(niche, norm["shots"][0]["speaker"]) in sp and "9:16" in sp)
    sheet = sc.character_sheet_prompt(niche, skit, hero)
    check(f"{niche_id}: character sheet prints the cap label", cast[hero]["label"] in sheet)

check("normalise repairs unknown speaker/location", (lambda n: n["shots"][0]["speaker"] == "the_provider"
      and n["shots"][0]["location"] == "living_room")(
    gs.normalise({"title": "X", "shots": [shot("ghost", "Hi there.", "mars", [])]},
                 sc.load_configs("config/niches/men_psychology.json")[0], "t")))

check("fal duration covers the line", an.pick_duration(4.2, [5, 10]) == 5 and an.pick_duration(6.1, [5, 10]) == 10)
check("fal duration caps at the longest option", an.pick_duration(14, [5, 10]) == 10)
check("narrator tries the cloned voice first",
      gd.engines_for({"voice": {"styletts2": True, "openai": "onyx"}}, ["openai", "kokoro"])[0] == "styletts2")
check("character without kokoro voice skips kokoro",
      gd.engines_for({"voice": {"openai": "onyx"}}, ["openai", "kokoro", "espeak"]) == ["openai", "espeak"])

with tempfile.TemporaryDirectory() as tmp:
    tl = Path(tmp) / "tl.json"
    tl.write_text(json.dumps({"duration": 4, "lines": [
        {"line": "one two", "start": 0.0, "end": 1.0}, {"line": "three", "start": 2.0, "end": 3.0}]}))
    _, timed = gc.timeline_words(str(tl))
    check("timeline captions keep the pause between speakers", timed[2][1] == 2.0 and timed[1][2] == 1.0)
    check("highlight colour is applied", "&H0033E633&" in gc.words_to_ass(timed, "&H0033E633&"))

vf = asm.overlay_filter("NINE YEARS IN THE SAME COAT", "Follow for part 2", "build/c.ass", 40.0,
                        {"vignette": True, "grain": 5, "title_seconds": 3, "cta_seconds": 2.5})
check("edit filter has vignette, grain, subtitles and CTA timing",
      all(x in vf for x in ("vignette", "noise=alls=5", "subtitles=", "gte(t,37.50)")))

# local push-in renders a clip of the right length at 24 fps (needs ffmpeg)
if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0:
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        img, out = os.path.join(tmp, "k.png"), os.path.join(tmp, "k.mp4")
        Image.new("RGB", (1024, 1536), (200, 210, 230)).save(img)
        an.local_motion(img, out, 1.5, 0, 24)
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                                "stream=r_frame_rate,width,height,nb_frames", "-of", "csv=p=0", out],
                               capture_output=True, text=True).stdout.strip()
        check(f"local push-in clip is 1080x1920 @ 24 fps, 2.5 s ({probe})", probe == "1080,1920,24/1,60")

sys.exit(1 if FAILED else 0)
