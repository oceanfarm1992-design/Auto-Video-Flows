#!/usr/bin/env python3
"""
Helper for generate_script_ai.py: gather a trending-topic signal from YouTube's
official "most popular" chart (Data API, needs only a plain API key — no OAuth).

Google Trends' public trending-searches endpoints (the old CSV/RSS feeds and the
pytrends library built on them) are dead as of this writing (they now 404), so
YouTube's chart is the structured signal here. It's combined with live OpenAI
web search in generate_script_ai.py's pick_trending_topic() for the actual
news/pop-culture read — this module only supplies the YouTube title list.

Never raises: any failure (missing key, network, quota) just returns an empty
list so the caller falls through to its next signal / the static topic list.
"""
import requests

YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def get_youtube_trending(api_key: str, region: str = "US", max_results: int = 15) -> list[str]:
    if not api_key:
        return []
    try:
        resp = requests.get(
            YOUTUBE_VIDEOS_URL,
            params={
                "part": "snippet",
                "chart": "mostPopular",
                "regionCode": region,
                "maxResults": max_results,
                "key": api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return [item["snippet"]["title"] for item in items if item.get("snippet", {}).get("title")]
    except Exception as exc:  # noqa: BLE001 — this signal is optional, never fatal
        print(f"[fetch_trending_topic] YouTube trending fetch failed: {type(exc).__name__}: {exc}")
        return []
