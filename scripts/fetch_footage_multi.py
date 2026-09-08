#!/usr/bin/env python3
"""
Stage 2b: Fetch one video clip or photo per script segment.

Reads build/script.json for segments[], estimates each segment's duration
from its word count, then fetches relevant media for each from:
  Pexels video → Pixabay video → Pexels photo → Pixabay photo → generated animation

Outputs:
  build/footage_0.mp4  (or .jpg for photos)
  build/footage_1.mp4
  ...
  build/footage_manifest.json  — list of {file, type, duration, query, source}

Usage:
    python scripts/fetch_footage_multi.py
    python scripts/fetch_footage_multi.py --force-source animate
"""
import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import requests

HEADERS = {"User-Agent": "yt-shorts-generator/1.0"}

PEXELS_VIDEO  = "https://api.pexels.com/videos/search"
PEXELS_PHOTO  = "https://api.pexels.com/v1/search"
PIXABAY_VIDEO = "https://pixabay.com/api/videos/"
PIXABAY_PHOTO = "https://pixabay.com/api/"

WORDS_PER_SECOND = 2.6   # fallback if not in config
MIN_CLIP_SECONDS  = 5.0  # minimum footage duration to fetch


def _download(url: str, dest: str, headers: dict = None):
    with requests.get(url, headers=headers or HEADERS,
                      stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)


# ── Pexels video ─────────────────────────────────────────────────────────────

def pexels_video(query: str, dest: str, min_height: int = 720) -> dict | None:
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    params = {"query": query, "orientation": "portrait", "size": "medium", "per_page": 40}
    r = requests.get(PEXELS_VIDEO, params=params,
                     headers={"Authorization": key, **HEADERS}, timeout=60)
    r.raise_for_status()
    videos = r.json().get("videos", [])
    if not videos:
        return None
    video = random.choice(videos)
    files = [f for f in video.get("video_files", [])
             if f.get("file_type") == "video/mp4" and f.get("link")]
    portrait = [f for f in files if (f.get("height") or 0) >= (f.get("width") or 1)]
    files = portrait or files
    files = [f for f in files if (f.get("height") or 0) >= min_height] or files
    if not files:
        return None
    best = max(files, key=lambda f: (f.get("height") or 0))
    _download(best["link"], dest, headers={"Authorization": key, **HEADERS})
    return {"source": "pexels_video", "query": query,
            "resolution": f"{best.get('width')}x{best.get('height')}",
            "license": "Pexels License"}


# ── Pixabay video ─────────────────────────────────────────────────────────────

def pixabay_video(query: str, dest: str, min_height: int = 720) -> dict | None:
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return None
    params = {"key": key, "q": query, "per_page": 40, "safesearch": "true"}
    r = requests.get(PIXABAY_VIDEO, params=params, headers=HEADERS, timeout=60)
    r.raise_for_status()
    hits = r.json().get("hits", [])
    if not hits:
        return None
    hit = random.choice(hits)
    renditions = hit.get("videos", {})
    chosen = None
    for name in ("large", "medium", "small", "tiny"):
        v = renditions.get(name)
        if v and v.get("url") and (v.get("height") or 0) >= min_height:
            chosen = v
            break
    if not chosen:
        chosen = next((renditions.get(n) for n in ("large", "medium", "small", "tiny")
                       if renditions.get(n, {}).get("url")), None)
    if not chosen:
        return None
    _download(chosen["url"], dest)
    return {"source": "pixabay_video", "query": query,
            "resolution": f"{chosen.get('width')}x{chosen.get('height')}",
            "license": "Pixabay Content License"}


# ── Pexels photo ──────────────────────────────────────────────────────────────

def pexels_photo(query: str, dest: str) -> dict | None:
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    params = {"query": query, "orientation": "portrait", "per_page": 30}
    r = requests.get(PEXELS_PHOTO, params=params,
                     headers={"Authorization": key, **HEADERS}, timeout=60)
    r.raise_for_status()
    photos = r.json().get("photos", [])
    if not photos:
        return None
    photo = random.choice(photos)
    src = photo.get("src", {})
    url = src.get("portrait") or src.get("large2x") or src.get("large")
    if not url:
        return None
    # save as jpg regardless of extension
    _download(url, dest, headers={"Authorization": key, **HEADERS})
    return {"source": "pexels_photo", "query": query, "type": "photo",
            "license": "Pexels License"}


# ── Pixabay photo ─────────────────────────────────────────────────────────────

def pixabay_photo(query: str, dest: str) -> dict | None:
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return None
    params = {"key": key, "q": query, "image_type": "photo",
              "orientation": "vertical", "per_page": 40, "safesearch": "true"}
    r = requests.get(PIXABAY_PHOTO, params=params, headers=HEADERS, timeout=60)
    r.raise_for_status()
    hits = r.json().get("hits", [])
    if not hits:
        return None
    hit = random.choice(hits)
    url = hit.get("largeImageURL") or hit.get("webformatURL")
    if not url:
        return None
    _download(url, dest)
    return {"source": "pixabay_photo", "query": query, "type": "photo",
            "license": "Pixabay Content License"}


# ── Generated animation fallback ──────────────────────────────────────────────

def generate_anim(query: str, dest: str, duration: float,
                  cfg: dict, seed: int) -> dict:
    """Render a cinematic gradient animation. Never fails."""
    from generate_animation import render_animation, pick_palette  # local import
    video_cfg = cfg.get("video", {})
    w = video_cfg.get("width", 1080)
    h = video_cfg.get("height", 1920)
    palette = pick_palette(cfg, str(seed))
    render_animation(dest, w, h, int(duration) + 2, palette, seed=seed % 256)
    return {"source": "animate", "query": query, "type": "video",
            "resolution": f"{w}x{h}", "license": "Generated animation"}


# ── Per-segment fetcher ───────────────────────────────────────────────────────

def fetch_segment(idx: int, segment: dict, duration: float,
                  out_dir: str, cfg: dict, force_source: str | None) -> dict:
    query = segment.get("footage_query", "cinematic nature")
    want_photo = segment.get("media_type") == "photo" and not force_source

    # destination file paths
    vid_dest  = os.path.join(out_dir, f"footage_{idx}.mp4")
    photo_dest = os.path.join(out_dir, f"footage_{idx}.jpg")

    if force_source:
        sources = [force_source]
    elif want_photo:
        sources = ["pexels_photo", "pixabay_photo", "pexels_video", "pixabay_video", "animate"]
    else:
        sources = ["pexels_video", "pixabay_video", "pexels_photo", "pixabay_photo", "animate"]

    info = None
    for source in sources:
        dest = photo_dest if "photo" in source else vid_dest
        try:
            if source == "pexels_video":
                info = pexels_video(query, dest)
            elif source == "pixabay_video":
                info = pixabay_video(query, dest)
            elif source == "pexels_photo":
                info = pexels_photo(query, photo_dest)
                if info:
                    dest = photo_dest
            elif source == "pixabay_photo":
                info = pixabay_photo(query, photo_dest)
                if info:
                    dest = photo_dest
            elif source == "animate":
                seed = sum(ord(c) for c in query) + idx
                info = generate_anim(query, vid_dest, duration, cfg, seed)
                dest = vid_dest
        except Exception as e:
            print(f"  [{source}] failed: {e}", file=sys.stderr)
            info = None
        if info and os.path.exists(dest) and os.path.getsize(dest) > 0:
            is_photo = "photo" in source
            info["file"]     = dest
            info["type"]     = "photo" if is_photo else "video"
            info["duration"] = duration
            info["query"]    = query
            print(f"  segment {idx}: {source} → {dest} ({duration:.1f}s)")
            return info
        print(f"  segment {idx}: {source}: no result, trying next")

    raise RuntimeError(f"Segment {idx}: all sources failed for query '{query}'")


# ── Duration estimation ───────────────────────────────────────────────────────

def estimate_durations(segments: list, total_words: int,
                       wps: float, audio_duration: float | None) -> list[float]:
    """Proportionally distribute audio_duration (or estimate) across segments."""
    if audio_duration is None:
        audio_duration = total_words / wps
    seg_words = [len(s.get("text", "").split()) for s in segments]
    total_seg_words = sum(seg_words) or 1
    durations = [max(MIN_CLIP_SECONDS, audio_duration * (w / total_seg_words))
                 for w in seg_words]
    return durations


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--config", default="config/sources.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--force-source", default=None,
                    choices=["pexels_video", "pixabay_video",
                             "pexels_photo", "pixabay_photo", "animate"])
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as f:
        script = json.load(f)

    cfg = {}
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
    wps = cfg.get("video", {}).get("words_per_second", WORDS_PER_SECOND)

    segments = script.get("segments", [])
    if not segments:
        # Fallback: create one segment covering the whole narration
        segments = [{"text": script.get("narration", ""),
                     "footage_query": script.get("footage_query", "cinematic nature"),
                     "media_type": "video"}]
        print("[fetch_footage_multi] No segments in script; using single fallback clip")

    total_words = len(script.get("narration", "").split())
    # Try to read audio duration if TTS already ran (not always the case at this point)
    audio_duration = None
    if os.path.exists("build/voice.wav"):
        try:
            import wave, contextlib
            with contextlib.closing(wave.open("build/voice.wav", "rb")) as w:
                audio_duration = w.getnframes() / float(w.getframerate())
        except Exception:
            pass

    durations = estimate_durations(segments, total_words, wps, audio_duration)

    os.makedirs(args.out, exist_ok=True)
    manifest = []
    for i, (seg, dur) in enumerate(zip(segments, durations)):
        print(f"[fetch_footage_multi] segment {i+1}/{len(segments)}: "
              f"'{seg.get('footage_query', '')}' ~{dur:.1f}s")
        info = fetch_segment(i, seg, dur, args.out, cfg, args.force_source)
        manifest.append(info)

    manifest_path = os.path.join(args.out, "footage_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"[fetch_footage_multi] wrote {manifest_path} "
          f"({len(manifest)} clips/photos)")


if __name__ == "__main__":
    main()
