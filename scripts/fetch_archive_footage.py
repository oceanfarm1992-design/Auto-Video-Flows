#!/usr/bin/env python3
"""
Stage 2: pull a public-domain B-roll clip from an allowlisted archive.org collection.

Approach (uses archive.org's documented APIs, no screen-scraping):
  1. Query the advancedsearch API for movie items in an allowlisted collection
     (prelinger / nasa). These collections are curated public domain.
       https://archive.org/advancedsearch.php?q=...&fl[]=identifier&output=json
  2. Pick one item (rotated by date for variety).
  3. Read that item's file manifest via the metadata API:
       https://archive.org/metadata/<identifier>
  4. Choose the most convenient downloadable video file (prefer .mp4, else .ogv/.mpeg)
     and download it from https://archive.org/download/<identifier>/<file>

Output: build/footage.mp4   (raw source clip; assemble stage crops/trims it)
        build/footage.json  (identifier + source URL, for attribution logging)

Usage:
    python scripts/fetch_archive_footage.py
    python scripts/fetch_archive_footage.py --collection nasa --out build
"""
import argparse
import datetime
import json
import os
import sys

import requests

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/{identifier}"
DOWNLOAD_URL = "https://archive.org/download/{identifier}/{filename}"

# Preference order for source video containers ffmpeg can happily read.
VIDEO_EXT_PREFERENCE = (".mp4", ".ogv", ".mpeg", ".mpg", ".mov", ".m4v")

HEADERS = {"User-Agent": "yt-shorts-generator/1.0 (personal public-domain pipeline)"}


def search_collection(collection, rows=50):
    """Return a list of item identifiers in an allowlisted collection."""
    params = {
        "q": f"collection:{collection} AND mediatype:movies",
        "fl[]": "identifier",
        "rows": rows,
        "output": "json",
        # Sort by downloads desc so we tend to get well-formed, popular items.
        "sort[]": "downloads desc",
    }
    r = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=60)
    r.raise_for_status()
    docs = r.json()["response"]["docs"]
    return [d["identifier"] for d in docs if d.get("identifier")]


def pick_playable_file(identifier, cfg):
    """Return (filename, size_bytes) of a suitable video file, or None."""
    r = requests.get(METADATA_URL.format(identifier=identifier),
                     headers=HEADERS, timeout=60)
    r.raise_for_status()
    meta = r.json()
    files = meta.get("files", [])

    candidates = []
    for f in files:
        name = f.get("name", "")
        lower = name.lower()
        if not lower.endswith(VIDEO_EXT_PREFERENCE):
            continue
        length = f.get("length")  # seconds, string; may be absent
        try:
            seconds = float(length) if length is not None else None
        except (TypeError, ValueError):
            seconds = None
        if seconds is not None:
            if seconds < cfg["min_source_seconds"] or seconds > cfg["max_source_seconds"]:
                continue
        # rank by extension preference
        ext_rank = next(i for i, e in enumerate(VIDEO_EXT_PREFERENCE)
                        if lower.endswith(e))
        size = int(f.get("size", 0) or 0)
        candidates.append((ext_rank, size, name))

    if not candidates:
        return None
    # prefer best container, then smallest reasonable file (faster CI download)
    candidates.sort(key=lambda c: (c[0], c[1]))
    _, size, name = candidates[0]
    return name, size


def download(identifier, filename, dest):
    url = DOWNLOAD_URL.format(identifier=identifier, filename=filename)
    print(f"[fetch_archive_footage] downloading {url}")
    with requests.get(url, headers=HEADERS, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as out:
            for chunk in r.iter_content(chunk_size=1 << 20):
                out.write(chunk)
    return url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sources.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--collection", default=None,
                    help="Force a specific allowlisted collection.")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)
    ac = config["archive_collections"]
    allowlist = ac["allowlist"]

    if args.collection:
        if args.collection not in allowlist:
            raise SystemExit(f"Collection '{args.collection}' not in allowlist {allowlist}")
        collection = args.collection
    else:
        # rotate collection by day for variety
        day = datetime.date.today().timetuple().tm_yday
        collection = allowlist[day % len(allowlist)]

    print(f"[fetch_archive_footage] using collection: {collection}")
    identifiers = search_collection(collection)
    if not identifiers:
        raise SystemExit(f"No items found in collection {collection}")

    day = datetime.date.today().timetuple().tm_yday
    # try a few items in case the first has no usable video file
    os.makedirs(args.out, exist_ok=True)
    tried = 0
    for offset in range(len(identifiers)):
        identifier = identifiers[(day + offset) % len(identifiers)]
        picked = pick_playable_file(identifier, ac)
        tried += 1
        if not picked:
            continue
        filename, size = picked
        dest = os.path.join(args.out, "footage.mp4")
        url = download(identifier, filename, dest)
        with open(os.path.join(args.out, "footage.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "collection": collection,
                    "identifier": identifier,
                    "source_file": filename,
                    "source_url": url,
                    "archive_item": f"https://archive.org/details/{identifier}",
                    "license": "Public Domain (archive.org allowlisted collection)",
                },
                f, indent=2,
            )
        print(f"[fetch_archive_footage] saved {dest} from {identifier} "
              f"({size/1e6:.1f} MB), tried {tried} item(s)")
        return

    print("ERROR: no usable video file found in any tried item.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
