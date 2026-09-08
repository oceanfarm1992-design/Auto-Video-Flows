#!/usr/bin/env python3
"""
Post a video to one or more platforms via Buffer's GraphQL API.

Handles TikTok, Facebook and Instagram (and any other service connected in
Buffer). Buffer deprecated its legacy REST API (/1/*); only the GraphQL
endpoint https://api.buffer.com/ is accepted now, with a Bearer token.

Environment:
    BUFFER_TOKEN    Buffer access token with publish scope
                    (from https://developers.buffer.com)
    BUFFER_ORG_ID   Buffer organization id (skips an org-lookup API call)

Usage:
    python scripts/post_buffer.py \
        --video-url "https://..." \
        --script build/script.json \
        --services tiktok,facebook,instagram
"""
import argparse
import json
import os
import sys

import requests

BUFFER_GRAPHQL = "https://api.buffer.com/"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "yt-shorts-generator/1.0",
}

# Caption length limits per platform
CAPTION_LIMITS = {
    "tiktok":    2200,
    "facebook":  63206,
    "instagram": 2200,
    "default":   2200,
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


# ── Queries / mutations ───────────────────────────────────────────────────────

GET_ORG = """
query {
  account { id currentOrganization { id } }
}
"""

GET_CHANNELS = """
query GetChannels($orgId: OrganizationId!) {
  channels(input: { organizationId: $orgId }) {
    id
    service
    name
    isDisconnected
  }
}
"""

CREATE_POST = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id } }
    ... on NotFoundError { message }
    ... on UnauthorizedError { message }
    ... on UnexpectedError { message }
    ... on RestProxyError { message }
    ... on LimitReachedError { message }
    ... on InvalidInputError { message }
  }
}
"""


# ── Organization + channels ─────────────────────────────────────────────────────

def get_org_id(token: str) -> str:
    org_id = os.environ.get("BUFFER_ORG_ID", "").strip()
    if org_id:
        print(f"[post_buffer] organizationId (from env): {org_id}")
        return org_id
    data = gql(token, GET_ORG)
    org_id = (data.get("account", {}).get("currentOrganization", {}) or {}).get("id")
    if not org_id:
        raise SystemExit("[post_buffer] Could not resolve organizationId; "
                         "set the BUFFER_ORG_ID secret.")
    print(f"[post_buffer] organizationId (from API): {org_id}")
    return org_id


def get_channels(token: str, services: list) -> list:
    org_id = get_org_id(token)
    data = gql(token, GET_CHANNELS, {"orgId": org_id})
    all_channels = data.get("channels", [])
    if not all_channels:
        raise SystemExit("[post_buffer] No channels returned for this organization.")

    print("[post_buffer] Connected channels: "
          + ", ".join(f"{c.get('service')}:{c.get('name')}" for c in all_channels))

    matched = [
        c for c in all_channels
        if c.get("service", "").lower() in services and not c.get("isDisconnected")
    ]
    if not matched:
        raise SystemExit(
            f"[post_buffer] No connected channels for {services}. "
            f"Connected: {[c.get('service') for c in all_channels]}."
        )
    return matched


# ── Per-platform post metadata ──────────────────────────────────────────────────

def build_metadata(service: str, caption: str) -> dict | None:
    """Buffer requires a post type for Instagram and Facebook. Vertical shorts
    map to 'reel'. TikTok takes an optional title."""
    svc = service.lower()
    if svc == "instagram":
        return {"instagram": {"type": "reel", "shouldShareToFeed": True}}
    if svc == "facebook":
        return {"facebook": {"type": "reel", "annotations": []}}
    if svc == "tiktok":
        title = caption.split("\n", 1)[0][:150] or "Daily motivation"
        return {"tiktok": {"title": title}}
    return None


def post_video(token: str, channel_id: str, service: str,
               video_url: str, caption: str) -> dict:
    caption = caption[:CAPTION_LIMITS.get(service.lower(), CAPTION_LIMITS["default"])]

    post_input = {
        "channelId": channel_id,
        "text": caption,
        "assets": [{"video": {"url": video_url}}],
        "mode": "shareNow",             # publish immediately
        "schedulingType": "automatic",  # Buffer auto-publishes (vs. notification)
        "needsApproval": False,
    }
    meta = build_metadata(service, caption)
    if meta:
        post_input["metadata"] = meta

    data = gql(token, CREATE_POST, {"input": post_input})
    result = data.get("createPost", {})
    typename = result.get("__typename")

    if typename == "PostActionSuccess":
        return result.get("post", {}) or {}

    msg = result.get("message", "unknown error")
    raise SystemExit(f"[post_buffer] {service} post failed ({typename}): {msg}")


# ── Caption ───────────────────────────────────────────────────────────────────

def build_caption(script_path: str, caption_file: str) -> str:
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


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-url",    required=True)
    ap.add_argument("--script",       default="build/script.json")
    ap.add_argument("--caption-file", default="build/caption_meta.txt")
    ap.add_argument("--services",     default="tiktok,facebook,instagram",
                    help="Comma-separated Buffer service names to post to.")
    args = ap.parse_args()

    token = os.environ.get("BUFFER_TOKEN", "").strip()
    if not token:
        raise SystemExit("[post_buffer] BUFFER_TOKEN environment variable not set.")

    services = [s.strip().lower() for s in args.services.split(",") if s.strip()]
    caption  = build_caption(args.script, args.caption_file)

    print(f"[post_buffer] targeting services: {services}")
    channels = get_channels(token, services)

    failures = []
    for ch in channels:
        cid  = ch["id"]
        svc  = ch.get("service", "?")
        name = ch.get("name", "?")
        print(f"[post_buffer] posting to {svc}:{name} ({cid})...")
        try:
            post = post_video(token, cid, svc, args.video_url, caption)
            print(f"[post_buffer] OK {svc}:{name} — post id={post.get('id')}")
        except SystemExit as e:
            print(str(e), file=sys.stderr)
            failures.append(svc)

    if failures:
        raise SystemExit(f"[post_buffer] failed for: {failures}")


if __name__ == "__main__":
    main()
