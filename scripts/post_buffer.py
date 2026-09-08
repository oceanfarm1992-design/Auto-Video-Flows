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

BUFFER_GRAPHQL = "https://api.buffer.com/"
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


GET_ORG_FULL = """
query {
  account {
    id
    currentOrganization {
      id
    }
  }
}
"""

GET_ORG_SIMPLE = """
query {
  account {
    id
  }
}
"""

GET_ORGS = """
query {
  organizations {
    id
    name
  }
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


def get_org_id(token: str) -> str:
    # Prefer the env var set as BUFFER_ORG_ID secret — fastest, no extra API call
    org_id = os.environ.get("BUFFER_ORG_ID", "").strip()
    if org_id:
        print(f"[post_buffer] organizationId (from env): {org_id}")
        return org_id

    # Fallback: query the API
    for query, path in [
        (GET_ORG_FULL,   lambda d: d.get("account", {}).get("currentOrganization", {}).get("id")),
        (GET_ORG_SIMPLE, lambda d: d.get("account", {}).get("id")),
        (GET_ORGS,       lambda d: (d.get("organizations") or [{}])[0].get("id")),
    ]:
        try:
            data = gql(token, query)
            oid = path(data)
            if oid:
                print(f"[post_buffer] organizationId (from API): {oid}")
                return oid
        except SystemExit:
            pass

    raise SystemExit(
        "[post_buffer] Could not resolve organizationId. "
        "Set BUFFER_ORG_ID as a GitHub Secret or check token scopes."
    )


def get_channels(token: str, services: list) -> list:
    """Return Buffer channel dicts for the requested services."""
    org_id = get_org_id(token)
    data = gql(token, GET_CHANNELS, {"orgId": org_id})
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
        if c.get("service", "").lower() in services and not c.get("isDisconnected")
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

    post_input = {
        "channelId": channel_id,
        "text": caption,
        "assets": [{"video": {"url": video_url}}],
        "mode": "shareNow",           # publish immediately
        "schedulingType": "automatic",  # Buffer auto-publishes (vs. notification)
        "needsApproval": False,
    }
    data = gql(token, CREATE_POST, {"input": post_input})
    result = data.get("createPost", {})
    typename = result.get("__typename")

    if typename == "PostActionSuccess":
        return result.get("post", {}) or {}

    # Any other union member is an error type carrying a message
    msg = result.get("message", "unknown error")
    raise SystemExit(f"[post_buffer] {service} post failed ({typename}): {msg}")


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


def introspect(token: str):
    """Print publishing-related mutations and their input shapes."""
    q = """
    query {
      __schema {
        mutationType {
          fields { name args { name type { name kind ofType { name kind } } } }
        }
      }
    }
    """
    data = gql(token, q)
    fields = data.get("__schema", {}).get("mutationType", {}).get("fields", [])
    print(f"[introspect] {len(fields)} mutations (skipped)")

    # Return type of createPost
    rq = """
    query {
      __type(name: "Mutation") {
        fields {
          name
          type { name kind ofType { name kind } }
        }
      }
    }
    """
    rd = gql(token, rq)
    for f in rd.get("__type", {}).get("fields", []):
        if f["name"] == "createPost":
            ty = f["type"]
            rtn = ty.get("name") or ty.get("ofType", {}).get("name")
            print(f"[introspect] createPost returns: {rtn} ({ty['kind']})")
            # detail that type's fields / union members
            dq = """
            query D($n: String!) {
              __type(name: $n) {
                name kind
                fields { name }
                possibleTypes { name }
              }
            }
            """
            dd = gql(token, dq, {"n": rtn})
            t = dd.get("__type", {})
            print(f"  kind={t.get('kind')}")
            if t.get("fields"):
                print(f"  fields={[x['name'] for x in t['fields']]}")
            if t.get("possibleTypes"):
                print(f"  possibleTypes={[x['name'] for x in t['possibleTypes']]}")

    # Detail the input type for any mutation that looks like publishing
    # Detail object types (success + one error) — fields, not inputFields
    for tname in ("PostActionSuccess", "InvalidInputError"):
        oq = """
        query O($n: String!) {
          __type(name: $n) {
            name kind
            fields { name type { name kind ofType { name kind } } }
          }
        }
        """
        try:
            od = gql(token, oq, {"n": tname})
            t = od.get("__type")
            if t:
                print(f"\n[introspect] {t['kind']} {t['name']} fields:")
                for f in t.get("fields") or []:
                    ty = f["type"]
                    tn = ty.get("name") or ty.get("ofType", {}).get("name")
                    print(f"  {f['name']}: {tn}")
        except SystemExit:
            pass

    # Detail input object types
    for tname in ("VideoAssetInput",):
        tq = """
        query T($n: String!) {
          __type(name: $n) {
            name kind
            inputFields {
              name
              type { name kind ofType { name kind ofType { name kind ofType { name kind } } } }
            }
          }
        }
        """
        try:
            td = gql(token, tq, {"n": tname})
            t = td.get("__type")
            if t:
                print(f"\n[introspect] {t['kind']} {t['name']}:")
                for inf in t.get("inputFields") or []:
                    ty = inf["type"]
                    # drill through NON_NULL/LIST wrappers to the leaf name
                    leaf, wrap = ty, ""
                    while leaf:
                        if leaf["kind"] == "NON_NULL":
                            wrap += "!"
                        elif leaf["kind"] == "LIST":
                            wrap += "[]"
                        if leaf.get("name"):
                            break
                        leaf = leaf.get("ofType")
                    print(f"  {inf['name']}: {leaf.get('name') if leaf else '?'} {wrap}")
        except SystemExit:
            pass

    # Enum values for the required enums
    for ename in ("ShareMode", "SchedulingType"):
        eq = """
        query E($n: String!) {
          __type(name: $n) { name kind enumValues { name } }
        }
        """
        try:
            ed = gql(token, eq, {"n": ename})
            e = ed.get("__type")
            if e:
                vals = [v["name"] for v in e.get("enumValues") or []]
                print(f"\n[introspect] enum {e['name']}: {vals}")
        except SystemExit:
            pass


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

    if os.environ.get("BUFFER_INTROSPECT") == "1":
        introspect(token)
        return

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
        print(f"[post_buffer] ✓ {svc}:{name} — post id={post.get('id')}")


if __name__ == "__main__":
    main()
