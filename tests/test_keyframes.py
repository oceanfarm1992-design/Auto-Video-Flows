"""Offline tests for the GPT Image model fallback in generate_keyframes.py (fake OpenAI client).

Run from the repo root:  python tests/test_keyframes.py
"""
import base64
import io
import sys
import types
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import generate_keyframes as gk  # noqa: E402

FAILED = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILED.append(name)


buf = io.BytesIO()
Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, "PNG")
PNG = base64.b64encode(buf.getvalue()).decode()


class FakeImages:
    def __init__(self, available):
        self.available, self.calls = set(available), []

    def generate(self, model, prompt, size, quality, n):
        self.calls.append((model, size))
        if model not in self.available:
            raise RuntimeError(f"Error code: 400 - The model '{model}' does not exist.")
        return types.SimpleNamespace(data=[types.SimpleNamespace(b64_json=PNG)])


def provider(available, listed, cfg):
    p = gk.OpenAIImages.__new__(gk.OpenAIImages)
    p.cfg, p.quality, p.model = cfg, "medium", None
    p.candidates = list(cfg["openai_models"])
    imgs = FakeImages(available)
    p.client = types.SimpleNamespace(images=imgs, models=types.SimpleNamespace(
        list=lambda: [types.SimpleNamespace(id=i) for i in listed]))
    return p, imgs


cfg = {"openai_models": ["gpt-image-2", "gpt-image-1"], "openai_size_v2": "864x1536", "openai_size": "1024x1536"}
p, imgs = provider({"gpt-image-2"}, [], cfg)
p.generate("x")
check("first configured model is used, with its exact 9:16 size", imgs.calls == [("gpt-image-2", "864x1536")])
p.generate("y")
check("the working model is remembered", imgs.calls[-1][0] == "gpt-image-2" and len(imgs.calls) == 2)

p, imgs = provider({"gpt-image-1"}, [], cfg)
p.generate("x")
check("falls through a missing model to the next one",
      [c[0] for c in imgs.calls] == ["gpt-image-2", "gpt-image-1"] and imgs.calls[-1][1] == "1024x1536")

p, imgs = provider({"gpt-image-3"}, ["gpt-4o", "gpt-image-3", "gpt-image-2-mini"], cfg)
p.generate("x")
check("discovers the newest gpt-image model the account lists", p.model == "gpt-image-3")

p, imgs = provider(set(), ["gpt-4o"], cfg)
try:
    p.generate("x")
    check("no model at all is a fatal (non-retried) error", False)
except gk.FatalImageError:
    check("no model at all is a fatal (non-retried) error", True)

n = {"k": 0}


def missing():
    n["k"] += 1
    raise RuntimeError("Error code: 400 - The model 'x' does not exist.")


try:
    gk.with_retry(missing)
except RuntimeError:
    pass
check("a missing model is not retried", n["k"] == 1)

# ── keyframe budget planner ──────────────────────────────────────────────────
import skit_common as sc  # noqa: E402

niche, _ = sc.load_configs("config/niches/men_psychology.json")


def sh(spk, loc, chars, framing="medium two-shot"):
    return {"speaker": spk, "location": loc, "characters": chars, "framing": framing, "line": "x"}


shots = ([sh("the_wife", "kitchen", ["the_wife", "the_provider"])]
         + [sh(c, "kitchen", ["the_wife", "the_provider"]) for c in ("the_provider", "the_wife") * 3]
         + [sh("the_provider", "kitchen", ["the_provider"], "close-up on The Provider")]
         + [sh("the_kid", "living_room", ["the_kid", "the_provider"]), sh("the_provider", "living_room", ["the_provider", "the_kid"])]
         + [sh("narrator", "living_room", ["the_provider", "the_kid"])])
jobs, views = sc.plan_keyframes(niche, shots, budget=4)
check("budget respected: 4 unique pictures for 11 shots", len(jobs) == 4 and len(views) == len(shots))
check("one master per location", sum(j["kind"] == "master" for j in jobs) == 2)
check("hook shot gets its own picture", views[0]["key"] == "shot_00")
check("hero stands on the left of every master",
      all(j["characters"][0] == "the_provider" for j in jobs if j["kind"] == "master"))
kitchen = [v for v, s in zip(views, shots) if v["key"] == "master_kitchen"]
check("reused master shots frame the speaker (left vs right)",
      any(v["focus"] < 0.5 for v in kitchen) and any(v["focus"] > 0.5 for v in kitchen))
check("narrator shot shows the whole master", views[-1]["focus"] == 0.5 and views[-1]["zoom"] < 1.1)
j2, v2 = sc.plan_keyframes(niche, shots, budget=1)
check("budget below the number of locations still gives every location a master",
      {j["kind"] for j in j2} == {"master"} and len(j2) == 2)
check("prompt pins left-to-right positions", "left to right: The Provider, The Wife"
      in sc.master_prompt(niche, {"style": "", "cap_rule": "", "frame_rule": ""}, "kitchen",
                          ["the_provider", "the_wife"], ["a", "b", "c"]))

sys.exit(1 if FAILED else 0)
