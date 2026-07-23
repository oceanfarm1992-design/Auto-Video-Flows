#!/usr/bin/env python3
"""
Stage 1: pick a public-domain excerpt to narrate.

We rotate deterministically through the excerpts listed in config/sources.json so
that every day gets a different one without needing to remember state. The rotation
key is the day-of-year, which is stable within a single day (same across all stages
of one CI run) and cycles through the whole catalogue over time.

Output: build/script.json  { "id", "title", "author", "text", "hook" }
        build/script.txt   (raw narration text, convenient for TTS)

Usage:
    python scripts/fetch_script_text.py                 # auto-pick by date
    python scripts/fetch_script_text.py --index 3        # force a specific flat index
    python scripts/fetch_script_text.py --config config/sources.json --out build
"""
import argparse
import datetime
import json
import os


def load_flat_excerpts(config):
    """Flatten every (text, excerpt) pair into a single indexable list."""
    flat = []
    for text in config["gutenberg_texts"]:
        for excerpt in text["excerpts"]:
            flat.append(
                {
                    "id": text["id"],
                    "title": text["title"],
                    "author": text["author"],
                    "gutenberg_id": text["gutenberg_id"],
                    "text": excerpt.strip(),
                }
            )
    return flat


def make_hook(item):
    """Short punchy title-card line derived from the author/theme."""
    return f"{item['author'].upper()} ON STAYING STRONG"


def write_platform_captions(item, config, out_dir):
    """Emit the caption/title text files the posting scripts read."""
    hashtags = config.get("hashtags", {})
    quote = item["text"]
    attribution = f"— {item['author']}, {item['title']}"
    yt_title = f"{item['author']}: Daily Motivation #Shorts"

    files = {
        "caption_meta.txt": f"{quote}\n\n{attribution}\n\n{hashtags.get('instagram', '')}",
        "caption_tiktok.txt": f"{quote[:120]}\n{hashtags.get('tiktok', '')}",
        "caption_youtube.txt": f"{quote}\n\n{attribution}\n\n{hashtags.get('youtube', '')}",
        "yt_title.txt": yt_title,
    }
    for name, content in files.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(content.strip() + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sources.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--index", type=int, default=None,
                    help="Force a flat excerpt index instead of date-based rotation.")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)

    flat = load_flat_excerpts(config)
    if not flat:
        raise SystemExit("No excerpts found in config.")

    if args.index is not None:
        idx = args.index % len(flat)
    else:
        day_of_year = datetime.date.today().timetuple().tm_yday
        idx = day_of_year % len(flat)

    item = flat[idx]
    item["hook"] = make_hook(item)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "script.json"), "w", encoding="utf-8") as f:
        json.dump(item, f, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "script.txt"), "w", encoding="utf-8") as f:
        f.write(item["text"])

    write_platform_captions(item, config, args.out)

    words = len(item["text"].split())
    print(f"[fetch_script_text] picked index {idx}/{len(flat)}: "
          f"{item['title']} by {item['author']} ({words} words)")


if __name__ == "__main__":
    main()
