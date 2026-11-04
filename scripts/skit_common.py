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
        f"{shot.get('action', '')}.{speaking} Keep every character's face, body shape, outfit, colours and "
        "the name on their cap exactly as in their reference image, and keep the location exactly as in "
        f"its reference. {style_block(skit)}"
    )


def motion_prompt(skit: dict, shot: dict) -> str:
    return f"{shot.get('motion') or shot.get('action', '')}. {skit['video']['motion_suffix']}"
