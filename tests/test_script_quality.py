"""Tests for scripts/script_quality.py (lint + segment repair). Run from the repo root."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import script_quality as q

FAILED = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILED.append(name)


GOOD = ("Daniel hasn't bought himself a coat in nine years. Picture him. Forty-one, two kids, a job he "
        "never mentions. Every payday, the money leaves in the same order. Rent. School shoes. Her birthday. "
        "His name is always last. Not because he doesn't want things. Because somewhere along the way, "
        "spending on himself started to feel like stealing from them. That's the quiet trap. So he waits. "
        "Send this to the Daniel in your life.")
check("a good script is clean", q.lint_narration(GOOD, 60, 125) == [])
check("filler phrase is flagged", any("filler" in i for i in q.lint_narration(GOOD + " Remember, a little care goes a long way.", 60, 140)))
check("curly apostrophe filler is flagged", any("filler" in i for i in q.lint_narration("It’s important to rest. Stop. " + GOOD, 60, 140)))
check("digits are flagged", any("number" in i for i in q.lint_narration(GOOD.replace("Forty-one", "41"), 60, 125)))
check("too short is flagged", any("LENGTH" in i for i in q.lint_narration("Short one. Yes.", 60, 125)))
check("ellipsis spam is flagged", any("ellips" in i for i in q.lint_narration(GOOD + " Wait... what... why...", 60, 140)))
lecture = ("When men are under significant emotional pressure they frequently withdraw from social contact "
           "because they believe that expressing vulnerability will damage their standing among peers. " * 3)
check("lecture rhythm is flagged", any("sentences are too long" in i or "short punchy" in i for i in q.lint_narration(lecture, 10, 500)))

seg = q.rebuild_segments(GOOD, 10)
check("rebuild gives exactly n segments", len(seg) == 10)
check("rebuild keeps every word", " ".join(seg).split() == GOOD.split())
check("segments are balanced", max(len(s.split()) for s in seg) <= 16)
check("segments_match accepts a faithful split", q.segments_match([{"text": s} for s in seg], GOOD, 10))
check("segments_match rejects wrong count", not q.segments_match([{"text": s} for s in seg[:8]], GOOD, 10))
check("segments_match rejects dropped text", not q.segments_match([{"text": s} for s in seg[:9]] + [{"text": "x"}], GOOD, 10))

sys.exit(1 if FAILED else 0)
