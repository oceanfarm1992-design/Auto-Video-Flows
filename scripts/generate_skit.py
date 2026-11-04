#!/usr/bin/env python3
"""
Stage 1 (skit format): GPT turns a niche topic into a short cartoon skit.

The recurring cast from config/niches/<niche>.json acts out the topic as a dialogue
scene in 10-14 shots (one line each, a new picture every ~4 s), and the narrator
(the channel's own voice) closes with the psychology insight in one or two lines.
The structure follows what keeps viewers watching story skits: open in the middle of
a conflict, escalate, hold back the reason, reveal it, land a payoff, then name the
psychology.

Output (same files as generate_script_niche.py, so fact-check, captions and posting
are reused unchanged):
    build/script.json   format=skit, shots[], narration (all lines joined), segments[], SEO fields
    build/script.txt    narration
    build/caption_*.txt, build/yt_*.txt
    logs/used_<niche>.json

Usage:
    python scripts/generate_skit.py --niche config/niches/men_psychology.json
    python scripts/generate_skit.py --niche config/niches/power_psychology.json --topic-index 3
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from pathlib import Path

from generate_script_niche import load_used, pick_topic, save_used
from script_quality import BANNED, _norm
from skit_common import cast_of, load_configs, locations_of

SYSTEM_PROMPT = (
    "You are the head writer of a hit cartoon-skit channel on TikTok, Reels and Shorts. Each video is a "
    "one-minute animated scene with a recurring cast; each character wears a cap with their name. "
    "Your skits teach one real psychology idea through a small, relatable, slightly funny or emotional "
    "moment, never through a lecture. Dialogue is short, natural and spoken out loud by voice actors. "
    "Ground ideas in widely accepted psychology; never cite studies, researchers or statistics, never "
    "invent quotes or numbers, never use real people. Describe tendencies, not universals. Be respectful "
    "of every gender and group. No medical, legal or financial advice. Output ONLY valid JSON."
)

STRUCTURE = """SKIT STRUCTURE ({n_min}-{n_max} shots, one spoken line per shot):
- Shot 1 HOOK: start in the middle of a conflict with a line that makes people stop scrolling (an odd demand, an accusation, a strange choice). No greetings, no set-up.
- Shots 2-4 SETUP: show the behaviour from the topic in one concrete, visual situation.
- Shots 5-8 ESCALATION: the other character misreads it or pushes; tension or comedy builds. Hold the real reason back.
- Shots 9-10 REVEAL: the hero's real reason (or his power move) lands, in plain words, with a "because" or an action.
- Next shot PAYOFF: a reaction that flips the scene: a laugh, a silence, a hug, the loud one deflating.
- LAST shot(s) NARRATOR: speaker "narrator", 1-2 lines, at most {narr_words} words in total: name the psychology in plain words and end with a line that makes people comment or share. The narrator's shot shows the final moment of the scene.

DIALOGUE RULES:
- Each line is 2-{max_line} words, one or two short sentences, the way people really talk (contractions, interruptions, "Wait.", "Seriously?").
- Characters alternate; no character speaks three shots in a row.
- Write numbers as words. No digits. No ALL-CAPS words. At most two ellipses in the whole skit.
- The hero must stay likeable and dignified. Comedy comes from the situation, never from mocking a group.
- Never write: "Remember,", "journey", "society", "it's important to", "deeper connection", "at the end of the day".

VISUAL RULES (these become image and animation prompts):
- Use at most {max_loc} locations, chosen from LOCATIONS by id. Most skits use one or two.
- "characters": the ids visible in the shot (1-3). The speaker is visible unless the speaker is the narrator.
- "action": one sentence describing a single frozen moment that an illustrator can draw: who is where, pose, prop, expression. Refer to characters by their cap names (e.g. "The Wife").
- "motion": one sentence describing what moves during the shot (gestures, a turn, a step, picking something up). Small, physical, clear.
- "framing": wide shot, medium two-shot, over-the-shoulder, or close-up on one character. Vary it; use a close-up for reveals."""


def schema(n_min: int, n_max: int) -> str:
    shot = {
        "speaker": "a cast id, or narrator",
        "line": "the exact words spoken",
        "emotion": "2-4 words of acting direction, e.g. 'annoyed, sharp' or 'quiet, almost smiling'",
        "location": "a location id",
        "characters": ["cast ids visible in the shot"],
        "framing": "wide shot | medium two-shot | over-the-shoulder | close-up on <cap name>",
        "action": "the frozen moment to draw",
        "motion": "what moves during the shot",
        "keyword": "the one word of this line to highlight on screen (or empty)",
    }
    return json.dumps({
        "hook_options": ["three different possible first lines"],
        "title": "on-screen hook card, max 6 words, ALL CAPS, creates a question (not the topic restated, not starting with WHY)",
        "shots": [shot, f"... {n_min}-{n_max} shots in order"],
        "lesson": "one-sentence takeaway in plain words",
        "seo_title": "YouTube Shorts title, max 70 characters, keyword-rich, curiosity",
        "seo_description": "50-80 words: what the skit shows + the psychology + a question for the comments + 3-5 keyword phrases",
        "hashtags_instagram": "8-10 hashtags",
        "hashtags_youtube": "#Shorts plus 4-5 hashtags",
        "hashtags_facebook": "5-6 hashtags",
        "hashtags_tiktok": "6-8 hashtags mixing broad and niche",
        "caption_instagram": "hook in the first 125 characters, 150-200 characters with hashtags",
        "caption_facebook": "100-140 characters, conversational",
        "caption_youtube": "100-120 characters, keyword-rich",
        "caption_tiktok": "punchy hook in the first 60 characters, under 150 characters with hashtags",
    }, indent=2, ensure_ascii=False)


def build_prompt(niche: dict, skit: dict, topic: str, keywords: list[str]) -> str:
    w = skit["writer"]
    cast = cast_of(niche)
    hero = niche["skit"]["hero"]
    cast_lines = "\n".join(
        f'- {cid} ("{c["label"]}"){" HERO" if cid == hero else ""}: {c["look"]}. Acting: {c["acting"]}.'
        for cid, c in cast.items() if cid != "narrator"
    )
    loc_lines = "\n".join(f"- {lid}: {desc}" for lid, desc in locations_of(niche).items())
    seo = (f"\n\nFor caption_tiktok/hashtags_tiktok, weave in 2-3 of these search terms where they fit: "
           f"{', '.join(keywords)}. Never use them in the dialogue." if keywords else "")
    structure = STRUCTURE.format(n_min=w["min_shots"], n_max=w["max_shots"], max_line=w["max_line_words"],
                                 max_loc=w["max_locations"], narr_words=28)
    return f"""Channel: {niche["channel_description"]}.
Tone of the narrator: {niche["tone"]}.
Topic of this skit: "{topic}"{seo}

CAST (use the hero plus 1-2 others that fit the topic; ids exactly as written):
{cast_lines}

LOCATIONS (ids exactly as written):
{loc_lines}

{structure}

Total spoken words across all lines: {w["min_words"]}-{w["max_words"]}.

Return ONLY valid JSON shaped like this:
{schema(w["min_shots"], w["max_shots"])}"""


def validate(data: dict, niche: dict, skit: dict) -> list[str]:
    """Human-readable problems; empty list = usable."""
    w = skit["writer"]
    cast, locs = cast_of(niche), locations_of(niche)
    shots = [s for s in data.get("shots", []) if isinstance(s, dict)]
    issues = []
    if not w["min_shots"] <= len(shots) <= w["max_shots"]:
        issues.append(f"write {w['min_shots']}-{w['max_shots']} shots (you wrote {len(shots)})")
    if not shots:
        return issues or ["no shots"]
    words = sum(len(str(s.get("line", "")).split()) for s in shots)
    if not w["min_words"] <= words <= w["max_words"]:
        issues.append(f"total spoken words must be {w['min_words']}-{w['max_words']} (you wrote {words})")
    if shots[-1].get("speaker") != "narrator":
        issues.append('the last shot must be spoken by "narrator"')
    if shots[0].get("speaker") == "narrator":
        issues.append("shot 1 must be a character's line, not the narrator")
    used_locs = {s.get("location") for s in shots}
    bad_locs = used_locs - set(locs)
    if bad_locs:
        issues.append(f"unknown location ids {sorted(map(str, bad_locs))}; use only {list(locs)}")
    if len(used_locs) > w["max_locations"]:
        issues.append(f"use at most {w['max_locations']} locations")
    run, prev = 0, None
    for i, s in enumerate(shots, 1):
        line = str(s.get("line", "")).strip()
        spk = s.get("speaker")
        if spk not in cast:
            issues.append(f"shot {i}: unknown speaker {spk!r}")
        chars = s.get("characters") or []
        if not isinstance(chars, list) or any(c not in cast or c == "narrator" for c in chars):
            issues.append(f"shot {i}: characters must be cast ids (not narrator)")
        elif spk not in ("narrator", None) and spk not in chars:
            issues.append(f"shot {i}: the speaker must be in 'characters'")
        n = len(line.split())
        if not 1 <= n <= w["max_line_words"] + (14 if spk == "narrator" else 0):
            issues.append(f"shot {i}: line has {n} words (max {w['max_line_words']})")
        if re.search(r"\d", line):
            issues.append(f"shot {i}: write numbers as words, no digits")
        low = _norm(line)
        hits = [b for b in BANNED if b in low]
        if hits:
            issues.append(f"shot {i}: banned phrase(s) {hits}")
        run = run + 1 if spk == prev else 1
        prev = spk
        if run >= 3 and spk != "narrator":
            issues.append(f"shot {i}: {spk} speaks three shots in a row")
    title = str(data.get("title", "")).strip()
    if not title or title.upper().startswith("WHY "):
        issues.append('"title" must be a curiosity hook card that does not start with WHY')
    return issues


WRITERS: list[str] = []   # which provider/model wrote each draft (logged into script.json)


def call_openai(client, prompt: str, model: str) -> dict:
    """Free OpenRouter model first when OPENROUTER_API_KEY is set, else/then OpenAI `model`."""
    from llm import chat_json
    data, used = chat_json(SYSTEM_PROMPT, prompt, openai_model=model, max_tokens=4000, task="skit_script")
    WRITERS.append(used)
    return data


def generate(client, niche: dict, skit: dict, topic: str, model: str, keywords: list[str]) -> dict:
    base = build_prompt(niche, skit, topic, keywords)
    feedback, best, best_issues = "", None, None
    for attempt in (1, 2, 3):
        data = call_openai(client, base + feedback, model)
        issues = validate(data, niche, skit)
        print(f"[generate_skit] draft {attempt}: {len(data.get('shots', []))} shots, "
              f"{'clean' if not issues else '; '.join(issues[:6])}")
        if best is None or len(issues) < len(best_issues):
            best, best_issues = data, issues
        if not issues:
            return data
        feedback = ("\n\nYour previous draft had these problems. Write a NEW draft that fixes every one of them "
                    "while keeping the skit structure:\n- " + "\n- ".join(issues))
    fatal = [i for i in best_issues if "unknown" in i or "characters must" in i or "no shots" in i]
    if fatal:
        sys.exit("Skit unusable after 3 drafts: " + "; ".join(fatal))
    print("[generate_skit] using the best draft despite remaining issues")
    return best


def normalise(data: dict, niche: dict, topic: str) -> dict:
    cast, locs = cast_of(niche), locations_of(niche)
    hero = niche["skit"]["hero"]
    first_loc = next(iter(locs))
    shots, last_loc, last_chars = [], first_loc, [hero]
    for s in data.get("shots", []):
        if not isinstance(s, dict) or not str(s.get("line", "")).strip():
            continue
        spk = s.get("speaker") if s.get("speaker") in cast else hero
        loc = s.get("location") if s.get("location") in locs else last_loc
        chars = [c for c in (s.get("characters") or []) if c in cast and c != "narrator"] or last_chars
        if spk != "narrator" and spk not in chars:
            chars = [spk] + chars
        shots.append({
            "speaker": spk,
            "line": str(s["line"]).strip(),
            "emotion": str(s.get("emotion", "") or "natural"),
            "location": loc,
            "characters": chars[:3],
            "framing": str(s.get("framing", "") or "medium shot"),
            "action": str(s.get("action", "") or ""),
            "motion": str(s.get("motion", "") or ""),
            "keyword": str(s.get("keyword", "") or ""),
        })
        last_loc, last_chars = loc, chars
    title = str(data.get("title", "")).strip().rstrip(".!?").upper()
    if not title or title.startswith("WHY "):
        title = shots[0]["line"].rstrip(".!?").upper() if shots else topic.upper()
    narration = " ".join(s["line"] for s in shots)
    out = dict(data)
    out.update({
        "format": "skit", "id": niche["id"], "niche": niche["id"], "topic": topic, "topic_source": "niche",
        "author": "", "field": niche.get("field", ""), "era": "",
        "title": title, "hook": title, "text": data.get("lesson", ""),
        "shots": shots, "narration": narration,
        "footage_query": f"{niche.get('field', '')} cartoon",
        # one segment per shot keeps every older stage (and the doodle fallback) working
        "segments": [{"text": s["line"], "scene": "", "label": s["keyword"].upper()[:28],
                      "footage_query": niche.get("field", "people"), "media_type": "video"} for s in shots],
    })
    out["caption_tiktok"] = out.get("caption_tiktok") or out.get("caption_instagram", "")
    out["hashtags_tiktok"] = out.get("hashtags_tiktok") or out.get("hashtags_instagram", "#fyp")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", required=True, help="config/niches/<niche>.json")
    ap.add_argument("--skit-config", default="config/skit.json")
    ap.add_argument("--model", default=None, help="defaults to config/skit.json writer.model")
    ap.add_argument("--topic-index", type=int, default=None)
    ap.add_argument("--out", default="build")
    args = ap.parse_args()

    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")):
        sys.exit("Set OPENROUTER_API_KEY (free models) and/or OPENAI_API_KEY.")
    from generate_script_ai import write_platform_captions

    niche, skit = load_configs(args.niche, args.skit_config)
    used_path = Path(niche["used_log"])
    topic = pick_topic(niche["topics"], load_used(used_path), args.topic_index)
    kws = niche.get("tiktok_seo_keywords", [])
    keywords = random.sample(kws, min(6, len(kws)))
    model = args.model or skit["writer"]["model"]
    print(f"[generate_skit] {niche['id']} | topic: {topic} | model: {model}")

    data = normalise(generate(None, niche, skit, topic, model, keywords), niche, topic)
    data["writer"] = WRITERS[-1] if WRITERS else ""

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "script.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "script.txt").write_text(data["narration"], encoding="utf-8")
    write_platform_captions(data, out)
    save_used(used_path, load_used(used_path), topic)
    print(f"[generate_skit] {len(data['shots'])} shots, {len(data['narration'].split())} words, "
          f"locations: {sorted({s['location'] for s in data['shots']})}")


if __name__ == "__main__":
    main()
