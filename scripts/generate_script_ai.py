#!/usr/bin/env python3
"""
Stage 1 (AI): Generate a historical success story script using OpenAI GPT.

Topic selection tries, in order:
  1. Trending: a web-search-grounded GPT call (see pick_trending_topic) looks at
     today's YouTube "most popular" chart plus live web search and picks ONE real,
     specific, named person/organization whose current relevance (an achievement,
     comeback, viral moment, milestone, anniversary) fits a motivational
     success-story short. It must stay biographically factual — no invented
     "trending reason" if none is verifiable.
  2. Static: config/topics.json's curated historical-figures list (avoiding
     recently used ones), same as before. Used whenever trending selection finds
     no good fit, errors, or --topic-index forces a specific static entry.

Either way, GPT then writes a unique narration, hook, platform captions, SEO
metadata, hashtags, and a footage search query tailored to the story.

Output:
    build/script.json   – full structured data consumed by later pipeline stages
    build/script.txt    – plain narration text for TTS

Usage:
    python scripts/generate_script_ai.py
    python scripts/generate_script_ai.py --topic-index 5     # force a specific static topic (skips trending)
    python scripts/generate_script_ai.py --no-trending       # static topic only
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

import fetch_trending_topic

USED_LOG = Path("logs/used_topics.json")
TOPICS_CONFIG = Path("config/topics.json")
BUILD_DIR = Path("build")

TRENDING_SYSTEM_PROMPT = (
    "You find real, current angles for a motivational short-form video series about "
    "historical and modern success stories. Use web search to check what's genuinely "
    "trending or newsworthy right now. Cross-reference with the YouTube trending "
    "titles you're given, but you are not limited to them.\n\n"
    "Pick ONE real, specific, named person or organization whose CURRENT relevance "
    "(a recent achievement, comeback, viral moment, record, milestone, or anniversary "
    "you can verify) would make a compelling success-story video today. The "
    "biographical facts you cite must be real and verifiable — never invent a "
    "'trending reason' you can't confirm.\n\n"
    "If nothing right now fits this format well, say so honestly.\n\n"
    "Respond with ONLY valid JSON (no markdown fences), exactly these keys:\n"
    '{"fit": true | false, "name": "...", "field": "...", "theme": "...", '
    '"era": "modern", "trend_reason": "...", "hook_line": "..."}\n'
    '"trend_reason" is one sentence on why this is relevant right now (empty string '
    'if fit is false). "hook_line" is a punchy ALL-CAPS-style spoken opening line '
    "(max ~15 words) that leads with that current relevance to stop the scroll — "
    'leave it an empty string if fit is false.'
)

SYSTEM_PROMPT = (
    "You are a world-class motivational video scriptwriter. "
    "Your scripts are designed to STOP the scroll and reprogram the viewer's mindset. "
    "Style rules: short punchy sentences. Use ellipses (...) for dramatic pauses. "
    "Use ALL CAPS for 1-2 key words per paragraph for emphasis. "
    "Build a clear emotional arc: SHOCK opening → raw human story → pivotal moment → mind-shifting lesson. "
    "Be specific with real details (years, numbers, names). No clichés. "
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

def parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def pick_trending_topic(client: OpenAI, model: str) -> dict | None:
    """Try to find a real, current angle via web search + YouTube's trending
    chart. Returns a topic dict (name/field/theme/era + trend_reason/hook_line)
    on a good fit, or None if nothing fits, the call fails, or the response
    can't be parsed — any of which just falls through to the static list."""
    youtube_titles = fetch_trending_topic.get_youtube_trending(
        os.environ.get("YOUTUBE_API_KEY", "")
    )
    user_content = "Today's YouTube trending video titles:\n" + (
        "\n".join(f"- {t}" for t in youtube_titles) if youtube_titles
        else "(none available — rely on web search alone)"
    )
    try:
        response = client.responses.create(
            model=model,
            tools=[{"type": "web_search"}],
            input=[
                {"role": "system", "content": TRENDING_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
        result = parse_json_response(response.output_text)
    except Exception as exc:  # noqa: BLE001 — trending is a nice-to-have, never fatal
        print(f"[generate_script_ai] trending topic lookup failed ({type(exc).__name__}: {exc}); "
              f"falling back to the static topic list.")
        return None

    if not result.get("fit") or not result.get("name"):
        print("[generate_script_ai] No good trending fit today; falling back to the static topic list.")
        return None

    return {
        "name": result["name"],
        "field": result.get("field", "achievement"),
        "theme": result.get("theme", "current success"),
        "era": result.get("era", "modern"),
        "trend_reason": result.get("trend_reason", ""),
        "hook_line": result.get("hook_line", ""),
    }


def build_prompt(topic: dict, story_format: str) -> str:
    name = topic["name"]
    field = topic["field"]
    theme = topic["theme"]
    era = topic["era"]
    trend_reason = topic.get("trend_reason")
    hook_line = topic.get("hook_line")
    trend_note = (
        f'\n\nThis story is TRENDING right now: {trend_reason}. Open with a hook '
        f'built around that (you may use or adapt this line: "{hook_line}") — but '
        f"every biographical fact you state must still be real and verifiable; "
        f"don't invent details just because the topic is trending."
        if trend_reason else ""
    )
    return f"""Write a motivational short-form video script about {name} ({era}), theme: "{theme}", field: {field}, format: {story_format.replace("_", " ")}.{trend_note}

Return ONLY valid JSON with these exact keys (no markdown, no code fences):

{{
  "title": "Hook title max 8 words ALL CAPS",
  "narration": "Spoken script EXACTLY 220-260 words. SHOCK opening sentence. Specific real story with dates and numbers. Short punchy sentences. Ellipses for pauses. ALL CAPS on 1-2 key words per section. End with a mind-shifting lesson the viewer will remember all day.",
  "segments": [
    {{
      "text": "Exact opening words from narration for segment 1 (~50 words). The SHOCK hook.",
      "footage_query": "dark gritty struggle failure person cinematic — MUST look different from other segments",
      "media_type": "video"
    }},
    {{
      "text": "Exact words for segment 2 from narration (~70 words). The raw human story.",
      "footage_query": "historical archive documentary specific scene — DIFFERENT visual from segment 1",
      "media_type": "video"
    }},
    {{
      "text": "Exact words for segment 3 from narration (~70 words). The pivotal turning point.",
      "footage_query": "dramatic light breakthrough moment — person achievement triumph — DIFFERENT from above",
      "media_type": "photo"
    }},
    {{
      "text": "Exact words for segment 4 from narration (~60 words). The mind-shifting lesson.",
      "footage_query": "abstract nature sky mountains sunrise — COMPLETELY different visual from all above segments",
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
        max_tokens=1200,  # longer narration needs more output tokens
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
    ap.add_argument("--trending-model", default="gpt-4o",
                     help="Model used for trending-topic selection (needs web search).")
    ap.add_argument("--no-trending", action="store_true",
                     help="Skip trending lookup and always use the static topic list.")
    args = ap.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY environment variable not set.")

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)

    topics = config["historical_figures"]
    formats = config["story_formats"]

    client = OpenAI(api_key=api_key)

    topic_source = "static"
    topic = None
    if args.topic_index is None and not args.no_trending:
        topic = pick_trending_topic(client, args.trending_model)
        if topic:
            topic_source = "trending"
            print(f"[generate_script_ai] Trending fit: {topic['name']} — {topic['trend_reason']}")

    if topic is None:
        topic = pick_topic(topics, args.topic_index)

    story_format = random.choice(formats)

    print(f"[generate_script_ai] Topic: {topic['name']} | Source: {topic_source} | "
          f"Format: {story_format} | Model: {args.model}")

    prompt = build_prompt(topic, story_format)
    data = call_openai(client, prompt, args.model)

    # Normalise: ensure required keys exist
    data.setdefault("id", topic["name"].lower().replace(" ", "_"))
    data.setdefault("hook", data.get("title", topic["name"].upper()))
    data["text"] = data.get("lesson", "")  # short quote used in captions
    data["narration"] = data.get("narration", "")
    data["topic_source"] = topic_source

    # Footage query goes into the script so fetch_footage.py can read it
    data["footage_query"] = data.get("footage_query", f"{topic['field']} cinematic")

    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)

    (out_dir / "script.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "script.txt").write_text(data["narration"], encoding="utf-8")

    write_platform_captions(data, out_dir)
    if topic_source == "static":
        save_used(load_used(), topic["name"])

    words = len(data["narration"].split())
    print(f"[generate_script_ai] Done. {words} narration words. "
          f"Footage query: '{data['footage_query']}'")

if __name__ == "__main__":
    main()
