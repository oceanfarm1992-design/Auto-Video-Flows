"""Tests for the niche series generator and Buffer YouTube support."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import generate_script_niche as gn
import post_buffer as pb

FAILED = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILED.append(name)


for niche in ("men_psychology", "power_psychology"):
    cfg = json.loads(Path(f"config/niches/{niche}.json").read_text(encoding="utf-8"))
    check(f"{niche} has 30 topics", len(cfg["topics"]) == 30)
    prompt = gn.build_prompt(cfg, cfg["topics"][0], ["a b"])
    check(f"{niche} prompt has topic", cfg["topics"][0] in prompt)
    d = gn.normalise({"title": "T", "narration": "n", "lesson": "l"}, cfg, cfg["topics"][0])
    check(f"{niche} normalise sets niche", d["niche"] == niche and d["author"] == "")

topics = ["a", "b", "c"]
check("pick_topic avoids recent", gn.pick_topic(topics, ["a", "b"], None) == "c")
check("pick_topic forced index", gn.pick_topic(topics, [], 4) == "b")
check("pick_topic all used falls back", gn.pick_topic(topics, ["a"] * 1, None) in topics)

meta = pb.build_metadata("youtube", "cap", "x" * 150)
check("youtube title capped", len(meta["youtube"]["title"]) == 100)
check("youtube category", meta["youtube"]["categoryId"] == "27")
check("tiktok meta unchanged", "tiktok" in pb.build_metadata("tiktok", "hi"))
check("youtube caption adds #Shorts", "#Shorts" in pb.pick_caption("youtube", "d", None, "desc"))
check("youtube keeps existing #shorts", pb.pick_caption("youtube", "d", None, "x #shorts") == "x #shorts")
check("tiktok caption used", pb.pick_caption("tiktok", "d", "tt", None) == "tt")
check("fallback caption", pb.pick_caption("facebook", "d", "tt", "yt") == "d")

sys.exit(1 if FAILED else 0)
