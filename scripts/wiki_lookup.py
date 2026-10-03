#!/usr/bin/env python3
"""
Free fact-check source for the daily (historical-figure) flow: Wikipedia's
official MediaWiki API, instead of paying OpenAI for a web_search tool call
on every run.

Wikipedia is a better primary source for "is this claim about a historical
figure accurate" than a generic search anyway, and the API is free, stable,
and documented — no scraping, no blocking risk, just a descriptive
User-Agent header as required by Wikipedia's API etiquette.

This only covers the common case (a figure with an established Wikipedia
page). factcheck_script.py falls back to OpenAI's web_search tool when
get_context() returns None — e.g. a very recent trending figure without a
detailed page yet.
"""
import sys
from typing import Optional

try:
    import requests
except ImportError:
    sys.exit("requests package not installed. Run: pip install requests")

WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = (
    "Auto-Video-Flows-FactCheck/1.0 "
    "(+https://github.com/oceanfarm1992-design/Auto-Video-Flows)"
)
MIN_CONTEXT_CHARS = 200  # shorter than this isn't enough to fact-check against


def _get(params: dict) -> dict:
    resp = requests.get(
        WIKI_API,
        params={**params, "format": "json"},
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _extract_for_title(title: str, exchars: int) -> Optional[str]:
    data = _get({
        "action": "query",
        "prop": "extracts",
        "explaintext": 1,
        "exchars": exchars,
        "redirects": 1,
        "titles": title,
    })
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        if "missing" in page:
            continue
        extract = (page.get("extract") or "").strip()
        if extract:
            return extract
    return None


def _search_title(query: str) -> Optional[str]:
    data = _get({
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": 1,
    })
    hits = data.get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def get_context(person: str, exchars: int = 4000,
                 min_chars: int = MIN_CONTEXT_CHARS) -> Optional[str]:
    """Plain-text Wikipedia extract about `person`, or None if no page exists
    or the page has too little content to fact-check against. Tries the
    name directly first (handles redirects), then falls back to a search
    for the closest matching title."""
    try:
        extract = _extract_for_title(person, exchars)
        if not extract or len(extract) < min_chars:
            title = _search_title(person)
            if title:
                extract = _extract_for_title(title, exchars) or extract
        if extract and len(extract) >= min_chars:
            return extract
        return None
    except Exception as exc:  # noqa: BLE001 — caller falls back to web_search
        print(f"[wiki_lookup] lookup failed for '{person}': {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Manual check: fetch a Wikipedia extract.")
    ap.add_argument("person")
    ap.add_argument("--chars", type=int, default=4000)
    args = ap.parse_args()
    ctx = get_context(args.person, exchars=args.chars)
    text = ctx if ctx else f"No usable Wikipedia context found for '{args.person}'."
    try:
        print(text)
    except UnicodeEncodeError:  # narrow Windows console codepages can't print some names
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace") + b"\n")
