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

A related follow-up bug found from a live production run (Kelsey Mitchell,
a WNBA scoring-record story): GPT started writing genuinely specific
queries once the copy-paste bug was fixed, but included the real person's
name (e.g. "Kelsey Mitchell celebrating after scoring"). Pexels/Pixabay are
royalty-free stock libraries with no footage of named individuals, so the
name can never match anything -- it just wastes query budget. The same run
also showed a query truncated mid-word ("...Professional Baske") because
the 60-char cutoff didn't respect word boundaries. _clean_query() now strips
the person's name (full name and first name alone) before field-anchoring,
and truncates on a word boundary.
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


def test_strips_real_persons_full_name() -> None:
    check(
        _clean_query("Kelsey Mitchell celebrating after scoring", "Professional Basketball",
                     "Kelsey Mitchell"),
        "celebrating after scoring Professional Basketball",
        "full name stripped before field is appended (unmatchable on stock sites)",
    )


def test_strips_real_persons_first_name_alone() -> None:
    check(
        _clean_query("Kelsey training hard at the gym", "sports", "Kelsey Mitchell"),
        "training hard at the gym sports",
        "first name alone is also stripped, not just the full name",
    )


def test_query_that_is_only_the_name_falls_back_to_field() -> None:
    check(
        _clean_query("Kelsey Mitchell", "sports", "Kelsey Mitchell"),
        "sports",
        "stripping the name to nothing falls back to the field, not an empty query",
    )


def test_truncation_respects_word_boundaries() -> None:
    # Matches the actual field/query scale seen in the production run that
    # surfaced this bug (Kelsey Mitchell / "Professional Basketball League").
    result = _clean_query(
        "Kelsey Mitchell celebrating after scoring", "Professional Basketball League",
        "Kelsey Mitchell",
    )
    assert len(result) <= 60, f"expected <=60 chars, got {len(result)}: {result!r}"
    check(result, "celebrating after scoring Professional Basketball League",
          "name stripped, full field preserved, no mid-word cut")


def test_truncation_preserves_field_over_dropping_it() -> None:
    # Regression guard: an earlier version of the fix truncated the combined
    # string from the end, which could delete the appended field entirely
    # (the exact anchor the safety net exists to preserve) rather than just
    # trimming a few characters off it.
    result = _clean_query(
        "Kelsey Mitchell celebrating after scoring impressively hard tonight",
        "Professional Womens Basketball League Championship",
        "Kelsey Mitchell",
    )
    assert len(result) <= 60, f"expected <=60 chars, got {len(result)}: {result!r}"
    assert "Professional" in result and "Championship" in result, (
        f"field must never be dropped entirely: {result!r}"
    )


def main() -> None:
    tests = [
        test_replaces_exact_generic_template_phrase,
        test_replacement_is_case_insensitive,
        test_appends_field_when_missing,
        test_does_not_duplicate_field_already_present,
        test_still_strips_prompt_instruction_hints,
        test_no_field_leaves_specific_query_untouched,
        test_strips_real_persons_full_name,
        test_strips_real_persons_first_name_alone,
        test_query_that_is_only_the_name_falls_back_to_field,
        test_truncation_respects_word_boundaries,
        test_truncation_preserves_field_over_dropping_it,
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
