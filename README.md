# yt-shorts-generator

A **near-zero-cost, fully automated pipeline** that builds one ~30-50s vertical (9:16)
motivational short (AI-scripted historical success stories, narrated in the channel
owner's own cloned voice) three times a day and posts it to **TikTok, Instagram,
Facebook, and YouTube Shorts**. Runs on the **GitHub Actions free tier**.

**TikTok + Facebook + Instagram** posting goes through **Buffer** (`post_buffer.py`),
which holds the already-verified platform connections and publishes immediately.
**YouTube** posting is handed off to **Zapier via a shared Google Sheet**
(`post_sheet.py` appends a row; a Zapier "New Spreadsheet Row" trigger posts from
there) — this sidesteps YouTube OAuth token-refresh churn.

## How it works

Each day a GitHub Actions cron job runs these stages in order:

| Stage | Script | What it does |
|-------|--------|--------------|
| 1 | `generate_script_ai.py` | **Trending-first topic pick:** a web-search-grounded GPT call checks live news/pop-culture (NewsAPI headlines + YouTube's trending chart) for a real, current person/story that fits the format, with a hook line built around *why it's relevant now*. Falls back to a random pick from `config/topics.json` (avoiding recently used ones) when nothing trending fits. Either way, GPT then writes the full narration, hook, per-segment footage queries, SEO metadata, hashtags, and platform captions. |
| 1.5 | `factcheck_script.py` | Re-checks the narration's factual claims (dates, numbers, named events) with a web-search-grounded OpenAI call, independent of the model that wrote them. Minor errors are auto-corrected in place; if the core story can't be verified, the run **aborts** rather than build/post it. |
| 2 | `generate_tts.py` | Generates the voiceover with **StyleTTS2**, cloning the channel owner's own voice from a private reference sample fetched at runtime via `VOICE_REPO_PAT`. Falls back to **Kokoro-82M** (offline neural TTS), then **Piper**, then `espeak-ng` if earlier engines fail. |
| 3 | `generate_captions.py` | Builds burned-in captions via **Whisper word-level timestamps** synced to the actual voiceover audio. |
| 4 | `fetch_footage_multi.py` | Fetches one theme-matched HD clip/photo per story segment. Tries **Pexels → Pixabay**, searching by each segment's `footage_query`. Falls back to `generate_animation.py` — a **generated cinematic gradient** (always on-tone, never random) — when no suitable clip is found. |
| 5a | `generate_music.py` | Synthesizes a soft **ambient music pad** with ffmpeg (`build/music.mp3`) — no assets needed. Skipped in favour of real tracks if you drop any in `assets/music/`. |
| 5b | `assemble_video.py` | ffmpeg: crop/pad footage to 1080x1920, burn in **centre-screen** captions + a hook title card + end-card CTA; **denoise + loudness-normalize** the voice, and mix the **background music** under it. |
| 6a | `post_buffer.py` | Posts to **TikTok + Facebook + Instagram** via Buffer's already-verified connections. |
| 6b | `post_sheet.py` | Appends one row (`title \| description \| hashtags \| caption \| video_url \| category`) to the shared Google Sheet. Zapier posts to **YouTube** from there. |
| 7 | workflow step | Appends a row to `logs/history.csv` (including the fact-check verdict) and commits it back. |

The workflow is `.github/workflows/daily-short.yml`. It runs **daily at 14:00 UTC**
(`build_and_post`) and is also runnable on demand via **workflow_dispatch**. There is no
longer a token-refresh job — posting auth lives in Zapier, not in this repo.

> The direct-API posters (`post_meta.py`, `post_tiktok.py`, `post_youtube.py`,
> `refresh_meta_token.py`) are kept in `scripts/` for reference but are **no longer wired
> into the workflow**. Re-wire `post_tiktok.py` once the TikTok app passes audit.

### One-time setup (no Google Cloud needed)

After this ~15-minute setup the pipeline is **fully autonomous** — no tokens to refresh,
nothing to touch daily.

1. Create a Google Sheet with a header row: `title | description | hashtags | caption | video_url | category`.
2. **Deploy the sheet web app (no Cloud project, no key file):** in that sheet, open
   **Extensions → Apps Script**, paste `scripts/sheet_webhook.gs`, then
   **Deploy → New deployment → Web app** (Execute as: *Me*, Who has access: *Anyone*).
   Copy the resulting `…/exec` URL into the `SHEET_WEBHOOK_URL` secret. Optionally set a
   random token in both the script and the `SHEET_WEBHOOK_TOKEN` secret.
3. In Zapier, create one Zap per platform: trigger **Google Sheets → New Spreadsheet Row**
   on this sheet; action **Instagram / Facebook / YouTube → post video**, mapping the
   `video_url` and `caption` columns. Zapier's free tier (~100 tasks/month) covers a
   once-daily post to three platforms (~90/month).

## Content sourcing

- **Text:** Project Gutenberg public-domain excerpts, curated in `config/sources.json`.
- **Video:** theme-matched HD stock from **Pexels** or **Pixabay** (free licenses, free
  commercial use), searched by each quote's `footage_query`. When no suitable stock clip
  is found, `generate_animation.py` renders an on-tone **animated gradient** background
  (ffmpeg, no assets/network) instead of dropping in a random/irrelevant clip — this is
  the guaranteed fallback and never fails. archive.org **NASA** public-domain footage is
  still available (`--source archive`, or add `"archive"` back to `footage.source_order`)
  but is off by default. The `prelinger` collection was removed (off-tone, low-res).
- **Voice:** StyleTTS2, cloning the channel owner's own voice from a private reference
  sample (never stored in this public repo, fetched at runtime). Falls back to
  Kokoro-82M, then Piper, then `espeak-ng` — open-source, offline, CI-friendly.
- **No copyrighted material is downloaded or reused.** (Pexels/Pixabay clips are free-to-
  use under their own licenses; NASA footage is public domain.)

## Required GitHub Secrets

Create these under **Settings → Secrets and variables → Actions**:

| Secret | Used by | Purpose |
|--------|---------|---------|
| `SHEET_WEBHOOK_URL` | `post_sheet.py` | Apps Script web app URL (…/exec) that appends the row to the sheet. See `scripts/sheet_webhook.gs`. No Google Cloud needed. |
| `SHEET_WEBHOOK_TOKEN` | `post_sheet.py` | *Optional.* Shared secret; must match the token in `sheet_webhook.gs` so only your pipeline can write. |
| `PEXELS_API_KEY` | `fetch_footage.py` | *Optional.* Free key from https://www.pexels.com/api/ for HD theme-matched footage (tried first). |
| `PIXABAY_API_KEY` | `fetch_footage.py` | *Optional.* Free key from https://pixabay.com/api/docs/ (tried second). |
| `VOICE_REPO_PAT` | `generate_tts.py` | *Optional.* Fine-grained, read-only GitHub PAT scoped to a separate private repo holding the cloned-voice reference sample. Without it, TTS falls back to Kokoro-82M. **Never paste this token into chat or commit it anywhere** — add it directly via GitHub Settings → Secrets and variables → Actions. |
| `OPENAI_API_KEY` | `generate_script_ai.py`, `factcheck_script.py`, `generate_captions.py` | Writes the narration, fact-checks it with web search, and (as a fallback) transcribes Whisper captions. |
| `BUFFER_API_KEY` / `BUFFER_ORG_ID` | `post_buffer.py` | Buffer account credentials for posting to TikTok, Facebook, and Instagram. |
| `NEWS_SECRETS` | `fetch_trending_topic.py` | *Optional.* API key from https://newsapi.org/ (free tier), used to read today's top headlines as a trending-topic signal. |
| `YOUTUBE_API_KEY` | `fetch_trending_topic.py` | *Optional.* A plain Google Cloud API key (not OAuth) with the YouTube Data API v3 enabled, used only to read the public "most popular videos" chart as a trending-topic signal. Without either this or `NEWS_SECRETS`, trending selection relies on OpenAI web search alone. |

Footage degrades gracefully: with **no** stock key set, `fetch_footage.py` falls back to
free archive.org NASA footage automatically. Set at least one stock key for the best
quality + relevance.

`GITHUB_TOKEN` (built-in) is used to create the release asset and commit the log — no
setup needed. The old per-platform token secrets (`META_*`, `TIKTOK_*`, `YOUTUBE_*`,
`GH_PAT`) are **no longer used** and can be deleted once you've confirmed the Zapier path
works.

## Posting notes

- **Public video URL still required.** Zapier's Instagram/Facebook/YouTube actions post
  from the `video_url` column, so the workflow still uploads `final.mp4` as a GitHub
  **Release asset** and writes that public URL into the sheet row. **This only works if
  the repo is PUBLIC**; for a private repo, host the mp4 elsewhere and set the URL there.
- **No tokens in the repo.** All platform authentication now lives inside the Zaps
  (Zapier's own connections), which is why the token-refresh job and all `META_*` /
  `TIKTOK_*` / `YOUTUBE_*` secrets are gone.
- **TikTok** is not posted — its app isn't audited and Zapier has no free TikTok
  content-posting integration. `scripts/post_tiktok.py` is retained for when that changes.

## Running / debugging locally

Every stage is independently runnable. Typical local dry-run:

```bash
pip install -r requirements.txt
sudo apt-get install -y ffmpeg fonts-dejavu-core espeak-ng

python scripts/fetch_script_text.py
python scripts/fetch_footage.py          # PEXELS_API_KEY/PIXABAY_API_KEY optional; falls back to a generated animation
# python scripts/fetch_footage.py --source animate   # force the generated gradient background
python scripts/generate_tts.py          # tries StyleTTS2 (needs VOICE_REPO_PAT) -> Kokoro -> Piper -> espeak
# python scripts/generate_tts.py --engine kokoro     # skip the voice clone, force a specific engine
python scripts/generate_captions.py
python scripts/generate_music.py       # ambient pad -> build/music.mp3 (or drop tracks in assets/music/)
python scripts/assemble_video.py
# -> build/final.mp4
```

Posting now goes through `post_sheet.py` → Apps Script web app → Google Sheet → Zapier;
set `SHEET_WEBHOOK_URL` to test it. The old direct-API posters are unwired (see note above).

## Limitations / things to verify

- **Repo must be public** for the release-asset public-URL (which Zapier posts from) to work.
- **TikTok is not posted** — its app isn't audited and Zapier has no free TikTok posting
  integration. `scripts/post_tiktok.py` is retained for when that changes.
- CLI flags / paths marked with `# VERIFY:` comments (Piper CLI flags such as
  `--length_scale`/`--sentence_silence`, ffmpeg font paths) should be confirmed against the
  versions actually installed on the runner.
- The pipeline is intentionally simple (one JSON config, no framework) — it's a personal
  hobby pipeline, not enterprise software.
