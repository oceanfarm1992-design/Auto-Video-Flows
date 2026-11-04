#!/usr/bin/env python3
"""
Stage 4 (skit format): bring every keyframe to life.

For each shot (duration = its line's span in build/skit_timeline.json):
  fal    image-to-video on fal.ai (Kling / Hailuo / Seedance ... set by config/skit.json
         video.fal_model). All shots are submitted to fal's queue at once and polled,
         so a 12-shot skit takes about as long as one clip. Clip length is the smallest
         of video.fal_durations that covers the line (the editor trims / holds).
  local  free: a slow push-in with a gentle drift on the keyframe (no character motion).
         Used when VIDEO_PROVIDER=local, when FAL_KEY is missing, or when a fal job fails.
  doodle when a shot has no keyframe at all (no image provider), the existing stick-figure
         doodle engine draws it, so the pipeline still finishes with zero paid services.

Output: build/clips/shot_NN.mp4 + build/skit_clips.json [{index, file, duration, source}]

Usage:
    python scripts/animate_shots.py --niche config/niches/men_psychology.json
    VIDEO_PROVIDER=local python scripts/animate_shots.py --niche ...
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
from PIL import Image

from skit_common import load_configs, load_script, motion_prompt

FAL_QUEUE = "https://queue.fal.run"


# ── fal.ai image-to-video ────────────────────────────────────────────────────

def _img_data_uri(path: str, max_side: int = 1280) -> str:
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def pick_duration(need: float, options: list[int]) -> int:
    opts = sorted(int(o) for o in options) or [5]
    return next((o for o in opts if o >= need - 0.25), opts[-1])


def fal_submit(key: str, model: str, image: str, prompt: str, duration: int, extra: dict) -> dict:
    payload = {"prompt": prompt, "image_url": _img_data_uri(image), "duration": str(duration), **extra}
    r = requests.post(f"{FAL_QUEUE}/{model}", json=payload, timeout=120,
                      headers={"Authorization": f"Key {key}"})
    r.raise_for_status()
    return r.json()


def fal_wait(key: str, jobs: dict, timeout_s: int) -> dict:
    """jobs: {index: submit-response}. Returns {index: video_url} for the ones that finished."""
    headers = {"Authorization": f"Key {key}"}
    done, pending, t0 = {}, dict(jobs), time.time()
    while pending and time.time() - t0 < timeout_s:
        time.sleep(8)
        for i, job in list(pending.items()):
            try:
                st = requests.get(job["status_url"], headers=headers, timeout=60).json()
            except Exception as exc:  # noqa: BLE001 - transient; poll again
                print(f"[animate] status poll for shot {i:02d} failed: {exc}", file=sys.stderr)
                continue
            status = st.get("status")
            if status == "COMPLETED":
                res = requests.get(job["response_url"], headers=headers, timeout=60).json()
                url = (res.get("video") or {}).get("url")
                if url:
                    done[i] = url
                else:
                    print(f"[animate] shot {i:02d}: no video in response {str(res)[:200]}", file=sys.stderr)
                pending.pop(i)
            elif status not in ("IN_QUEUE", "IN_PROGRESS"):
                print(f"[animate] shot {i:02d}: fal status {status}: {str(st)[:200]}", file=sys.stderr)
                pending.pop(i)
        print(f"[animate] fal: {len(done)} done, {len(pending)} running ({int(time.time() - t0)}s)")
    for i in pending:
        print(f"[animate] shot {i:02d}: timed out on fal", file=sys.stderr)
    return done


# ── free fallbacks ───────────────────────────────────────────────────────────

def local_motion(image: str, dest: str, duration: float, idx: int, fps: int = 24) -> None:
    """Slow push-in (1.00 -> 1.07) with a slight sideways drift, alternating direction per shot.
    Rendered from a 2x canvas so the zoom doesn't stair-step."""
    frames = max(2, int(round(duration * fps)) + fps)          # one spare second for the editor
    drift = "+" if idx % 2 == 0 else "-"
    vf = (
        "scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,"
        f"zoompan=z='1+0.07*on/{frames}':"
        f"x='iw/2-(iw/zoom/2){drift}40*on/{frames}':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s=1080x1920:fps={fps},setsar=1,format=yuv420p"
    )
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", image, "-vf", vf,
                    "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", dest],
                   check=True)


def doodle_clip(shot: dict, dest: str, duration: float, idx: int, used: list) -> None:
    from fetch_footage_multi import generate_doodle
    seg = {"text": shot["line"], "scene": "", "label": ""}
    generate_doodle(seg, dest, duration, idx, used, start_s=99.0)


def valid_video(path: str, min_seconds: float = 0.5) -> bool:
    """Fully decode the file and check it has a real duration. A provider can return a
    truncated or empty mp4 that would crash or blank the edit much later. (Same idea as
    MoneyPrinterTurbo's material validation before a clip is used.)"""
    if not os.path.exists(path) or os.path.getsize(path) < 1024:
        return False
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                           capture_output=True, text=True)
    try:
        if float(probe.stdout.strip()) < min_seconds:
            return False
    except ValueError:
        return False
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"],
                            capture_output=True, text=True)
    return decode.returncode == 0 and not decode.stderr.strip()


def download(url: str, dest: str) -> None:
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", required=True)
    ap.add_argument("--skit-config", default="config/skit.json")
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--timeline", default="build/skit_timeline.json")
    ap.add_argument("--keyframes", default="build/keyframes.json")
    ap.add_argument("--out-dir", default="build/clips")
    args = ap.parse_args()

    _, skit = load_configs(args.niche, args.skit_config)
    vcfg, fps = skit["video"], int(skit["edit"]["fps"])
    data = load_script(args.script)
    timeline = json.loads(Path(args.timeline).read_text(encoding="utf-8"))["lines"]
    frames = {}
    if Path(args.keyframes).exists():
        frames = {k["index"]: k["file"] for k in json.loads(Path(args.keyframes).read_text()) if k.get("file")}

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    durs = [max(0.8, t["shot_end"] - t["shot_start"]) for t in timeline]

    provider = (os.environ.get("VIDEO_PROVIDER") or vcfg.get("provider", "fal")).lower()
    key = os.environ.get("FAL_KEY", "").strip()
    if provider == "fal" and not key:
        print("[animate] FAL_KEY not set; using the free local push-in for every shot")
        provider = "local"

    results: dict[int, tuple[str, str]] = {}
    if provider == "fal" and frames:
        jobs = {}
        for i, shot in enumerate(data["shots"]):
            if i not in frames:
                continue
            try:
                jobs[i] = fal_submit(key, vcfg["fal_model"], frames[i], motion_prompt(skit, shot),
                                     pick_duration(durs[i], vcfg.get("fal_durations", [5, 10])),
                                     vcfg.get("fal_args", {}))
            except Exception as exc:  # noqa: BLE001
                print(f"[animate] shot {i:02d}: fal submit failed ({exc}); local fallback", file=sys.stderr)
        print(f"[animate] submitted {len(jobs)} shot(s) to {vcfg['fal_model']}")
        for i, url in fal_wait(key, jobs, int(vcfg.get("timeout_s", 900))).items():
            dest = str(out / f"shot_{i:02d}.mp4")
            for attempt in (1, 2):
                try:
                    download(url, dest)
                    if valid_video(dest):
                        results[i] = (dest, "fal")
                        break
                    print(f"[animate] shot {i:02d}: downloaded clip is broken (try {attempt})", file=sys.stderr)
                except Exception as exc:  # noqa: BLE001
                    print(f"[animate] shot {i:02d}: download failed ({exc})", file=sys.stderr)

    used_doodles: list = []
    for i, shot in enumerate(data["shots"]):
        if i in results:
            continue
        dest = str(out / f"shot_{i:02d}.mp4")
        if i in frames:
            local_motion(frames[i], dest, durs[i], i, fps)
            results[i] = (dest, "local")
        else:
            doodle_clip(shot, dest, durs[i], i, used_doodles)
            results[i] = (dest, "doodle")

    manifest = [{"index": i, "file": results[i][0], "duration": round(durs[i], 3), "source": results[i][1]}
                for i in range(len(data["shots"]))]
    Path("build/skit_clips.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    counts = {s: sum(1 for m in manifest if m["source"] == s) for s in ("fal", "local", "doodle")}
    print(f"[animate] clips ready: {counts}")


if __name__ == "__main__":
    main()
