#!/usr/bin/env python3
"""
Post a video to TikTok via Buffer's publishing API.

Buffer handles the TikTok OAuth so we never need to store TikTok credentials
in this repo — only the Buffer access token (BUFFER_TOKEN secret).

Usage:
    python scripts/post_buffer_tiktok.py \
        --video-url "https://..." \
        --script build/script.json
"""
import argparse
import json
import os
import sys

import requests

BUFFER_API = "https://api.bufferapp.com/1"
HEADERS    = {"User-Agent": "yt-shorts-generator/1.0"}


def get_tiktok_profile_ids(token: str) -> list:
    """Return all TikTok profile IDs connected to this Buffer account."""
    r = requests.get(
        f"{BUFFER_API}/profiles.json",
        params={"access_token": token},
        headers=HEADERS,
        timeout=30,
    )
    if r.status_code != 200:
        print(f"[post_buffer_tiktok] profiles request failed {r.status_code}: {r.text[:300]}",
              file=sys.stderr)
        r.raise_for_status()

    profiles = r.json()
    tiktok   = [p["id"] for p in profiles if p.get("service", "").lower() == "tiktok"]
    if not tiktok:
        services = [p.get("service") for p in profiles]
        raise SystemExit(
            f"No TikTok profile found in Buffer account. "
            f"Connected services: {services}. "
            "Make sure TikTok is connected at buffer.com."
        )
    return tiktok


def post_video(token: str, profile_ids: list, video_url: str,
               caption: str, schedule_now: bool = True) -> dict:
    """Create a Buffer post for the given profile IDs."""
    data = {
        "access_token": token,
        "text": caption,
        "now": "true" if schedule_now else "false",
        "media[video]": video_url,
    }
    # Buffer API takes repeated profile_ids[] fields
    for pid in profile_ids:
        data.setdefault("profile_ids[]", [])
        if isinstance(data["profile_ids[]"], list):
            data["profile_ids[]"].append(pid)
        else:
            data["profile_ids[]"] = [data["profile_ids[]"], pid]

    # requests handles list values as repeated fields
    payload = []
    for k, v in data.items():
        if isinstance(v, list):
            for item in v:
                payload.append((k, item))
        else:
            payload.append((k, v))

    r = requests.post(
        f"{BUFFER_API}/updates/create.json",
        data=payload,
        headers=HEADERS,
        timeout=30,
    )
    if r.status_code not in (200, 201):
        print(f"[post_buffer_tiktok] post failed {r.status_code}: {r.text[:500]}",
              file=sys.stderr)
        r.raise_for_status()
    return r.json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-url", required=True)
    ap.add_argument("--script",    default="build/script.json")
    ap.add_argument("--caption-file", default="build/caption_tiktok.txt",
                    help="Platform-specific caption file (falls back to script lesson).")
    args = ap.parse_args()

    token = os.environ.get("BUFFER_TOKEN", "").strip()
    if not token:
        raise SystemExit("BUFFER_TOKEN environment variable not set.")

    # Load caption
    caption = ""
    if os.path.exists(args.caption_file):
        caption = open(args.caption_file, encoding="utf-8").read().strip()

    if not caption and os.path.exists(args.script):
        with open(args.script, encoding="utf-8") as f:
            script = json.load(f)
        # Build a TikTok caption from the script
        lesson = script.get("lesson", "")
        author = script.get("author", "")
        tags   = script.get("hashtags_instagram", "#motivation #success #history")
        caption = f"{lesson}\n\n— {author}\n\n{tags}"

    caption = caption[:2200]  # TikTok caption limit

    print(f"[post_buffer_tiktok] fetching Buffer TikTok profile IDs...")
    profile_ids = get_tiktok_profile_ids(token)
    print(f"[post_buffer_tiktok] found {len(profile_ids)} TikTok profile(s): {profile_ids}")

    print(f"[post_buffer_tiktok] posting video: {args.video_url[:80]}...")
    result = post_video(token, profile_ids, args.video_url, caption)

    success = result.get("success", False)
    post_id = result.get("updates", [{}])[0].get("id", "?") if result.get("updates") else "?"
    print(f"[post_buffer_tiktok] {'success' if success else 'failed'} — Buffer post id: {post_id}")

    if not success:
        print(f"[post_buffer_tiktok] response: {json.dumps(result, indent=2)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
