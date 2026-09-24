#!/usr/bin/env python3
"""
Stage 1 for topic-based series (e.g. "Why men..." and human psychology / power).

Unlike generate_script_ai.py (real people, trending stories), these series pick a
topic from a curated list in config/niches/<series>.json and have GPT write a
short psychology-style script. The output uses the same build/script.json
schema, so every downstream stage (fact-check, TTS, captions, footage,
assembly, posting) is reused unchanged.

Output:
    build/script.json, build/script.txt, build/caption_*.txt, build/yt_*.txt
    logs/used_<series>.json  (recently used topics, so they don't repeat)

Usage:
    python scripts/generate_script_niche.py --niche config/niches/men_psychology.json
    python scripts/generate_script_niche.py --niche config/niches/power_psychology.json --topic-index 0
"""
import argparse
import json
import os
import random
import sys
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    sys.exit("openai package not installed. Run: pip install openai")

from generate_script_ai import write_platform_captions

RECENT_WINDOW = 15
MIN_WORDS, MAX_WORDS = 50, 150

SYSTEM_PROMPT = (
    "You write short-form video scripts for a psychology and self-awareness channel. "
    "Style: short punchy sentences, second person, ellipses (...) for pauses, one strong "
    "opening line that stops the scroll, one memorable closing line. Never write words in "
    "ALL CAPS inside the narration. Ground every idea in well-established psychology or "
    "everyday behavior. Only cite a study, statistic, experiment or named researcher if you "
    "are certain it is real and described accurately; otherwise state the idea as a common "
    "pattern with no citation. Never invent numbers or quotes. Frame generalizations as "
    "tendencies ('many', 'often'), never as universal facts. Be respectful of every gender "
    "and group, never insulting or demeaning. No medical, legal or financial advice. "
    "Output ONLY valid JSON."
)


def load_used(path: Path) -> list[str]:
    if path.exists():
        return list(json.loads(path.read_text(encoding="utf-8")).get("used", []))
    return []


def save_used(path: Path, used: list[str], topic: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    trimmed = [t for t in used if t != topic][-(RECENT_WINDOW * 2):] + [topic]
    path.write_text(json.dumps({"used": trimmed}, indent=2, ensure_ascii=False), encoding="utf-8")


def pick_topic(topics: list[str], used: list[str], force_index: int | None) -> str:
    if force_index is not None:
        return topics[force_index % len(topics)]
    recent = set(used[-RECENT_WINDOW:])
    available = [t for t in topics if t not in recent] or topics
    return random.choice(available)


def build_prompt(cfg: dict, topic: str, tiktok_keywords: list[str]) -> str:
    seo_note = (
        "\n\nFor caption_tiktok/hashtags_tiktok, naturally weave in 2-3 of these high-search "
        f"terms where they genuinely fit (skip any that don't): {', '.join(tiktok_keywords)}."
        if tiktok_keywords else ""
    )
    return f"""Write a short-form video script for a channel about: {cfg["channel_description"]}.
Tone: {cfg["tone"]}.
Topic of this video: "{topic}"{seo_note}

Return ONLY valid JSON with these exact keys (no markdown, no code fences):

{{
  "title": "Hook title, max 8 words, ALL CAPS, curiosity-driven",
  "narration": "Spoken script of 100-120 words (never fewer than 90; count them). First sentence is a scroll-stopping hook. Explain the psychology behind the topic in plain words, with one concrete everyday example. End with one memorable line. No ALL CAPS words.",
  "segments": [
    {{"text": "Exact opening words of the narration (~25 words).", "footage_query": "YOUR OWN 3-6 word stock-footage search for a filmable scene matching THIS segment", "media_type": "video"}},
    {{"text": "Exact next words of the narration (~30 words).", "footage_query": "YOUR OWN 3-6 word search, visually different from segment 1", "media_type": "video"}},
    {{"text": "Exact next words of the narration (~30 words).", "footage_query": "YOUR OWN 3-6 word search, visually different from segments 1-2", "media_type": "photo"}},
    {{"text": "Exact final words of the narration (~25 words).", "footage_query": "YOUR OWN 3-6 word search, visually different from segments 1-3", "media_type": "video"}}
  ],
  "lesson": "One sentence takeaway",
  "footage_query": "3-6 word fallback search for the whole video",
  "seo_title": "YouTube Shorts title, max 70 characters, keyword-rich",
  "seo_description": "YouTube description, 50-80 words: what the video explains + a question for the comments + 3-5 keyword phrases",
  "hashtags_instagram": "8-10 relevant hashtags",
  "hashtags_youtube": "#Shorts plus 4-5 relevant hashtags",
  "hashtags_facebook": "5-6 hashtags",
  "hashtags_tiktok": "6-8 hashtags mixing broad (#fyp) and niche tags",
  "caption_instagram": "Instagram caption: hook in the first 125 characters, 150-200 characters total with hashtags",
  "caption_facebook": "Facebook caption, 100-140 characters, conversational",
  "caption_youtube": "YouTube caption, 100-120 characters, keyword-rich",
  "caption_tiktok": "TikTok caption: punchy hook in the first 60 characters, under 150 characters total including hashtags"
}}

Rules:
- The four segment texts, in order, must together be the complete narration with no gaps.
- Every footage_query is placeholder instruction text, not example output: write your own real search phrase.
  These hit royalty-free stock libraries (Pexels/Pixabay), so describe a generic filmable ACTION, SETTING or OBJECT.
  Never name a real person. Scene ideas for this series: {cfg["footage_hint"]}."""


def call_openai(client: OpenAI, prompt: str, model: str) -> dict:
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        temperature=0.8,
        max_tokens=1500,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def generate(client: OpenAI, cfg: dict, topic: str, model: str, keywords: list[str]) -> dict:
    """One retry if GPT returns a narration outside the usable length range."""
    prompt = build_prompt(cfg, topic, keywords)
    for attempt in (1, 2, 3):
        data = call_openai(client, prompt, model)
        words = len(str(data.get("narration", "")).split())
        if MIN_WORDS <= words <= MAX_WORDS:
            return data
        print(f"[generate_script_niche] attempt {attempt}: narration has {words} words "
              f"(want {MIN_WORDS}-{MAX_WORDS}); {'retrying' if attempt < 3 else 'giving up'}")
    sys.exit("Narration length out of range after retry.")


def normalise(data: dict, cfg: dict, topic: str) -> dict:
    data["id"] = cfg["id"]
    data["niche"] = cfg["id"]
    data["topic"] = topic
    data["author"] = ""
    data["field"] = cfg["field"]
    data["era"] = ""
    data["topic_source"] = "niche"
    data["hook"] = data.get("title", topic.upper())
    data["text"] = data.get("lesson", "")
    data["narration"] = data.get("narration", "")
    data["footage_query"] = data.get("footage_query") or f"{cfg['field']} cinematic"
    data["caption_tiktok"] = data.get("caption_tiktok") or data.get("caption_instagram", "")
    data["hashtags_tiktok"] = data.get("hashtags_tiktok") or data.get("hashtags_instagram", "#fyp")
    return data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", required=True, help="Path to config/niches/<series>.json")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--topic-index", type=int, default=None)
    ap.add_argument("--out", default="build")
    args = ap.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY environment variable not set.")

    cfg = json.loads(Path(args.niche).read_text(encoding="utf-8"))
    used_path = Path(cfg["used_log"])
    topic = pick_topic(cfg["topics"], load_used(used_path), args.topic_index)

    all_keywords = cfg.get("tiktok_seo_keywords", [])
    keywords = random.sample(all_keywords, min(6, len(all_keywords)))
    print(f"[generate_script_niche] Series: {cfg['id']} | Topic: {topic}")

    data = normalise(generate(OpenAI(api_key=api_key), cfg, topic, args.model, keywords), cfg, topic)

    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)
    (out_dir / "script.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "script.txt").write_text(data["narration"], encoding="utf-8")
    write_platform_captions(data, out_dir)
    save_used(used_path, load_used(used_path), topic)

    print(f"[generate_script_niche] Done. {len(data['narration'].split())} narration words. "
          f"Footage query: '{data['footage_query']}'")


if __name__ == "__main__":
    main()
