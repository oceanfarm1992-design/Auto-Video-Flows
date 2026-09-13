#!/usr/bin/env python3
"""
Regression test for scripts/generate_tts.py's soften_caps_for_speech().

No test framework required (this repo has none) -- run directly:
    python tests/test_generate_tts.py

Context: generate_script_ai.py deliberately writes ALL-CAPS emphasis words
into narration (e.g. "He was BROKE and alone."). gruut (StyleTTS2's
phonemizer) treats a run of capital letters as an initialism and spells it
out ("B R O K E") instead of speaking the word -- this produced the reported
"spelling the word instead of saying it" bug in real generated videos.
soften_caps_for_speech() title-cases such runs before TTS synthesis so they
are pronounced normally.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from generate_tts import soften_caps_for_speech  # noqa: E402


def check(actual: str, expected: str, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def test_softens_emphasis_word() -> None:
    check(
        soften_caps_for_speech("You must STOP now."),
        "You must Stop now.",
        "single emphasis word",
    )


def test_softens_multiple_words_in_one_sentence() -> None:
    check(
        soften_caps_for_speech("He was BROKE and alone... then he became a LEGEND."),
        "He was Broke and alone... then he became a Legend.",
        "multiple emphasis words",
    )


def test_leaves_single_letter_words_alone() -> None:
    # "I" and "A" are real single-letter words, not initialisms to soften.
    check(
        soften_caps_for_speech("I am here. A new day."),
        "I am here. A new day.",
        "single-letter words",
    )


def test_handles_apostrophe_inside_caps_run() -> None:
    check(
        soften_caps_for_speech("DON'T give up."),
        "Don't give up.",
        "apostrophe inside a caps run",
    )


def test_leaves_lowercase_and_normal_titlecase_untouched() -> None:
    text = "This is a normal sentence about Napoleon and stop signs."
    check(soften_caps_for_speech(text), text, "already-normal text")


def main() -> None:
    tests = [
        test_softens_emphasis_word,
        test_softens_multiple_words_in_one_sentence,
        test_leaves_single_letter_words_alone,
        test_handles_apostrophe_inside_caps_run,
        test_leaves_lowercase_and_normal_titlecase_untouched,
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
