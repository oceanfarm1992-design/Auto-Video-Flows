#!/usr/bin/env python3
"""
JSON chat completions with the cheapest working provider first.

  1. OpenRouter FREE models (env OPENROUTER_API_KEY): the models in config/llm.json
     "openrouter_free_models" that are still listed as free, then the best other free model
     OpenRouter lists right now (its catalogue changes often), then the openrouter/free router.
     Free tier limits: 20 requests/min and 50/day (1,000/day after a one-time $10 credit top-up)
     - a video needs 2-4 calls.
  2. OpenAI (env OPENAI_API_KEY) with the caller's model (gpt-4.1 for scripts): the fallback
     when OpenRouter is not configured, rate-limited, down, or returns unusable JSON.

Note: some free OpenRouter models are hosted by providers that may train on prompts. Only the
video topic and the channel's style instructions are sent.

    data, used = chat_json(system, prompt, openai_model="gpt-4.1")
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import requests

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
CONFIG = Path("config/llm.json")
# Families that write good structured English, best first (used to rank free models we discover).
PREFERRED_FAMILIES = ["deepseek", "qwen3", "kimi", "glm", "gpt-oss", "llama-4", "gemini", "gemma", "mistral",
                      "nemotron", "llama"]


def load_cfg() -> dict:
    if CONFIG.exists():
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    return {}


def parse_json(text: str) -> dict:
    """JSON object from a model reply that may wrap it in prose or ``` fences."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        return json.loads(fence.group(1))
    a, b = text.find("{"), text.rfind("}")
    if a != -1 and b > a:
        return json.loads(text[a:b + 1])
    raise ValueError("no JSON object in the reply")


def _is_free(m: dict) -> bool:
    p = m.get("pricing") or {}
    try:
        return m.get("id", "").endswith(":free") or (float(p.get("prompt", 1)) == 0 and float(p.get("completion", 1)) == 0)
    except (TypeError, ValueError):
        return False


def rank_free_models(models: list[dict], min_context: int = 32000) -> list[str]:
    """Free text models with enough context, best family first, then by context length."""
    def family(mid: str) -> int:
        low = mid.lower()
        return next((i for i, f in enumerate(PREFERRED_FAMILIES) if f in low), len(PREFERRED_FAMILIES))

    usable = []
    for m in models:
        arch = m.get("architecture") or {}
        outs = arch.get("output_modalities") or ["text"]
        if _is_free(m) and "text" in outs and "image" not in outs and (m.get("context_length") or 0) >= min_context \
                and m.get("id") != "openrouter/free":
            usable.append(m)
    usable.sort(key=lambda m: (family(m["id"]), -(m.get("context_length") or 0)))
    return [m["id"] for m in usable]


def openrouter_candidates(cfg: dict, key: str) -> list[str]:
    preferred = list(cfg.get("openrouter_free_models", []))
    listed: list[dict] = []
    try:
        r = requests.get(f"{OPENROUTER_BASE}/models", timeout=30, headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        listed = r.json().get("data", [])
    except Exception as exc:  # noqa: BLE001 - the preferred list still works without the catalogue
        print(f"[llm] could not list OpenRouter models: {exc}", file=sys.stderr)
    if listed:
        free_now = {m["id"] for m in listed if _is_free(m)}
        preferred = [m for m in preferred if m in free_now]
    ranked = [m for m in rank_free_models(listed) if m not in preferred]
    out = preferred + ranked[: int(cfg.get("max_discovered", 3))]
    if cfg.get("use_free_router", True):
        out.append("openrouter/free")
    return out


def _openrouter_call(key: str, model: str, system: str, prompt: str, max_tokens: int) -> dict:
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0.8,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    headers = {"Authorization": f"Bearer {key}", "X-Title": "auto-video-flows"}
    r = requests.post(f"{OPENROUTER_BASE}/chat/completions", json=body, headers=headers, timeout=240)
    if r.status_code == 400 and "response_format" in r.text:
        body.pop("response_format")                       # some free models don't support JSON mode
        r = requests.post(f"{OPENROUTER_BASE}/chat/completions", json=body, headers=headers, timeout=240)
    if r.status_code == 429:
        raise RuntimeError("rate-limited (free tier: 20/min, 50/day)")
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(str(data["error"])[:200])
    return parse_json(data["choices"][0]["message"]["content"])


_OR_STATE: dict = {}   # remembered across calls in one run: candidates, the model that worked, dead flag


def chat_json(system: str, prompt: str, openai_model: str = "gpt-4.1", max_tokens: int = 4000,
              task: str = "script") -> tuple[dict, str]:
    """Returns (parsed JSON, "provider:model" that produced it)."""
    cfg = load_cfg()
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key and cfg.get("use_openrouter", True) and task in cfg.get("openrouter_tasks", ["script"]) \
            and not _OR_STATE.get("dead"):
        if "candidates" not in _OR_STATE:
            _OR_STATE["candidates"] = openrouter_candidates(cfg, key)
        order = ([_OR_STATE["ok"]] if _OR_STATE.get("ok") else []) + \
            [m for m in _OR_STATE["candidates"] if m != _OR_STATE.get("ok")]
        for model in order[: int(cfg.get("max_attempts", 4))]:
            try:
                out = _openrouter_call(key, model, system, prompt, max_tokens)
                _OR_STATE["ok"] = model
                print(f"[llm] {task}: OpenRouter {model} (free)")
                return out, f"openrouter:{model}"
            except Exception as exc:  # noqa: BLE001
                print(f"[llm] OpenRouter {model} failed: {str(exc)[:160]}", file=sys.stderr)
                if "rate-limited" in str(exc):
                    _OR_STATE["dead"] = True      # daily/minute cap hit: don't burn time on others
                    break
        else:
            _OR_STATE["dead"] = True
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("No working OpenRouter model and OPENAI_API_KEY is not set.")
    from openai import OpenAI
    kwargs = dict(model=openai_model, response_format={"type": "json_object"},
                  messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}])
    if openai_model.startswith("gpt-5") and "chat" not in openai_model:
        kwargs["max_completion_tokens"] = max(max_tokens, 8000)
        kwargs["reasoning_effort"] = os.environ.get("SCRIPT_REASONING", "low")
    else:
        kwargs["max_tokens"] = max_tokens
        kwargs["temperature"] = 0.9
    resp = OpenAI().chat.completions.create(**kwargs)
    print(f"[llm] {task}: OpenAI {openai_model}")
    return json.loads(resp.choices[0].message.content), f"openai:{openai_model}"
