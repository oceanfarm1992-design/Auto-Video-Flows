#!/usr/bin/env python3
"""
Stage 3 (skit format): the pictures.

1. Cast + location assets (generated ONCE per niche, then reused forever):
     assets/skit/<niche>/characters/<id>.png   character design sheet (cap with name)
     assets/skit/<niche>/locations/<id>.png    empty background plate
   Commit these. Every keyframe is drawn FROM them as references, which is what keeps
   the cast looking the same from shot to shot and video to video. Delete a file to
   redesign that character/location; the next run regenerates it.

2. One keyframe per shot (build/keyframes/shot_NN.png): an image-edit call given the
   location plate + the sheets of the characters in the shot, plus the shot's action,
   framing and the speaker's expression.

Providers (config/skit.json -> images.provider, env IMAGE_PROVIDER overrides):
  openai  gpt-image (OPENAI_API_KEY) — default
  fal     any fal.ai text-to-image + image-edit model pair (FAL_KEY)
  none    no images; animate_shots.py draws every shot with the doodle engine instead
A shot whose image fails is simply left out of build/keyframes.json; animate_shots.py
falls back to the doodle engine for it, so a run never dies here.

Usage:
    python scripts/generate_keyframes.py --niche config/niches/men_psychology.json
    python scripts/generate_keyframes.py --niche ... --assets-only     # design/refresh the cast only
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from PIL import Image

from skit_common import (asset_dir, cast_of, character_sheet_prompt, label, load_configs, load_script,
                         location_plate_prompt, shot_prompt)


# ── providers ────────────────────────────────────────────────────────────────

class OpenAIImages:
    def __init__(self, cfg: dict):
        from openai import OpenAI
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY not set")
        self.client = OpenAI()
        self.model = cfg.get("openai_model", "gpt-image-1")
        self.size = cfg.get("openai_size", "1024x1536")
        self.quality = cfg.get("openai_quality", "medium")

    def generate(self, prompt: str) -> bytes:
        r = self.client.images.generate(model=self.model, prompt=prompt, size=self.size, quality=self.quality, n=1)
        return base64.b64decode(r.data[0].b64_json)

    def edit(self, prompt: str, refs: list[Path]) -> bytes:
        files = [open(p, "rb") for p in refs]
        try:
            r = self.client.images.edit(model=self.model, image=files, prompt=prompt, size=self.size,
                                        quality=self.quality, n=1)
        finally:
            for f in files:
                f.close()
        return base64.b64decode(r.data[0].b64_json)


def data_uri(path: Path, max_side: int = 900) -> str:
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


class FalImages:
    """fal.ai synchronous endpoints. VERIFY: parameter names for the chosen models."""

    def __init__(self, cfg: dict):
        self.key = os.environ.get("FAL_KEY", "").strip()
        if not self.key:
            raise RuntimeError("FAL_KEY not set")
        self.text_model = cfg.get("fal_text_model", "fal-ai/nano-banana")
        self.edit_model = cfg.get("fal_edit_model", "fal-ai/nano-banana/edit")

    def _run(self, model: str, payload: dict) -> bytes:
        r = requests.post(f"https://fal.run/{model}", json=payload, timeout=600,
                          headers={"Authorization": f"Key {self.key}"})
        r.raise_for_status()
        url = r.json()["images"][0]["url"]
        if url.startswith("data:"):
            return base64.b64decode(url.split(",", 1)[1])
        img = requests.get(url, timeout=300)
        img.raise_for_status()
        return img.content

    def generate(self, prompt: str) -> bytes:
        return self._run(self.text_model, {"prompt": prompt, "num_images": 1, "aspect_ratio": "9:16",
                                           "output_format": "png"})

    def edit(self, prompt: str, refs: list[Path]) -> bytes:
        return self._run(self.edit_model, {"prompt": prompt, "image_urls": [data_uri(p) for p in refs],
                                           "num_images": 1, "aspect_ratio": "9:16", "output_format": "png"})


def make_provider(cfg: dict):
    name = (os.environ.get("IMAGE_PROVIDER") or cfg.get("provider", "openai")).strip().lower()
    if name in ("none", "off", "doodle", ""):
        return None
    try:
        return {"openai": OpenAIImages, "fal": FalImages}[name](cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"[keyframes] image provider '{name}' unavailable ({exc}); every shot will be a doodle",
              file=sys.stderr)
        return None


def save_png(blob: bytes, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.open(io.BytesIO(blob)).convert("RGB").save(dest, "PNG")


def with_retry(fn, tries: int = 3):
    for k in range(tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if k == tries - 1:
                raise
            print(f"[keyframes] retry after {type(exc).__name__}: {str(exc)[:160]}", file=sys.stderr)
            time.sleep(4 * (k + 1))


# ── assets ───────────────────────────────────────────────────────────────────

def ensure_assets(provider, niche: dict, skit: dict, char_ids: list[str], loc_ids: list[str]) -> dict:
    """Return {id: Path} for every requested character and location that exists or was made."""
    have = {}
    jobs = []
    for cid in char_ids:
        p = asset_dir(niche, "characters") / f"{cid}.png"
        jobs.append((cid, p, character_sheet_prompt(niche, skit, cid)))
    for lid in loc_ids:
        p = asset_dir(niche, "locations") / f"{lid}.png"
        jobs.append((lid, p, location_plate_prompt(niche, skit, lid)))

    def work(job):
        key, path, prompt = job
        if path.exists():
            return key, path
        if provider is None:
            return key, None
        try:
            print(f"[keyframes] designing {path} ...")
            save_png(with_retry(lambda: provider.generate(prompt)), path)
            return key, path
        except Exception as exc:  # noqa: BLE001
            print(f"[keyframes] could not make {path}: {exc}", file=sys.stderr)
            return key, None

    with ThreadPoolExecutor(4) as pool:
        for key, path in pool.map(work, jobs):
            if path:
                have[key] = path
    return have


# ── shots ────────────────────────────────────────────────────────────────────

def make_shot(provider, niche: dict, skit: dict, i: int, shot: dict, assets: dict, out_dir: Path) -> str | None:
    dest = out_dir / f"shot_{i:02d}.png"
    if dest.exists():
        return str(dest)
    refs, names = [], []
    if shot["location"] in assets:
        refs.append(assets[shot["location"]])
        names.append(f"the location ({shot['location'].replace('_', ' ')})")
    for cid in shot["characters"]:
        if cid in assets:
            refs.append(assets[cid])
            names.append(f"the character {label(niche, cid)}")
    if provider is None or not refs:
        return None
    prompt = shot_prompt(niche, skit, shot, names)
    try:
        save_png(with_retry(lambda: provider.edit(prompt, refs)), dest)
        print(f"[keyframes] shot {i:02d} done")
        return str(dest)
    except Exception as exc:  # noqa: BLE001
        print(f"[keyframes] shot {i:02d} failed, doodle fallback: {exc}", file=sys.stderr)
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", required=True)
    ap.add_argument("--skit-config", default="config/skit.json")
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--out-dir", default="build/keyframes")
    ap.add_argument("--assets-only", action="store_true", help="only design the full cast and every location")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    niche, skit = load_configs(args.niche, args.skit_config)
    provider = make_provider(skit["images"])

    if args.assets_only:
        chars = [c for c in cast_of(niche) if c != "narrator"]
        got = ensure_assets(provider, niche, skit, chars, list(niche["skit"]["locations"]))
        print(f"[keyframes] assets ready: {len(got)}/{len(chars) + len(niche['skit']['locations'])}")
        return

    data = load_script(args.script)
    shots = data["shots"]
    chars = sorted({c for s in shots for c in s["characters"]})
    locs = sorted({s["location"] for s in shots})
    assets = ensure_assets(provider, niche, skit, chars, locs)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max(1, args.workers)) as pool:
        files = list(pool.map(lambda a: make_shot(provider, niche, skit, a[0], a[1], assets, out_dir),
                              enumerate(shots)))
    manifest = [{"index": i, "file": f} for i, f in enumerate(files)]
    Path("build").mkdir(exist_ok=True)
    Path("build/keyframes.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    ok = sum(1 for f in files if f)
    print(f"[keyframes] {ok}/{len(shots)} keyframes drawn"
          + ("" if ok == len(shots) else f"; {len(shots) - ok} shot(s) will use the doodle fallback"))


if __name__ == "__main__":
    main()
