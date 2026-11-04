"""Offline tests for the OpenRouter-free-first JSON helper (scripts/llm.py). No network.

Run from the repo root:  python tests/test_llm.py
"""
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import llm  # noqa: E402

FAILED = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILED.append(name)


check("plain JSON", llm.parse_json('{"a": 1}') == {"a": 1})
check("JSON in a ``` fence", llm.parse_json('Sure!\n```json\n{"a": 2}\n```') == {"a": 2})
check("JSON inside prose", llm.parse_json('Here you go: {"a": 3} hope it helps') == {"a": 3})

models = [
    {"id": "meta-llama/llama-3.1-8b:free", "context_length": 8000, "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "deepseek/deepseek-v4:free", "context_length": 128000, "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "qwen/qwen3-235b:free", "context_length": 256000, "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "openai/gpt-5", "context_length": 400000, "pricing": {"prompt": "0.00001", "completion": "0.00004"}},
    {"id": "google/gemini-image:free", "context_length": 64000, "pricing": {"prompt": "0", "completion": "0"},
     "architecture": {"output_modalities": ["image", "text"]}},
]
ranked = llm.rank_free_models(models)
check("only free text models with enough context, best family first",
      ranked == ["deepseek/deepseek-v4:free", "qwen/qwen3-235b:free"])


def run(or_behaviour, have_openai=True):
    """or_behaviour: list of results/exceptions for successive OpenRouter calls."""
    llm._OR_STATE.clear()
    calls = []
    seq = list(or_behaviour)

    def fake_or(key, model, system, prompt, max_tokens):
        calls.append(model)
        r = seq.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    llm._openrouter_call = fake_or
    llm.openrouter_candidates = lambda cfg, key: ["m1:free", "m2:free", "openrouter/free"]
    llm.load_cfg = lambda: {"use_openrouter": True, "openrouter_tasks": ["skit_script"], "max_attempts": 4}
    os.environ["OPENROUTER_API_KEY"] = "test"
    if have_openai:
        os.environ["OPENAI_API_KEY"] = "test"
    else:
        os.environ.pop("OPENAI_API_KEY", None)
    fake_resp = types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='{"src": "openai"}'))])
    sys.modules["openai"] = types.SimpleNamespace(OpenAI=lambda: types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=lambda **k: fake_resp))))
    out, used = llm.chat_json("s", "p", task="skit_script")
    return out, used, calls


out, used, calls = run([{"src": "m1"}])
check("first free model answers", used == "openrouter:m1:free" and out == {"src": "m1"})
out, used, calls = run([RuntimeError("bad json"), {"src": "m2"}])
check("a failing free model falls through to the next", used == "openrouter:m2:free" and calls == ["m1:free", "m2:free"])
llm.time.sleep = lambda s: None
busy = llm.RateLimited('{"error":{"message":"google/gemma is temporarily rate-limited upstream","code":429}}')
out, used, calls = run([busy, {"src": "m2"}])
check("one busy free model (429 upstream) -> next free model", used == "openrouter:m2:free" and calls == ["m1:free", "m2:free"])
cap = llm.RateLimited('{"error":{"message":"Rate limit exceeded: free-models-per-day","code":429}}')
out, used, calls = run([cap])
check("account daily free quota -> straight to OpenAI", used == "openai:gpt-4.1" and calls == ["m1:free"])
out, used, calls = run([RuntimeError("x")] * 3)
check("all free models down -> OpenAI", used == "openai:gpt-4.1" and len(calls) == 3)
llm._OR_STATE.clear()
llm.load_cfg = lambda: {"use_openrouter": True, "openrouter_tasks": ["skit_script"]}
llm._openrouter_call = lambda *a: (_ for _ in ()).throw(AssertionError("must not be called"))
_, used = llm.chat_json("s", "p", task="narrated_script")
check("tasks not opted in never use OpenRouter (live narrated series stays on gpt-4.1)", used == "openai:gpt-4.1")

check("free-only guard", llm.is_free_id("x/y:free") and llm.is_free_id("openrouter/free")
      and not llm.is_free_id("openai/gpt-5"))

# a paid id in the candidates is never called
out, used, calls = None, None, None
llm._OR_STATE.clear()
seen = []
llm._openrouter_call = lambda key, model, *a: (seen.append(model), {"ok": 1})[1]
llm.openrouter_candidates = lambda cfg, key: ["openai/gpt-5", "m1:free"]
llm.load_cfg = lambda: {"use_openrouter": True, "openrouter_tasks": ["skit_script"]}
_, used = llm.chat_json("s", "p", task="skit_script")
check("paid model ids are never called", seen == ["m1:free"] and used == "openrouter:m1:free")

# per-run request cap
llm._OR_STATE.clear()
n = {"k": 0}


def always_fail(*a):
    n["k"] += 1
    raise RuntimeError("bad json")


llm._openrouter_call = always_fail
llm.openrouter_candidates = lambda cfg, key: [f"m{i}:free" for i in range(10)]
llm.load_cfg = lambda: {"use_openrouter": True, "openrouter_tasks": ["skit_script"], "max_attempts": 10,
                        "max_requests_per_run": 3}
_, used = llm.chat_json("s", "p", task="skit_script")
check("per-run free request cap is enforced", n["k"] == 3 and used == "openai:gpt-4.1")

sys.exit(1 if FAILED else 0)
