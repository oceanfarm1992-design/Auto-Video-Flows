#!/usr/bin/env python3
"""
Regression test for scripts/generate_script_ai.py's build_prompt() TikTok
SEO-keyword injection.

No test framework required (this repo has none) -- run directly:
    python tests/test_generate_script_ai.py

Context: TikTok's real "Creator Search Insights" (trending/underserved search
terms for a niche) lives inside TikTok Studio behind login, with no public
API -- not something this CI pipeline can pull automatically. Instead,
config/topics.json's tiktok_seo_keywords.keywords holds a curated, manually
refreshable list (update it whenever you check TikTok Studio yourself), and
generate_script_ai.py samples a few of them into the prompt each run so GPT
can weave relevant ones into caption_tiktok/hashtags_tiktok. This also gave
TikTok its own caption/hashtag schema fields instead of reusing Instagram's.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from generate_script_ai import build_prompt  # noqa: E402

TOPIC = {"name": "Ada Lovelace", "field": "computing", "theme": "vision ahead of her time", "era": "19th century"}


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def test_schema_always_includes_tiktok_fields() -> None:
    prompt = build_prompt(TOPIC, "failure_to_success")
    check('"caption_tiktok"' in prompt, "schema must always request caption_tiktok")
    check('"hashtags_tiktok"' in prompt, "schema must always request hashtags_tiktok")


def test_no_keywords_omits_seo_note() -> None:
    prompt = build_prompt(TOPIC, "failure_to_success", tiktok_keywords=None)
    check("high-search-volume" not in prompt, "no keyword list should mean no SEO note")

    prompt_empty = build_prompt(TOPIC, "failure_to_success", tiktok_keywords=[])
    check("high-search-volume" not in prompt_empty, "empty keyword list should mean no SEO note")


def test_keywords_are_included_when_provided() -> None:
    keywords = ["self improvement", "never give up", "growth mindset"]
    prompt = build_prompt(TOPIC, "failure_to_success", tiktok_keywords=keywords)
    check("high-search-volume" in prompt, "SEO note should appear when keywords are given")
    for kw in keywords:
        check(kw in prompt, f"keyword {kw!r} should be passed through into the prompt")


def test_trend_note_and_tiktok_note_coexist() -> None:
    topic = dict(TOPIC, trend_reason="Ada Lovelace Day is trending", hook_line="SHE PREDICTED AI 200 YEARS EARLY")
    prompt = build_prompt(topic, "failure_to_success", tiktok_keywords=["daily motivation"])
    check("TRENDING right now" in prompt, "trend note should still render alongside the TikTok SEO note")
    check("daily motivation" in prompt, "TikTok SEO note should still render alongside the trend note")


def main() -> None:
    tests = [
        test_schema_always_includes_tiktok_fields,
        test_no_keywords_omits_seo_note,
        test_keywords_are_included_when_provided,
        test_trend_note_and_tiktok_note_coexist,
    ]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {test.__name__}: {exc}")

    if failures:
        sys.exit(f"{failures}/{len(tests)} test(s) failed")
    print(f"All {len(tests)} test(s) passed.")


if __name__ == "__main__":
    main()
