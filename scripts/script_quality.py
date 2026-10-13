#!/usr/bin/env python3
"""
Quality gate and segment repair for the niche series scripts (no network, no OpenAI).

lint_narration()   flags the habits that make AI scripts boring: filler phrases,
                   lecture-length sentences, no short punchlines, ellipsis spam,
                   digits (TTS reads words better and digits trigger a needless
                   fact-check), a generic hook.
rebuild_segments() splits a narration into n balanced chunks at sentence/phrase
                   breaks, used whenever the model's own segments don't add up to
                   the narration (so the picture can always change every few seconds).
"""
from __future__ import annotations

import re

BANNED = [
    "remember,", "remember:", "it's important to", "it is important to", "it's crucial", "it is crucial",
    "in today's", "fast-paced", "societal", "society", "navigat", "journey", "at the end of the day",
    "deeper connection", "vulnerability is a strength", "go a long way", "goes a long way",
    "understanding this", "in conclusion", "ultimately,", "let's dive", "delve", "tapestry",
    "it's a common tendency", "many men often", "foster", "embrace", "empower", "resonate",
    "deeper reason", "unspoken rule", "cautious hope", "badge of honor", "speak louder",
    "take a man like", "take a woman like", "life lesson",
]
SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def _norm(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'").lower()


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def lint_narration(text: str, min_words: int, max_words: int) -> list[str]:
    """Human-readable problems with a draft; empty list = good to go."""
    issues = []
    words = len(text.split())
    if words < min_words:
        issues.append(f"LENGTH: only {words} words; write {min_words}-{max_words}")
    elif words > max_words:
        issues.append(f"LENGTH: {words} words; trim to {min_words}-{max_words}")

    low = _norm(text)
    found = [b for b in BANNED if b in low]
    if found:
        issues.append("filler phrases to remove: " + ", ".join(f"'{b}'" for b in found))
    if text.count("...") + text.count("…") > 2:
        issues.append("too many ellipses; use at most two")
    if "#" in text or "@" in text:
        issues.append("no hashtags or @-handles in the spoken script")
    if re.search(r"\d", text):
        issues.append("write every number as words (forty-one, nine years), no digits")

    sents = sentences(text)
    if sents:
        if re.match(r"(picture|imagine|have you ever|did you know|ever wonder|let's talk)\b", _norm(sents[0])):
            issues.append("the hook must not open with Picture/Imagine/Have you ever; start inside a moment or with a bold claim")
        if len(sents[0].split()) > 16:
            issues.append("the first sentence (hook) is too long; make it 12 words or fewer")
        lengths = [len(s.split()) for s in sents]
        if sum(lengths) / len(lengths) > 14:
            issues.append("sentences are too long on average; mix in short punches of 2-5 words")
        if not any(n <= 5 for n in lengths):
            issues.append("no short punchy sentence (2-5 words); add some")
    return issues


# ----------------------------------------------------------------------- segments
def _units(text: str, target: float) -> list[str]:
    """Sentences, with any sentence much longer than a segment split at commas/dashes."""
    out = []
    for sent in sentences(text):
        if len(sent.split()) <= target * 1.6:
            out.append(sent)
            continue
        parts = re.split(r"(?<=[,;:—–])\s+", sent)
        out.extend(p.strip() for p in parts if p.strip())
    return out


def rebuild_segments(narration: str, n: int) -> list[str]:
    """Exactly n consecutive chunks of the narration, balanced by word count."""
    words = narration.split()
    if n <= 1 or len(words) <= n:
        return [narration.strip()]
    target = len(words) / n
    units = _units(narration, target)
    chunks: list[list[str]] = []
    cur: list[str] = []
    cur_words = 0
    for unit in units:
        uw = len(unit.split())
        if cur and cur_words >= target * 0.75 or (cur and cur_words + uw > target * 1.4):
            chunks.append(cur)
            cur, cur_words = [], 0
        cur.append(unit)
        cur_words += uw
    if cur:
        chunks.append(cur)
    # force exactly n: split the heaviest or merge the lightest neighbours
    weight = lambda ch: sum(len(u.split()) for u in ch)  # noqa: E731
    while len(chunks) > n:
        i = min(range(len(chunks) - 1), key=lambda k: weight(chunks[k]) + weight(chunks[k + 1]))
        chunks[i:i + 2] = [chunks[i] + chunks[i + 1]]
    while len(chunks) < n:
        i = max(range(len(chunks)), key=lambda k: weight(chunks[k]))
        flat = " ".join(chunks[i]).split()
        if len(flat) < 2:
            break
        half = len(flat) // 2
        chunks[i:i + 1] = [[" ".join(flat[:half])], [" ".join(flat[half:])]]
    return [" ".join(ch).strip() for ch in chunks]


def segments_match(segments: list[dict], narration: str, n: int, tolerance: float = 0.06) -> bool:
    """True when the model's segments are n pieces that together are the narration."""
    if len(segments) != n:
        return False
    joined = " ".join(str(s.get("text", "")) for s in segments).split()
    ref = narration.split()
    if not ref:
        return False
    return abs(len(joined) - len(ref)) <= max(3, tolerance * len(ref))
