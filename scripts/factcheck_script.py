#!/usr/bin/env python3
"""
Stage 1.5: fact-check the AI-generated narration against real sources before
any TTS/footage/posting work happens.

generate_script_ai.py's system prompt tells GPT to "be specific with real
details (years, numbers, names)" about a real historical figure — exactly the
kind of claim GPT-4o-mini hallucinates. This stage re-checks those claims with
a model that has real web search, rather than asking the same kind of model
to grade its own homework from memory.

Behavior:
  - Verdict "pass": no changes.
  - Verdict "minor_issues" (small correctable errors, e.g. wrong year/number):
    the corrected narration is written back to build/script.json / script.txt
    and the run continues.
  - Verdict "major_issues" (the core story is fabricated/unverifiable): the
    run aborts (non-zero exit) rather than build and post false information.
  - If the fact-check call itself errors (network/API problem, not a content
    verdict), the run continues with a warning — a broken checker should not
    take down the otherwise-autonomous daily pipeline.

Output: overwrites build/script.json ("narration") and build/script.txt when
corrected; always writes build/factcheck.json with the full verdict for the
run log / manual review.

Usage:
    python scripts/factcheck_script.py
    python scripts/factcheck_script.py --model gpt-4o
"""
import argparse
import json
import os
import sys
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    sys.exit("openai package not installed. Run: pip install openai")

BUILD_DIR = Path("build")

SYSTEM_PROMPT = (
    "You are a rigorous fact-checker for a motivational history video. "
    "You are given a short narration script about a real historical figure. "
    "Use web search to verify every concrete factual claim in it: dates, "
    "numbers, named events, quotes, and attributed achievements. Ignore "
    "subjective framing, motivational language, and the closing lesson/moral "
    "line — those are not factual claims.\n\n"
    "Respond with ONLY valid JSON (no markdown fences), exactly these keys:\n"
    '{"verdict": "pass" | "minor_issues" | "major_issues", '
    '"issues": [{"claim": "...", "problem": "...", "correction": "..."}], '
    '"corrected_narration": "..."}\n\n'
    '"corrected_narration" must be the FULL narration text (same style, '
    'tone, and approximate length as the original — only the specific wrong '
    'details fixed) whenever verdict is "minor_issues"; leave it as an empty '
    'string otherwise. Reserve "major_issues" for when the central story '
    'itself is fabricated or cannot be verified at all, not for a single '
    "wrong date or number."
)


def build_prompt(data: dict) -> str:
    return (
        f"Historical figure: {data.get('author', data.get('id', 'unknown'))}\n"
        f"Era: {data.get('era', 'unknown')}\n"
        f"Field: {data.get('field', 'unknown')}\n\n"
        f"Narration to fact-check:\n{data.get('narration', '')}"
    )


def parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def run_factcheck(client: OpenAI, data: dict, model: str) -> dict:
    response = client.responses.create(
        model=model,
        tools=[{"type": "web_search"}],
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(data)},
        ],
    )
    return parse_json_response(response.output_text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--model", default="gpt-4o",
                     help="Should be a stronger/independent model than the one that wrote "
                          "the narration, with web search available.")
    args = ap.parse_args()

    script_path = Path(args.script)
    data = json.loads(script_path.read_text(encoding="utf-8"))

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY environment variable not set.")
    client = OpenAI(api_key=api_key)

    BUILD_DIR.mkdir(exist_ok=True)
    who = data.get("author", data.get("id", "unknown"))
    print(f"[factcheck_script] Checking narration about '{who}' with {args.model} (web search) ...")

    try:
        result = run_factcheck(client, data, args.model)
    except Exception as exc:  # noqa: BLE001 — a broken checker shouldn't kill the daily run
        print(f"[factcheck_script] fact-check call failed ({type(exc).__name__}: {exc}); "
              f"continuing without verification.", file=sys.stderr)
        (BUILD_DIR / "factcheck.json").write_text(
            json.dumps({"verdict": "error", "error": str(exc)}, indent=2), encoding="utf-8"
        )
        return

    verdict = result.get("verdict", "pass")
    issues = result.get("issues", [])
    (BUILD_DIR / "factcheck.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if issues:
        print(f"[factcheck_script] Verdict: {verdict}. {len(issues)} issue(s):")
        for issue in issues:
            print(f"  - {issue.get('claim')}: {issue.get('problem')} -> {issue.get('correction')}")
    else:
        print(f"[factcheck_script] Verdict: {verdict}.")

    if verdict == "major_issues":
        sys.exit(
            "[factcheck_script] Aborting run: the narration's central story could not be "
            "verified. Refusing to build/post a video based on unverified claims."
        )

    if verdict == "minor_issues":
        corrected = (result.get("corrected_narration") or "").strip()
        if not corrected:
            print("[factcheck_script] minor_issues verdict but no corrected_narration provided; "
                  "continuing with the original narration.", file=sys.stderr)
            return
        data["narration"] = corrected
        script_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        Path("build/script.txt").write_text(corrected, encoding="utf-8")
        print("[factcheck_script] Applied corrected narration.")


if __name__ == "__main__":
    main()
