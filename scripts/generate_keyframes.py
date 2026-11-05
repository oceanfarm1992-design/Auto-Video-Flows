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
                         location_plate_prompt, master_prompt, mouth_open_prompt, plan_keyframes, shot_prompt)


# ── providers ────────────────────────────────────────────────────────────────

class FatalImageError(RuntimeError):
    """A request that will fail the same way every time (bad model, no access): never retried."""


def _is_model_missing(exc: Exception) -> bool:
    msg = str(exc).lower()
    return ("does not exist" in msg or "model_not_found" in msg or "invalid model" in msg
            or "unknown model" in msg) and "model" in msg


def model_size(model: str, cfg: dict) -> str:
    """gpt-image-2 takes any WxH divisible by 16 -> exact 9:16. Older models: their 2:3 portrait."""
    if model.startswith("gpt-image-2") or model == "chatgpt-image-latest":
        return cfg.get("openai_size_v2", "864x1536")
    return cfg.get("openai_size", "1024x1536")


class OpenAIImages:
    """OpenAI GPT Image models. Model names change (gpt-image-1 was retired for some accounts),
    so this tries config openai_models in order and, if none exists, asks the account which
    gpt-image models it can use and takes the newest."""

    def __init__(self, cfg: dict):
        from openai import OpenAI
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY not set")
        self.client = OpenAI()
        self.cfg = cfg
        self.candidates = list(cfg.get("openai_models") or [cfg.get("openai_model", "gpt-image-2")])
        self.model: str | None = None
        self.quality = cfg.get("openai_quality", "medium")

    def _discover(self) -> list[str]:
        try:
            ids = [m.id for m in self.client.models.list() if "image" in m.id and m.id.startswith(("gpt-image", "chatgpt-image"))]
        except Exception as exc:  # noqa: BLE001
            print(f"[keyframes] could not list models: {exc}", file=sys.stderr)
            return []
        return sorted(ids, reverse=True)

    def _call(self, fn):
        """Run fn(model) with the first model that exists; remember it for later calls."""
        if self.model:
            return fn(self.model)
        tried = []
        for pool in (self.candidates, None):
            names = pool if pool is not None else [m for m in self._discover() if m not in tried]
            for m in names:
                tried.append(m)
                try:
                    out = fn(m)
                    self.model = m
                    print(f"[keyframes] using image model {m}")
                    return out
                except Exception as exc:  # noqa: BLE001
                    if not _is_model_missing(exc):
                        raise
                    print(f"[keyframes] image model {m} not available on this account", file=sys.stderr)
        raise FatalImageError(f"no GPT Image model available (tried {tried})")

    def generate(self, prompt: str) -> bytes:
        def go(m):
            r = self.client.images.generate(model=m, prompt=prompt, size=model_size(m, self.cfg),
                                            quality=self.quality, n=1)
            return base64.b64decode(r.data[0].b64_json)
        return self._call(go)

    def edit(self, prompt: str, refs: list[Path], shrink: bool = True) -> bytes:
        if shrink:   # full size only when the output must line up with the input (mouth variants)
            refs = small_refs(refs, int(self.cfg.get("ref_max_side", 768)))

        def go(m):
            files = [open(p, "rb") for p in refs]
            try:
                r = self.client.images.edit(model=m, image=files, prompt=prompt, size=model_size(m, self.cfg),
                                            quality=self.quality, n=1)
            finally:
                for f in files:
                    f.close()
            return base64.b64decode(r.data[0].b64_json)
        return self._call(go)


def small_refs(refs: list[Path], max_side: int) -> list[Path]:
    """Reference images are billed as input tokens by size: send small copies (cached in build/refs)."""
    out = []
    d = Path("build/refs")
    d.mkdir(parents=True, exist_ok=True)
    for p in refs:
        dest = d / f"{Path(p).parent.name}_{Path(p).stem}_{max_side}.png"
        if not dest.exists():
            im = Image.open(p).convert("RGB")
            im.thumbnail((max_side, max_side))
            im.save(dest, "PNG")
        out.append(dest)
    return out


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

    def edit(self, prompt: str, refs: list[Path], shrink: bool = True) -> bytes:
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
        except FatalImageError:
            raise
        except Exception as exc:  # noqa: BLE001
            if k == tries - 1 or _is_model_missing(exc) or "must be verified" in str(exc).lower():
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

def make_job(provider, niche: dict, skit: dict, job: dict, shots: list[dict], assets: dict,
             out_dir: Path) -> str | None:
    """Draw one planned keyframe (a location master or a key moment)."""
    dest = out_dir / f"{job['key']}.png"
    if dest.exists():
        return str(dest)
    refs, names = [], []
    if job["location"] in assets:
        refs.append(assets[job["location"]])
        names.append(f"the location ({job['location'].replace('_', ' ')})")
    for cid in job["characters"]:
        if cid in assets:
            refs.append(assets[cid])
            names.append(f"the character {label(niche, cid)}")
    if provider is None or not refs:
        return None
    if job["kind"] == "master":
        prompt = master_prompt(niche, skit, job["location"], job["characters"], names)
    else:
        prompt = shot_prompt(niche, skit, shots[job["shot"]], names)
    try:
        save_png(with_retry(lambda: provider.edit(prompt, refs)), dest)
        print(f"[keyframes] {job['key']} done")
        return str(dest)
    except Exception as exc:  # noqa: BLE001
        print(f"[keyframes] {job['key']} failed: {exc}", file=sys.stderr)
        return None


def _pt(v):
    ok = isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(t, (int, float)) for t in v) \
        and all(0 <= t <= 1 for t in v)
    return (float(v[0]), float(v[1])) if ok else None


def face_rig(niche: dict, chars: list[str], image: str) -> tuple[list | None, dict]:
    """One vision call: per character head / eyes / mouth / hand (fractions). Returns
    (mouth hints in character order, {char: {feature: (x, y) fractions}})."""
    from llm import vision_json
    from skit_common import face_points_prompt
    try:
        data, used = vision_json(face_points_prompt(niche, chars), image)
    except Exception as exc:  # noqa: BLE001
        print(f"[keyframes] no face points ({str(exc)[:120]})", file=sys.stderr)
        return None, {}
    rig = {}
    for c in chars:
        f = data.get(c) if isinstance(data.get(c), dict) else {}
        rig[c] = {k: _pt(f.get(k)) for k in ("head", "left_eye", "right_eye", "mouth", "hand")}
    print(f"[keyframes] face points from {used}: "
          + ", ".join(f"{c}: {sum(v is not None for v in r.values())}/5" for c, r in rig.items()))
    return [rig[c]["mouth"] for c in chars], rig


def mouth_hints(niche: dict, chars: list[str], image: str) -> list | None:
    """Where each character's mouth is, per a vision model (free OpenRouter VL first, then
    gpt-4.1-mini, ~0.1 cent). Only used to pick the right changed region; None if unavailable."""
    from llm import vision_json
    from skit_common import mouth_points_prompt
    try:
        data, used = vision_json(mouth_points_prompt(niche, chars), image)
    except Exception as exc:  # noqa: BLE001
        print(f"[keyframes] no mouth hints ({str(exc)[:120]})", file=sys.stderr)
        return None
    out = []
    for c in chars:
        v = data.get(c)
        ok = isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(t, (int, float)) for t in v) \
            and all(0 <= t <= 1 for t in v)
        out.append((float(v[0]), float(v[1])) if ok else None)
    print(f"[keyframes] mouth hints from {used}: {dict(zip(chars, out))}")
    return out


def make_mouth_variant(provider, niche: dict, job: dict, closed: str, out_dir: Path) -> dict | None:
    """Open-mouth edit of a keyframe + where each character's mouth is (mouth_flaps.py)."""
    from mouth_flaps import aligned_open, assign_mouths, detect_mouths
    dest = out_dir / f"{job['key']}_open.png"
    try:
        if not dest.exists():
            save_png(with_retry(lambda: provider.edit(mouth_open_prompt(niche, job["characters"]),
                                                      [Path(closed)], shrink=False)), dest)
        base = Image.open(closed).convert("RGB")
        hints, rig = face_rig(niche, job["characters"], closed)
        if hints is None:
            hints = mouth_hints(niche, job["characters"], closed)
        boxes = detect_mouths(base, aligned_open(base, Image.open(dest)), len(job["characters"]), hints=hints)
        mouths = assign_mouths(boxes, job["characters"], base.width)
        from mouth_flaps import debug_overlay
        debug_overlay(base, mouths, str(out_dir / f"{job['key']}_mouths.jpg"))
        missing = [c for c in job["characters"] if c not in mouths]
        print(f"[keyframes] {job['key']}: mouths found for {sorted(mouths) or 'nobody'}"
              + (f"; not found: {missing}" if missing else ""))
        w, h = base.size
        rig_px = {c: {k: [v[0] * w, v[1] * h] for k, v in f.items() if v and k != "mouth"}
                  for c, f in rig.items()}
        for c, box in mouths.items():
            rig_px.setdefault(c, {})["mouth_box"] = box
        return {"open_file": str(dest), "mouths": mouths, "rig": rig_px}
    except Exception as exc:  # noqa: BLE001 - lip sync is an extra; the still picture still works
        print(f"[keyframes] {job['key']}: no mouth variant ({exc})", file=sys.stderr)
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
    budget = int(os.environ.get("MAX_KEYFRAMES") or skit["images"].get("max_keyframes", 4))
    jobs, views = plan_keyframes(niche, shots, budget)
    chars = sorted({c for j in jobs for c in j["characters"]})
    locs = sorted({j["location"] for j in jobs})
    assets = ensure_assets(provider, niche, skit, chars, locs)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max(1, args.workers)) as pool:
        made = dict(zip([j["key"] for j in jobs],
                        pool.map(lambda j: make_job(provider, niche, skit, j, shots, assets, out_dir), jobs)))
    lips = {}
    if provider is not None and skit["images"].get("lipsync", True):
        todo = [j for j in jobs if made.get(j["key"])]
        with ThreadPoolExecutor(max(1, args.workers)) as pool:
            for j, res in zip(todo, pool.map(lambda j: make_mouth_variant(provider, niche, j, made[j["key"]], out_dir),
                                             todo)):
                if res:
                    lips[j["key"]] = res
    # a failed key moment falls back to its location's master; a failed master leaves its shots
    # to the doodle renderer
    manifest = []
    for i, v in enumerate(views):
        f = made.get(v["key"])
        if not f and v["key"].startswith("shot_"):
            f = made.get(f"master_{shots[i]['location']}")
            v = {"key": f"master_{shots[i]['location']}", "focus": 0.5, "zoom": 1.12}
        lip = lips.get(v["key"], {})
        manifest.append({"index": i, "file": f, "key": v["key"], "focus": v["focus"], "zoom": v["zoom"],
                         "speaker": shots[i]["speaker"], "open_file": lip.get("open_file"),
                         "mouth": lip.get("mouths", {}).get(shots[i]["speaker"]),
                         "rig": lip.get("rig"), "emotion": shots[i].get("emotion", "")})
    Path("build").mkdir(exist_ok=True)
    Path("build/keyframes.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    ok = sum(1 for m in manifest if m["file"])
    drawn = sum(1 for f in made.values() if f)
    print(f"[keyframes] {drawn}/{len(jobs)} unique pictures drawn (budget {budget}) for {len(shots)} shots; "
          f"{ok}/{len(shots)} shots covered"
          + ("" if ok == len(shots) else f"; {len(shots) - ok} shot(s) will use the doodle fallback"))


if __name__ == "__main__":
    main()
