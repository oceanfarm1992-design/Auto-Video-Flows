#!/usr/bin/env python3
"""
Shared helpers for the cartoon-skit format (no network).

The skit format turns a niche topic into a short dialogue scene acted by a recurring
cast (each character wears a cap with their name, so viewers and the image model
always know who is who), closed by the narrator with the psychology insight:

  generate_skit.py          GPT writes the shot list (speaker, line, location, action, motion)
  generate_dialogue_tts.py  one voice per character -> build/voice.wav + build/skit_timeline.json
  generate_captions.py      word-level captions (--timeline for the offline fallback)
  generate_keyframes.py     cast sheets + location plates (cached in assets/skit/<niche>/),
                            then one keyframe image per shot
  animate_shots.py          image-to-video per shot (fal.ai) or a free local push-in
  assemble_skit.py          hard cuts timed to the dialogue, vignette, grain, title, captions, CTA

Config: config/skit.json (shared) + the 'skit' block of config/niches/<niche>.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SKIT_CONFIG = Path("config/skit.json")
ASSET_ROOT = Path("assets/skit")


def load_configs(niche_path: str | Path, skit_path: str | Path = SKIT_CONFIG) -> tuple[dict, dict]:
    niche = json.loads(Path(niche_path).read_text(encoding="utf-8"))
    skit = json.loads(Path(skit_path).read_text(encoding="utf-8"))
    if "skit" not in niche:
        raise SystemExit(f"{niche_path} has no 'skit' block")
    return niche, skit


def asset_dir(niche: dict, kind: str) -> Path:
    """assets/skit/<niche>/<kind> where kind is 'characters' or 'locations'."""
    d = ASSET_ROOT / niche["id"] / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def cast_of(niche: dict) -> dict:
    return niche["skit"]["cast"]


def locations_of(niche: dict) -> dict:
    return niche["skit"]["locations"]


def label(niche: dict, char_id: str) -> str:
    return cast_of(niche).get(char_id, {}).get("label", "") or char_id.replace("_", " ").title()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


def load_script(path: str | Path = "build/script.json") -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != "skit" or not data.get("shots"):
        raise SystemExit(f"{path} is not a skit script (run generate_skit.py first)")
    return data


def style_block(skit: dict) -> str:
    return f"{skit['style']} {skit['cap_rule']} {skit['frame_rule']}"


def character_sheet_prompt(niche: dict, skit: dict, char_id: str) -> str:
    c = cast_of(niche)[char_id]
    return (
        "Character design reference sheet of ONE cartoon character, full body, front three-quarter view, "
        f"standing in a relaxed neutral pose on a plain white background. {skit['style']} "
        f"The character: {c['look']}. The cap has the words \"{c['label']}\" printed on the front in clean "
        "white capital letters. No other text, no background objects."
    )


def location_plate_prompt(niche: dict, skit: dict, loc_id: str) -> str:
    return (
        f"Empty background plate for an animated cartoon, no people: {locations_of(niche)[loc_id]}. "
        f"{skit['style']} Leave open floor space in the lower middle where characters will stand. "
        "No text anywhere."
    )


def shot_prompt(niche: dict, skit: dict, shot: dict, ref_names: list[str]) -> str:
    """Prompt for one keyframe. ref_names describes the reference images in order."""
    speaker = shot.get("speaker", "")
    who = label(niche, speaker) if speaker and speaker != "narrator" else ""
    refs = "; ".join(f"image {i + 1} is {n}" for i, n in enumerate(ref_names))
    speaking = (f" {who} is speaking with a {shot.get('emotion', 'natural')} expression, mouth open mid-word."
                if who else "")
    return (
        f"Reference images: {refs}. Draw ONE frame of the animated cartoon: {shot.get('framing', 'medium shot')}. "
        f"{shot.get('action', '')}.{speaking}{positions_text(niche, ordered_cast(niche, shot.get('characters', [])))} "
        "Keep every character's face, body shape, outfit, colours and "
        "the name on their cap exactly as in their reference image, and keep the location exactly as in "
        f"its reference. {style_block(skit)}"
    )


def motion_prompt(skit: dict, shot: dict) -> str:
    return f"{shot.get('motion') or shot.get('action', '')}. {skit['video']['motion_suffix']}"


# ── keyframe budget ───────────────────────────────────────────────────────────
# Pictures are ~90% of a skit's cost, so a skit gets a small budget of unique keyframes:
#   1. one MASTER per location: a medium-wide shot of everyone who appears there, standing
#      in a fixed left-to-right order;
#   2. the rest of the budget for KEY MOMENTS with their own acting: the hook (shot 0),
#      close-ups, and the payoff (last character line).
# Every other shot reuses its location's master with a virtual camera that pans/zooms onto the
# speaker (a close-up is just a tighter crop), so the cast never drifts between shots.

def ordered_cast(niche: dict, chars) -> list[str]:
    """Characters in their fixed left-to-right order (cast order in the config, hero first)."""
    order = [c for c in cast_of(niche) if c != "narrator"]
    hero = niche["skit"].get("hero")
    if hero in order:
        order.remove(hero)
        order.insert(0, hero)
    return [c for c in order if c in set(chars)]


def plan_keyframes(niche: dict, shots: list[dict], budget: int = 4) -> tuple[list[dict], list[dict]]:
    """Return (jobs, views).
    jobs:  [{"key", "kind": "master"|"moment", "location", "characters", "shot": index|None}]
    views: one per shot: {"key", "focus": 0..1 (x position to frame), "zoom": end zoom}"""
    jobs, master_of = [], {}
    for s in shots:
        loc = s["location"]
        if loc in master_of:
            continue
        here = [c for t in shots if t["location"] == loc for c in t["characters"]]
        top = sorted(set(here), key=lambda c: (-here.count(c), here.index(c)))[:3]
        master_of[loc] = f"master_{loc}"
        jobs.append({"key": master_of[loc], "kind": "master", "location": loc,
                     "characters": ordered_cast(niche, top), "shot": None})

    last_char = max((i for i, s in enumerate(shots) if s["speaker"] != "narrator"), default=0)
    priority = [0] + [i for i, s in enumerate(shots) if "close" in s.get("framing", "").lower()] + [last_char]
    moments = []
    for i in priority:
        if i not in moments and len(jobs) + len(moments) < max(budget, len(jobs)):
            moments.append(i)
    for i in moments:
        s = shots[i]
        jobs.append({"key": f"shot_{i:02d}", "kind": "moment", "location": s["location"],
                     "characters": ordered_cast(niche, s["characters"]), "shot": i})

    views = []
    for i, s in enumerate(shots):
        close = "close" in s.get("framing", "").lower()
        if i in moments:
            views.append({"key": f"shot_{i:02d}", "focus": 0.5, "zoom": 1.07})
            continue
        job = next(j for j in jobs if j["key"] == master_of[s["location"]])
        chars = job["characters"]
        target = s["speaker"] if s["speaker"] in chars else next((c for c in s["characters"] if c in chars), None)
        if target is None or s["speaker"] == "narrator":
            views.append({"key": job["key"], "focus": 0.5, "zoom": 1.06})
            continue
        k = chars.index(target)
        focus = (k + 0.5) / len(chars)
        single = {1: 1.1, 2: 1.35}.get(len(chars), 1.5)    # isolate the speaker in the wide frame
        views.append({"key": job["key"], "focus": round(focus, 3), "zoom": 1.9 if close else single})
    return jobs, views


def positions_text(niche: dict, chars: list[str]) -> str:
    if len(chars) < 2:
        return ""
    names = ", ".join(label(niche, c) for c in chars)
    return f" Positions from left to right: {names}, each clearly separated."


def master_prompt(niche: dict, skit: dict, location: str, chars: list[str], ref_names: list[str]) -> str:
    refs = "; ".join(f"image {i + 1} is {n}" for i, n in enumerate(ref_names))
    who = " and ".join(label(niche, c) for c in chars)
    return (
        f"Reference images: {refs}. Draw ONE frame of the animated cartoon: a medium-wide shot of {who} "
        f"standing in the {location.replace('_', ' ')}, turned slightly toward each other mid-conversation, "
        f"relaxed neutral expressions, full figures visible.{positions_text(niche, chars)} Keep every "
        "character's face, body shape, outfit, colours and the name on their cap exactly as in their reference "
        f"image, and keep the location exactly as in its reference. {style_block(skit)}"
    )
