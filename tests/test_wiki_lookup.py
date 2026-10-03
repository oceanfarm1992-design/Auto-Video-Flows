"""Tests for scripts/wiki_lookup.py — mocks requests.get so these don't
depend on live network access (CI and local runs stay deterministic)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import wiki_lookup as wl

FAILED = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILED.append(name)


def fake_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def extract_payload(title, extract, missing=False):
    page = {"missing": ""} if missing else {"extract": extract}
    return {"query": {"pages": {"1": {**page, "title": title}}}}


# direct title hit, long enough extract
with patch("wiki_lookup.requests.get") as mget:
    mget.return_value = fake_response(extract_payload("Marie Curie", "x" * 500))
    ctx = wl.get_context("Marie Curie")
    check("direct hit returns extract", ctx == "x" * 500)
    check("sends descriptive User-Agent", "User-Agent" in mget.call_args.kwargs["headers"])

# missing page -> falls back to search -> second extract call
with patch("wiki_lookup.requests.get") as mget:
    mget.side_effect = [
        fake_response(extract_payload("Nonexistent Person", "", missing=True)),
        fake_response({"query": {"search": [{"title": "Real Person"}]}}),
        fake_response(extract_payload("Real Person", "y" * 400)),
    ]
    ctx = wl.get_context("Nonexistent Person")
    check("search fallback finds a real title", ctx == "y" * 400)
    check("search fallback makes 3 calls", mget.call_count == 3)

# too-short extract and no better search result -> None
with patch("wiki_lookup.requests.get") as mget:
    mget.side_effect = [
        fake_response(extract_payload("Obscure Topic", "short")),
        fake_response({"query": {"search": []}}),
    ]
    ctx = wl.get_context("Obscure Topic")
    check("too-short extract with no search hit returns None", ctx is None)

# network/API error -> None, doesn't raise
with patch("wiki_lookup.requests.get", side_effect=RuntimeError("boom")):
    ctx = wl.get_context("Anyone")
    check("exception is swallowed, returns None", ctx is None)

sys.exit(1 if FAILED else 0)
