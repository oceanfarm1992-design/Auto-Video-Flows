#!/usr/bin/env python3
"""
Helper for generate_script_ai.py: gather structured trending-topic signals for
pick_trending_topic()'s web-search-grounded GPT call to reason over —
  - NewsAPI.org top headlines (real current news, needs NEWS_SECRETS)
  - YouTube's official "most popular" chart (Data API, plain API key, no OAuth)

Google Trends' public trending-searches endpoints (the old CSV/RSS feeds and the
pytrends library built on them) are dead as of this writing (they now 404), so
these two are the structured signals instead of Google Trends.

Both are optional and never raise: any failure (missing key, network, quota)
just returns an empty list so the caller falls through to its next signal /
the static topic list.
"""
import requests

NEWSAPI_URL = "https://newsapi.org/v2/top-headlines"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def get_news_headlines(api_key: str, country: str = "us", max_results: int = 20) -> list[str]:
    if not api_key:
        return []
    try:
        resp = requests.get(
            NEWSAPI_URL,
            params={"country": country, "pageSize": max_results, "apiKey": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        headlines = []
        for article in resp.json().get("articles", []):
            title = (article.get("title") or "").strip()
            if not title:
                continue
            desc = (article.get("description") or "").strip()
            headlines.append(f"{title} — {desc}" if desc else title)
        return headlines
    except Exception as exc:  # noqa: BLE001 — this signal is optional, never fatal
        print(f"[fetch_trending_topic] NewsAPI fetch failed: {type(exc).__name__}: {exc}")
        return []


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
