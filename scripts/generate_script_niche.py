#!/usr/bin/env python3
"""
Stage 1 for topic-based series (e.g. "Why men..." and human psychology / power).

Unlike generate_script_ai.py (real people, trending stories), these series pick a
topic from a curated list in config/niches/<series>.json and have GPT write a
short story-driven psychology script. The output uses the same build/script.json
schema, so every downstream stage (fact-check, TTS, captions, footage,
assembly, posting) is reused unchanged.

Why it is written the way it is (retention, not information):
  - a micro-story with one illustrative character, not a lecture about the topic
  - a hook that creates a curiosity gap, a reveal, a twist, a closing line that
    echoes the hook and asks for a share
  - many short beats (SEGMENTS) so the picture changes every ~4 seconds
  - a lint pass (script_quality.py) rejects filler phrases and lecture rhythm and
    asks the model to rewrite, up to three times

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
from script_quality import lint_narration, rebuild_segments, segments_match

try:  # doodle scene menu so the writer can pick a drawable scene per beat
    from doodle_scenes import SCENE_FUNCS, pick_scene, scene_menu
except ImportError:  # Pillow/numpy missing: segments just get no scene id
    SCENE_FUNCS = {}

    def scene_menu() -> str:
        return ""

    def pick_scene(text, used=None):
        return ""

RECENT_WINDOW = 15
N_SEGMENTS = 10                  # one new picture roughly every four seconds
MIN_WORDS, MAX_WORDS = 80, 120   # what the lint asks for
HARD_MIN, HARD_MAX = 60, 150     # below/above this the run fails instead of publishing

SYSTEM_PROMPT = (
    "You are a top short-form video writer for a psychology channel (TikTok, Reels, Shorts). "
    "Your scripts are spoken by one voice over cartoon stick-figure animation, so every line must "
    "work for the ear and suggest something drawable. You write like a gifted storyteller talking to "
    "one person: concrete, specific, emotionally true, never preachy, never a lecture. Ground ideas in "
    "widely accepted psychology. Do not cite studies, researchers or statistics unless you are certain "
    "they are real and accurate, and never invent quotes or numbers. Illustrative characters are "
    "welcome when framed as illustrative ('Picture Daniel', 'Take a man like Marcus'); never claim "
    "they are real people. Describe tendencies, not universals. Be respectful of every gender and "
    "group. No medical, legal or financial advice. Output ONLY valid JSON."
)

STRUCTURE = """STORY STRUCTURE (follow in this order; do not print the labels):
1. HOOK: the first sentence, 12 words or fewer. A specific, slightly surprising claim or scene that makes someone stop scrolling and need the answer. NEVER restate the topic as a general statement.
2. SCENE: meet one illustrative person ("Picture Daniel...", "Take a man like Marcus...") in one concrete moment with a sensory detail. Use a fresh first name.
3. TENSION: what he does that looks strange or wrong. Hold back the reason for a beat.
4. REVEAL: the real psychological reason, in plain words. One idea only.
5. TWIST: flip how the viewer saw it. A reframe that lands emotionally.
6. CLOSE: one short line that echoes or answers the hook and ends with a trigger to share or comment ("Send this to...", "Would you have guessed?")."""

STYLE = """STYLE RULES:
- {words} words in total, spoken at a calm pace. Count them.
- Talk like a smart friend: contractions, plain words, no jargon.
- Rhythm: mix very short sentences (2-5 words) with longer ones. Several short punches.
- Concrete beats abstract: ages, times, objects, places. Every idea needs an example.
- Write numbers as words (forty-one, nine years). No digits.
- At most TWO ellipses in the whole script. No ALL-CAPS words.
- Never write: "Remember,", "It's important to", "In today's world", "journey", "society", "vulnerability is a strength", "a little X can go a long way", "understanding this", "deeper connection". No moral lecture at the end."""


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


def schema_example(n_segments: int) -> str:
    seg = {
        "text": "the exact next 7-13 words of the narration, nothing skipped",
        "scene": "ONE id from the SCENE LIST that illustrates this beat",
        "footage_query": "3-6 word generic stock search for this beat (an action or setting, never a named person)",
        "media_type": "video",
    }
    obj = {
        "title": "On-screen hook card, max 7 words, ALL CAPS, makes you need the answer",
        "narration": "the full spoken script",
        "segments": [seg, f"... exactly {n_segments} objects like this in total, in order, together they are the whole narration"],
        "lesson": "one-sentence takeaway",
        "footage_query": "3-6 word fallback stock search for the whole video",
        "seo_title": "YouTube Shorts title, max 70 characters, keyword-rich, curiosity",
        "seo_description": "YouTube description, 50-80 words: what the video explains + a question for the comments + 3-5 keyword phrases",
        "hashtags_instagram": "8-10 relevant hashtags",
        "hashtags_youtube": "#Shorts plus 4-5 relevant hashtags",
        "hashtags_facebook": "5-6 hashtags",
        "hashtags_tiktok": "6-8 hashtags mixing broad (#fyp) and niche tags",
        "caption_instagram": "Instagram caption: hook in the first 125 characters, 150-200 characters total with hashtags",
        "caption_facebook": "Facebook caption, 100-140 characters, conversational",
        "caption_youtube": "YouTube caption, 100-120 characters, keyword-rich",
        "caption_tiktok": "TikTok caption: punchy hook in the first 60 characters, under 150 characters total including hashtags",
    }
    return json.dumps(obj, indent=2, ensure_ascii=False)


def build_prompt(cfg: dict, topic: str, tiktok_keywords: list[str]) -> str:
    seo_note = (
        "\n\nFor caption_tiktok/hashtags_tiktok, naturally weave in 2-3 of these high-search "
        f"terms where they genuinely fit (skip any that don't): {', '.join(tiktok_keywords)}."
        if tiktok_keywords else ""
    )
    examples = "\n\n".join(
        f'Topic: {ex["topic"]}\nHook card: {ex["title"]}\nScript: {ex["narration"]}'
        for ex in cfg.get("examples", [])
    )
    example_block = (
        "\nGOLD-STANDARD EXAMPLES (match this voice, rhythm and energy; never copy their "
        f"content or character names):\n\n{examples}\n" if examples else ""
    )
    scenes = scene_menu()
    scene_block = f"\nSCENE LIST (stick-man doodle scenes; use each id at most once):\n{scenes}\n" if scenes else ""
    return f"""Write a short-form video script for a channel about: {cfg["channel_description"]}.
Tone: {cfg["tone"]}.
Topic of this video: "{topic}"{seo_note}

{STRUCTURE}

{STYLE.format(words=f"{MIN_WORDS + 5}-{MAX_WORDS - 15}")}
{example_block}{scene_block}
Return ONLY valid JSON (no markdown, no code fences) shaped like this:

{schema_example(N_SEGMENTS)}"""


def call_openai(client: OpenAI, prompt: str, model: str) -> dict:
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        temperature=0.9,
        max_tokens=2600,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def generate(client: OpenAI, cfg: dict, topic: str, model: str, keywords: list[str]) -> dict:
    """Write, lint, and ask for a rewrite (up to three drafts) until the script has no
    filler, the right rhythm and length. Only a hopeless length is fatal."""
    base = build_prompt(cfg, topic, keywords)
    feedback = ""
    best, best_issues = None, None
    for attempt in (1, 2, 3):
        data = call_openai(client, base + feedback, model)
        issues = lint_narration(str(data.get("narration", "")), MIN_WORDS, MAX_WORDS)
        print(f"[generate_script_niche] draft {attempt}: "
              f"{len(str(data.get('narration', '')).split())} words, "
              f"{'clean' if not issues else '; '.join(issues)}")
        if best is None or len(issues) < len(best_issues):
            best, best_issues = data, issues
        if not issues:
            return data
        feedback = ("\n\nYour previous draft had these problems. Write a NEW draft that fixes every one "
                    "of them while keeping the story structure:\n- " + "\n- ".join(issues))
    words = len(str(best.get("narration", "")).split())
    if HARD_MIN <= words <= HARD_MAX:
        print("[generate_script_niche] using the best draft despite remaining lint issues")
        return best
    sys.exit(f"Narration length {words} out of range after 3 drafts.")


def build_segments(data: dict, cfg: dict) -> list[dict]:
    """N_SEGMENTS beats that together are the narration, each with a scene id."""
    narration = data.get("narration", "")
    raw = data.get("segments") or []
    if segments_match(raw, narration, N_SEGMENTS):
        texts = [str(s["text"]).strip() for s in raw]
    else:
        print("[generate_script_niche] model segments don't match the narration; rebuilding them")
        texts = rebuild_segments(narration, N_SEGMENTS)
        raw = []
    used: list[str] = []
    segments = []
    for i, text in enumerate(texts):
        given = raw[i] if i < len(raw) else {}
        scene = given.get("scene") if given.get("scene") in SCENE_FUNCS and given.get("scene") not in used else ""
        scene = scene or pick_scene(text, used)
        if scene:
            used.append(scene)
        segments.append({
            "text": text,
            "scene": scene,
            "footage_query": given.get("footage_query") or f"{cfg['field']} {data.get('footage_query', 'cinematic')}".strip(),
            "media_type": "video",
        })
    return segments


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
    data["segments"] = build_segments(data, cfg)
    data["caption_tiktok"] = data.get("caption_tiktok") or data.get("caption_instagram", "")
    data["hashtags_tiktok"] = data.get("hashtags_tiktok") or data.get("hashtags_instagram", "#fyp")
    return data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", required=True, help="Path to config/niches/<series>.json")
    ap.add_argument("--model", default="gpt-4o",
                    help="Writing quality is what keeps people watching; mini writes generic scripts.")
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
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "script.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "script.txt").write_text(data["narration"], encoding="utf-8")
    write_platform_captions(data, out_dir)
    save_used(used_path, load_used(used_path), topic)

    print(f"[generate_script_niche] Done. {len(data['narration'].split())} narration words, "
          f"{len(data['segments'])} segments.")


if __name__ == "__main__":
    main()
