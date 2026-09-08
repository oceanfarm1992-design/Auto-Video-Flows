#!/usr/bin/env python3
"""
Stage 1 (AI): Generate a historical success story script using OpenAI GPT.

Picks a historical figure from config/topics.json (avoiding recently used ones),
then calls GPT to write a unique narration, hook, platform captions, SEO metadata,
hashtags, and a footage search query tailored to the story.

Output:
    build/script.json   – full structured data consumed by later pipeline stages
    build/script.txt    – plain narration text for TTS

Usage:
    python scripts/generate_script_ai.py
    python scripts/generate_script_ai.py --topic-index 5   # force a specific topic
    python scripts/generate_script_ai.py --model gpt-4o-mini
"""
import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    sys.exit("openai package not installed. Run: pip install openai")

USED_LOG = Path("logs/used_topics.json")
TOPICS_CONFIG = Path("config/topics.json")
BUILD_DIR = Path("build")

SYSTEM_PROMPT = (
    "Scriptwriter for a motivational short-form video channel. "
    "Be concise, vivid, educational. "
    "Narration style: storytelling, present-tense. "
    "No clichés ('journey', 'passion', 'hustle'). "
    "Output ONLY valid JSON."
)

def load_used() -> set:
    if USED_LOG.exists():
        data = json.loads(USED_LOG.read_text(encoding="utf-8"))
        return set(data.get("used", []))
    return set()

def save_used(used: set, new_name: str):
    USED_LOG.parent.mkdir(exist_ok=True)
    # keep last 50 so the pool resets naturally
    used_list = list(used)[-49:] + [new_name]
    USED_LOG.write_text(
        json.dumps({"used": used_list}, indent=2),
        encoding="utf-8"
    )

def pick_topic(topics: list, force_index: int | None) -> dict:
    if force_index is not None:
        return topics[force_index % len(topics)]
    used = load_used()
    available = [t for t in topics if t["name"] not in used]
    if not available:
        available = topics  # full reset once all used
    return random.choice(available)

def build_prompt(topic: dict, story_format: str) -> str:
    name = topic["name"]
    field = topic["field"]
    theme = topic["theme"]
    era = topic["era"]
    return f"""Write a motivational short-form video script about {name} ({era}), theme: "{theme}", field: {field}, format: {story_format.replace("_", " ")}.

Return ONLY valid JSON with these exact keys (no markdown, no code fences):

{{
  "title": "Hook title max 8 words ALL CAPS",
  "narration": "Full spoken script 180-220 words. Strong hook opening. Vivid specific story. Lesson at end. No clichés.",
  "segments": [
    {{
      "text": "Exact opening words of narration for segment 1 (hook/intro, ~40 words)",
      "footage_query": "4-5 word Pexels search query matching this segment visually",
      "media_type": "video"
    }},
    {{
      "text": "Exact words for segment 2 (the story/struggle, ~60 words)",
      "footage_query": "specific visual query for this moment",
      "media_type": "video"
    }},
    {{
      "text": "Exact words for segment 3 (the turning point, ~50 words)",
      "footage_query": "specific visual query for this moment",
      "media_type": "photo"
    }},
    {{
      "text": "Exact words for segment 4 (lesson/call to action, ~50 words)",
      "footage_query": "inspirational abstract visual query",
      "media_type": "video"
    }}
  ],
  "lesson": "One sentence takeaway",
  "footage_query": "4-6 word fallback query for overall story",
  "seo_title": "YouTube title max 60 chars with person name",
  "seo_description": "YouTube description 80-120 words: story + lesson + CTA + 3-5 keyword phrases",
  "hashtags_instagram": "8-10 hashtags including person name and field",
  "hashtags_youtube": "#Shorts #motivation 5-6 hashtags",
  "hashtags_facebook": "5-6 hashtags",
  "caption_instagram": "Instagram caption: hook in first 125 chars, full 150-200 chars with hashtags",
  "caption_facebook": "Facebook caption 100-140 chars, conversational",
  "caption_youtube": "YouTube caption 100-120 chars keyword-rich",
  "author": "{name}",
  "field": "{field}",
  "era": "{era}"
}}

IMPORTANT: The segment texts must together form the complete narration in order with no gaps.
Each footage_query must be specific to that segment's visual moment, not generic."""

def call_openai(client: OpenAI, prompt: str, model: str) -> dict:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.85,
        max_tokens=900,  # gpt-4o-mini is concise; 900 tokens covers full output
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content
    return json.loads(raw)

def write_platform_captions(data: dict, out_dir: Path):
    files = {
        "caption_meta.txt": data.get("caption_instagram", ""),
        "caption_facebook.txt": data.get("caption_facebook", ""),
        "caption_youtube.txt": data.get("caption_youtube", ""),
        "yt_title.txt": data.get("seo_title", data["title"]),
        "yt_description.txt": data.get("seo_description", ""),
    }
    for name, content in files.items():
        (out_dir / name).write_text(content.strip() + "\n", encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(TOPICS_CONFIG))
    ap.add_argument("--out", default=str(BUILD_DIR))
    ap.add_argument("--topic-index", type=int, default=None)
    ap.add_argument("--model", default="gpt-4o-mini")
    args = ap.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY environment variable not set.")

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)

    topics = config["historical_figures"]
    formats = config["story_formats"]

    topic = pick_topic(topics, args.topic_index)
    story_format = random.choice(formats)

    print(f"[generate_script_ai] Topic: {topic['name']} | Format: {story_format} | Model: {args.model}")

    client = OpenAI(api_key=api_key)
    prompt = build_prompt(topic, story_format)
    data = call_openai(client, prompt, args.model)

    # Normalise: ensure required keys exist
    data.setdefault("id", topic["name"].lower().replace(" ", "_"))
    data.setdefault("hook", data.get("title", topic["name"].upper()))
    data["text"] = data.get("lesson", "")  # short quote used in captions
    data["narration"] = data.get("narration", "")

    # Footage query goes into the script so fetch_footage.py can read it
    data["footage_query"] = data.get("footage_query", f"{topic['field']} cinematic")

    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)

    (out_dir / "script.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "script.txt").write_text(data["narration"], encoding="utf-8")

    write_platform_captions(data, out_dir)
    save_used(load_used(), topic["name"])

    words = len(data["narration"].split())
    print(f"[generate_script_ai] Done. {words} narration words. "
          f"Footage query: '{data['footage_query']}'")

if __name__ == "__main__":
    main()
