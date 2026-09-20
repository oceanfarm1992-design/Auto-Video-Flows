#!/usr/bin/env python3
"""
Regression test for scripts/fetch_footage_multi.py's _clean_query().

No test framework required (this repo has none) -- run directly:
    python tests/test_fetch_footage_multi.py

Context: generate_script_ai.py's prompt used to hand GPT-4o-mini literal
example strings for each segment's footage_query (e.g. "dark gritty
struggle failure person cinematic"). GPT regularly echoed those examples
back verbatim instead of writing something specific to the story, so every
video -- regardless of topic -- searched Pexels/Pixabay for the same four
generic phrases. That's why the footage never matched the narration.

The prompt no longer offers copy-pasteable example text, but _clean_query()
also anchors every query to the story's field: it swaps out an exact match
of one of the old template phrases for the field, and appends the field to
any query that doesn't already mention it, so results stay on-topic even if
GPT's phrasing is vague in the future.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from fetch_footage_multi import _clean_query  # noqa: E402


def check(actual: str, expected: str, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def test_replaces_exact_generic_template_phrase() -> None:
    check(
        _clean_query("dark gritty struggle failure person cinematic", "finance investing"),
        "finance investing",
        "old template phrase 1 swapped for field",
    )
    check(
        _clean_query("abstract nature sky mountains sunrise", "sports"),
        "sports",
        "old template phrase 4 swapped for field",
    )


def test_replacement_is_case_insensitive() -> None:
    check(
        _clean_query("DARK GRITTY STRUGGLE FAILURE PERSON CINEMATIC", "music"),
        "music",
        "generic phrase match ignores case",
    )


def test_appends_field_when_missing() -> None:
    check(
        _clean_query("vintage stock exchange trading floor", "finance investing"),
        "vintage stock exchange trading floor finance investing",
        "specific query gets field appended for topic anchoring",
    )


def test_does_not_duplicate_field_already_present() -> None:
    check(
        _clean_query("finance investing boardroom meeting", "finance investing"),
        "finance investing boardroom meeting",
        "field already in query is not appended again",
    )


def test_still_strips_prompt_instruction_hints() -> None:
    check(
        _clean_query("boxing gym training montage — MUST look different from other segments", "sports"),
        "boxing gym training montage sports",
        "instruction-hint stripping still works alongside field anchoring",
    )


def test_no_field_leaves_specific_query_untouched() -> None:
    check(
        _clean_query("vintage stock exchange trading floor", ""),
        "vintage stock exchange trading floor",
        "empty field does not alter an already-specific query",
    )


def main() -> None:
    tests = [
        test_replaces_exact_generic_template_phrase,
        test_replacement_is_case_insensitive,
        test_appends_field_when_missing,
        test_does_not_duplicate_field_already_present,
        test_still_strips_prompt_instruction_hints,
        test_no_field_leaves_specific_query_untouched,
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
