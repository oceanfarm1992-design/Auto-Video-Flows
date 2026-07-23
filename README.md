# yt-shorts-generator

A **zero-cost, fully automated daily pipeline** that builds one ~30-50s vertical (9:16)
motivational short from **public-domain sources** and posts it to **Instagram Reels,
Facebook, TikTok, and YouTube Shorts**. Everything runs on the **GitHub Actions free
tier** — no paid infrastructure, no YouTube ripping.

## How it works

Each day a GitHub Actions cron job runs these stages in order:

| Stage | Script | What it does |
|-------|--------|--------------|
| 1 | `fetch_script_text.py` | Picks a public-domain excerpt (Marcus Aurelius / Emerson / Seneca) from `config/sources.json`, rotating by date. Also writes per-platform caption files. |
| 2 | `fetch_archive_footage.py` | Pulls a B-roll clip from an **allowlisted** archive.org collection (`prelinger`, `nasa`) using the documented advancedsearch + metadata + download APIs. |
| 3 | `generate_tts.py` | Generates the voiceover with **Piper TTS** (offline, no API key). Falls back to `espeak-ng` if Piper fails. |
| 4 | `generate_captions.py` | Builds a burned-in `.srt` from the known script text + measured audio duration (no transcription needed). |
| 5 | `assemble_video.py` | ffmpeg: crop/pad footage to 1080x1920, burn in animated captions, a hook title card, and an end-card CTA; mux with the voiceover. |
| 6 | `post_meta.py` / `post_tiktok.py` / `post_youtube.py` | Publish to all four destinations. |
| 7 | workflow step | Appends a row to `logs/history.csv` and commits it back. |

The workflow is `.github/workflows/daily-short.yml`. It has two schedules:
- **daily 14:00 UTC** → `build_and_post`
- **Mondays 06:00 UTC** → `refresh_meta_token` (keeps the Meta long-lived token fresh)

Both jobs are also runnable on demand via **workflow_dispatch**.

## Content sourcing (public domain only)

- **Text:** Project Gutenberg public-domain excerpts, curated in `config/sources.json`.
- **Video:** archive.org `prelinger` and `nasa` collections (verified public domain),
  fetched via `https://archive.org/advancedsearch.php` + `https://archive.org/metadata/<id>`.
- **Voice:** Piper TTS — open-source, offline, CI-friendly.
- **No copyrighted material is downloaded or reused.**

## Required GitHub Secrets

Create these under **Settings → Secrets and variables → Actions**:

| Secret | Used by | Purpose |
|--------|---------|---------|
| `META_ACCESS_TOKEN` | Meta post + refresh | Long-lived access token (auto-refreshed weekly). |
| `META_IG_USER_ID` | `post_meta.py` | Instagram business account user id. |
| `META_PAGE_ID` | `post_meta.py` | Facebook Page id. |
| `META_APP_ID` | `refresh_meta_token.py` | Meta app id (token exchange). |
| `META_APP_SECRET` | `refresh_meta_token.py` | Meta app secret (token exchange). |
| `TIKTOK_CLIENT_KEY` | `post_tiktok.py` | TikTok app client key. |
| `TIKTOK_CLIENT_SECRET` | `post_tiktok.py` | TikTok app client secret. |
| `TIKTOK_REFRESH_TOKEN` | `post_tiktok.py` | Rotated + rewritten on **every** run. |
| `YOUTUBE_CLIENT_ID` | `post_youtube.py` | Google OAuth client id. |
| `YOUTUBE_CLIENT_SECRET` | `post_youtube.py` | Google OAuth client secret. |
| `YOUTUBE_REFRESH_TOKEN` | `post_youtube.py` | Google OAuth refresh token (long-lived). |
| `GH_PAT` | tiktok / meta refresh | PAT with **Secrets: write** so scripts can rewrite rotated tokens. |

`GITHUB_TOKEN` (built-in) is used to create the release asset and commit the log — no
setup needed.

## Platform auth notes

### Meta (Instagram Reels + Facebook)
- Instagram Reels publishing is a **2-step container flow** and **requires a public
  https video URL** — Meta fetches the file itself. The workflow uploads `final.mp4`
  as a GitHub **Release asset** and passes its public download URL.
  **This only works if the repo is PUBLIC.** For a private repo, host the mp4 elsewhere.
- Facebook Page video is uploaded directly (multipart), no public URL needed.
- `refresh_meta_token.py` re-exchanges the long-lived token weekly (60-day lifetime)
  and writes it back to `META_ACCESS_TOKEN` via the GitHub API.

### TikTok
- Access tokens expire in ~24h and refresh tokens **rotate on each use**, so
  `post_tiktok.py` refreshes first, **immediately persists the new refresh token** back
  to `TIKTOK_REFRESH_TOKEN`, then uploads.
- **Until the TikTok app passes audit**, posts are `privacy_level = SELF_ONLY` (visible
  only to you). You must change visibility manually in the TikTok app until approved.

### YouTube
- OAuth refresh-token flow; a fresh access token is minted each run (nothing written back).
- Discloses AI/synthetic assembly via `status.containsSyntheticMedia = true`
  (the Data API field added 2024-10-30).

## Running / debugging locally

Every stage is independently runnable. Typical local dry-run:

```bash
pip install -r requirements.txt
sudo apt-get install -y ffmpeg fonts-dejavu-core espeak-ng

python scripts/fetch_script_text.py
python scripts/fetch_archive_footage.py
python scripts/generate_tts.py          # or: --fallback espeak
python scripts/generate_captions.py
python scripts/assemble_video.py
# -> build/final.mp4
```

The posting scripts read credentials from env vars; export the relevant ones and add
`--no-write` (TikTok) to avoid touching real secrets while testing.

## Limitations / things to verify

- **Repo must be public** for the Instagram Reels public-URL fetch to work.
- **TikTok is draft/SELF_ONLY** until the app is audited.
- Several exact API version strings and CLI flags are marked with `# VERIFY:` comments
  in the scripts (Graph API version, Piper CLI flags, ffmpeg font paths, YouTube
  category id / synthetic-media field). Check those before relying on production posting.
- The pipeline is intentionally simple (one JSON config, no framework) — it's a personal
  hobby pipeline, not enterprise software.
