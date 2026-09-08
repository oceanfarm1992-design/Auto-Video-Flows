#!/usr/bin/env python3
"""
Post a video to one or more platforms via Buffer's GraphQL API.

Handles TikTok and Facebook (and any other service connected in Buffer).
Buffer deprecated its legacy REST API (/1/*) — only the GraphQL endpoint
https://graph.buffer.com/ is accepted now, with a Bearer token.

The BUFFER_TOKEN secret must be an access token with 'publish' scope.
Generate one at: https://developers.buffer.com → Create App → Access Token

Usage:
    python scripts/post_buffer.py \
        --video-url "https://..." \
        --script build/script.json \
        --services tiktok,facebook
"""
import argparse
import json
import os
import sys

import requests

BUFFER_GRAPHQL = "https://graph.buffer.com/"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "yt-shorts-generator/1.0",
}

# Caption length limits per platform
CAPTION_LIMITS = {
    "tiktok":   2200,
    "facebook": 63206,
    "instagram": 2200,
    "default":  2200,
}


def gql(token: str, query: str, variables: dict = None) -> dict:
    resp = requests.post(
        BUFFER_GRAPHQL,
        json={"query": query, "variables": variables or {}},
        headers={**HEADERS, "Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"[post_buffer] GraphQL HTTP {resp.status_code}: {resp.text[:500]}",
              file=sys.stderr)
        resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        raise SystemExit(f"[post_buffer] GraphQL errors: {body['errors']}")
    return body.get("data", {})


GET_CHANNELS = """
query {
  channels {
    id
    service
    name
    isConnected
  }
}
"""

CREATE_POST = """
mutation CreatePost($channelId: String!, $text: String!, $mediaUrls: [String!]) {
  postCreate(input: {
    channelId: $channelId
    text: $text
    mediaUrls: $mediaUrls
  }) {
    post {
      id
      status
    }
    userErrors {
      message
      field
    }
  }
}
"""


def get_channels(token: str, services: list) -> list:
    """Return Buffer channel dicts for the requested services."""
    data = gql(token, GET_CHANNELS)
    all_channels = data.get("channels", [])
    if not all_channels:
        raise SystemExit(
            "[post_buffer] No channels returned. "
            "Check BUFFER_TOKEN has 'publish' scope and channels are connected at buffer.com."
        )

    print("[post_buffer] Connected channels: "
          + ", ".join(f"{c.get('service')}:{c.get('name')}" for c in all_channels))

    matched = [
        c for c in all_channels
        if c.get("service", "").lower() in services and c.get("isConnected")
    ]
    if not matched:
        raise SystemExit(
            f"[post_buffer] No connected channels found for services {services}. "
            f"Connected: {[c.get('service') for c in all_channels]}. "
            "Connect the required platforms at buffer.com/channels."
        )
    return matched


def post_video(token: str, channel_id: str, service: str,
               video_url: str, caption: str) -> dict:
    limit = CAPTION_LIMITS.get(service.lower(), CAPTION_LIMITS["default"])
    caption = caption[:limit]

    data = gql(token, CREATE_POST, {
        "channelId": channel_id,
        "text": caption,
        "mediaUrls": [video_url],
    })
    result = data.get("postCreate", {})
    errors = result.get("userErrors", [])
    if errors:
        raise SystemExit(f"[post_buffer] Post errors for {service}: {errors}")
    return result.get("post", {})


def build_caption(script_path: str, caption_file: str, service: str) -> str:
    if os.path.exists(caption_file):
        text = open(caption_file, encoding="utf-8").read().strip()
        if text:
            return text

    if os.path.exists(script_path):
        with open(script_path, encoding="utf-8") as f:
            script = json.load(f)
        lesson = script.get("lesson", "")
        author = script.get("author", "")
        tags   = script.get("hashtags_instagram", "#motivation #success #history")
        return f"{lesson}\n\n— {author}\n\n{tags}"

    return "#motivation #success"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-url",    required=True)
    ap.add_argument("--script",       default="build/script.json")
    ap.add_argument("--caption-file", default="build/caption_meta.txt")
    ap.add_argument("--services",     default="tiktok,facebook",
                    help="Comma-separated Buffer service names to post to.")
    args = ap.parse_args()

    token = os.environ.get("BUFFER_TOKEN", "").strip()
    if not token:
        raise SystemExit("[post_buffer] BUFFER_TOKEN environment variable not set.")

    services = [s.strip().lower() for s in args.services.split(",") if s.strip()]
    caption  = build_caption(args.script, args.caption_file, services[0] if services else "")

    print(f"[post_buffer] targeting services: {services}")
    channels = get_channels(token, services)

    for ch in channels:
        cid  = ch["id"]
        svc  = ch.get("service", "?")
        name = ch.get("name", "?")
        print(f"[post_buffer] posting to {svc}:{name} ({cid})...")
        post = post_video(token, cid, svc, args.video_url, caption)
        print(f"[post_buffer] {svc}:{name} — id={post.get('id')} status={post.get('status')}")


if __name__ == "__main__":
    main()
