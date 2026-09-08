#!/usr/bin/env python3
"""
Post a video to TikTok via Buffer's GraphQL API.

Buffer deprecated its legacy REST API (/1/*) — it now rejects public API
tokens with HTTP 401. This script uses the current GraphQL endpoint at
https://graph.buffer.com/ with a Bearer token.

The BUFFER_TOKEN secret must be a valid Buffer access token with
'publish' scope. Generate one at:
  https://developers.buffer.com  →  Create App  →  Access Token

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

BUFFER_GRAPHQL = "https://graph.buffer.com/"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "yt-shorts-generator/1.0",
}


def gql(token: str, query: str, variables: dict = None) -> dict:
    resp = requests.post(
        BUFFER_GRAPHQL,
        json={"query": query, "variables": variables or {}},
        headers={**HEADERS, "Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"[post_buffer_tiktok] GraphQL HTTP {resp.status_code}: {resp.text[:500]}",
              file=sys.stderr)
        resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        raise SystemExit(f"[post_buffer_tiktok] GraphQL errors: {body['errors']}")
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


def get_tiktok_channel_ids(token: str) -> list:
    data = gql(token, GET_CHANNELS)
    channels = data.get("channels", [])
    if not channels:
        raise SystemExit(
            "[post_buffer_tiktok] No channels returned. "
            "Check that your BUFFER_TOKEN has 'publish' scope and "
            "at least one channel is connected at buffer.com."
        )

    print(f"[post_buffer_tiktok] Connected channels: "
          + ", ".join(f"{c.get('service')}:{c.get('name')}" for c in channels))

    tiktok = [c["id"] for c in channels
              if c.get("service", "").lower() == "tiktok" and c.get("isConnected")]
    if not tiktok:
        services = [f"{c.get('service')}:{c.get('name')}" for c in channels]
        raise SystemExit(
            f"[post_buffer_tiktok] No connected TikTok channel found. "
            f"Connected: {services}. "
            "Connect TikTok at buffer.com/channels before running."
        )
    return tiktok


def post_video(token: str, channel_id: str, video_url: str, caption: str) -> dict:
    data = gql(token, CREATE_POST, {
        "channelId": channel_id,
        "text": caption,
        "mediaUrls": [video_url],
    })
    result = data.get("postCreate", {})
    errors = result.get("userErrors", [])
    if errors:
        raise SystemExit(f"[post_buffer_tiktok] Post errors: {errors}")
    return result.get("post", {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-url", required=True)
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--caption-file", default="build/caption_tiktok.txt")
    args = ap.parse_args()

    token = os.environ.get("BUFFER_TOKEN", "").strip()
    if not token:
        raise SystemExit("[post_buffer_tiktok] BUFFER_TOKEN environment variable not set.")

    # Load caption
    caption = ""
    if os.path.exists(args.caption_file):
        caption = open(args.caption_file, encoding="utf-8").read().strip()

    if not caption and os.path.exists(args.script):
        with open(args.script, encoding="utf-8") as f:
            script = json.load(f)
        lesson = script.get("lesson", "")
        author = script.get("author", "")
        tags   = script.get("hashtags_instagram", "#motivation #success #history")
        caption = f"{lesson}\n\n— {author}\n\n{tags}"

    caption = caption[:2200]  # TikTok caption limit

    print("[post_buffer_tiktok] fetching Buffer channels...")
    channel_ids = get_tiktok_channel_ids(token)
    print(f"[post_buffer_tiktok] found {len(channel_ids)} TikTok channel(s): {channel_ids}")

    for cid in channel_ids:
        print(f"[post_buffer_tiktok] posting to channel {cid}...")
        post = post_video(token, cid, args.video_url, caption)
        print(f"[post_buffer_tiktok] posted — id={post.get('id')} status={post.get('status')}")


if __name__ == "__main__":
    main()
