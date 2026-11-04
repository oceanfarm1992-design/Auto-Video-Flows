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

sys.exit(1 if FAILED else 0)
