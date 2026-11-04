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
| 6a | `post_buffer.py` | Posts to **TikTok + Facebook + Instagram** via Buffer's already-verified connections. TikTok gets its own SEO-aware caption (`caption_tiktok.txt`) instead of reusing Instagram's — see [TikTok SEO keywords](#tiktok-seo-keywords) below. |
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
- **No platform tokens in the repo.** Buffer and Zapier hold the actual TikTok/Facebook/
  Instagram/YouTube connections; this repo only holds a Buffer API key and a Sheet webhook
  URL. `scripts/post_tiktok.py`, `post_meta.py`, `post_youtube.py`, and
  `refresh_meta_token.py` are earlier direct-API attempts, kept for reference but no longer
  wired into the workflow.

## TikTok SEO keywords

`config/topics.json`'s `tiktok_seo_keywords.keywords` list feeds `caption_tiktok`/
`hashtags_tiktok` generation — each run, GPT is offered a random sample of 6 and asked to
weave in 2-3 that genuinely fit the story.

TikTok's real **Creator Search Insights** (live trending/underserved search terms for your
niche) has no public API and lives inside TikTok Studio behind your own login, so this list
isn't pulled automatically. **Refresh it periodically yourself**: open TikTok Studio →
Analytics → Creator Search Insights, and swap the keyword list in `config/topics.json` for
whatever's currently relevant.

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

## Topic series: "Why men..." and psychology / power

Two extra flows reuse the same pipeline (`.github/workflows/niche-short.yml`):

| Workflow | Config |
|---|---|
| `men-short.yml` | `config/niches/men_psychology.json` |
| `power-short.yml` | `config/niches/power_psychology.json` |

Topics come from the config's `topics` list (edit freely). Each script is fact-checked, voiced, and posted to TikTok/Facebook/Instagram via the main Buffer account (`BUFFER_API_KEY`, `BUFFER_ORG_ID`) and to YouTube via a second Buffer account. No Zapier is used.

Extra repo secrets (add in GitHub → Settings → Secrets → Actions): `BUFFER_YT_API_KEY`, `BUFFER_YT_ORG_ID`. If `BUFFER_YT_API_KEY` is empty, the YouTube step is skipped.

Test without posting: `gh workflow run men-short.yml -f skip_post=true`.

### Schedule: one video/day, rotating flows

`men-short.yml` and `power-short.yml` have no schedule of their own anymore —
`.github/workflows/rotate-short.yml` runs once a day at **14:00 UTC** and triggers
exactly one of them, in rotation:

```
day 1 -> men-short    day 2 -> power-short    day 3 -> men-short  ...
```

The rotation key is `(days since epoch) % 2`, so it's deterministic with nothing to
store. Force a specific flow (e.g. for testing) with
`gh workflow run rotate-short.yml -f force_flow=men-short -f skip_post=true`.

`daily-short.yml` (the original historical/trending-figure series) is **no longer run
automatically**. The workflow file is kept for manual runs only:
`gh workflow run daily-short.yml -f skip_post=true`.

## Cartoon-skit format (`format: skit`)

A second format for both series, built from an analysis of top-performing AI cartoon skits on TikTok.
In those videos the "3D-looking" depth comes from flat 2D backgrounds painted in perspective, animated by an
image-to-video model with a slow push-in. They are cut on every line of dialogue, and every character wears a
cap with their name. This pipeline produces the same kind of video for our niches. A recurring cast acts out
each topic in 10-14 shots, and the narrator (the channel's cloned voice) closes with the psychology insight.

| Stage | Script | What it does |
|---|---|---|
| 1 | `generate_skit.py` | GPT writes the skit: hook line in the middle of a conflict, setup, escalation, reveal, payoff, then the narrator's insight. Each shot gets a speaker, line, acting note, location, visible characters, framing, a drawable `action` and a `motion`. A validator rejects bad drafts (unknown cast or location, digits, banned filler, the wrong length, the narrator not last) and asks for a rewrite, up to 3 drafts. |
| 1.5 | `factcheck_script.py` | Same fact-check as before. The lines are what get voiced, so any verdict other than pass, skip or error writes a fresh skit on a new topic once. |
| 2 | `generate_dialogue_tts.py` | One voice per character, from `config/niches/<niche>.json → skit.cast.<id>.voice`. Engines in order: OpenAI `gpt-4o-mini-tts` with the line's acting direction, then Kokoro, then espeak. The narrator tries the cloned StyleTTS2 voice first. Writes `build/voice.wav` and `build/skit_timeline.json` (exact start and end of every line). |
| 3 | `generate_captions.py --timeline … --highlight …` | Word-by-word captions with the active word in green. Whisper when available, otherwise each line's words spread over that line's own timing. |
| 4 | `generate_keyframes.py` | Designs each character sheet and location plate **once** and caches it in `assets/skit/<niche>/` (committed by the workflow), then draws one keyframe per shot using those images as references, which keeps the cast consistent. Provider: `openai` (gpt-image, default), `fal`, or `none`. |
| 5 | `animate_shots.py` | Image-to-video on **fal.ai** (Kling by default; any fal image-to-video model via `config/skit.json → video.fal_model`). All shots go to the queue at once. Without `FAL_KEY` it falls back to a free local push-in on the keyframe. A shot with no keyframe is drawn by the doodle engine. |
| 6 | `assemble_skit.py` | Hard cuts on each line, conformed to **24 fps** (no 24→30 duplicate-frame judder), vignette, light grain, hook title, captions, CTA, and voice over a quiet music bed. Writes `build/final.mp4`. |

Posting, release upload and history logging are the same steps as the narrated format.

### Turning it on

1. **Optional but recommended:** add the `FAL_KEY` secret (https://fal.ai, pay per clip) for real character
   animation. Without it, skits use the free push-in on AI keyframes.
2. **Design the cast first:** run the **skit-cast** workflow (Actions → skit-cast → niche) and review the
   images in the run's artifact. Re-run it with `commit=true` when you like them. To redesign one character,
   delete its PNG under `assets/skit/<niche>/characters/` and run it again. The cast's looks, cap names and
   voices are in `config/niches/<niche>.json → skit`.
3. **Test a skit without posting:** `gh workflow run men-short.yml -f format=skit -f skip_post=true`
   (the run uploads the keyframes and script as an artifact).
4. **Switch a series over:** set `"format": "skit"` in `config/niches/<niche>.json`. The daily rotation
   then posts skits for that series. Or keep the narrated format and run skits on demand with `-f format=skit`.

Repo variables (Settings → Variables) `IMAGE_PROVIDER` (`openai` | `fal` | `none`) and `VIDEO_PROVIDER`
(`fal` | `local`) override `config/skit.json` without a commit.

**Free script writing (OpenRouter):** add the `OPENROUTER_API_KEY` secret and skits are written by free
OpenRouter models (`scripts/llm.py`, `config/llm.json`), with GPT-4.1 as the automatic fallback when the free
tier is rate-limited (20/min, 50/day) or a free model returns unusable JSON. `script.json → writer` records
which model wrote each video. The live narrated series stays on GPT-4.1 unless you add `"narrated_script"`
to `openrouter_tasks`.

**Cost per skit (low-cost defaults):** about 4 low-quality pictures (one master per location, plus the
hook, close-ups and payoff) and roughly 6¢ of text, voice and captions, so about **$0.10–0.20 per video**
with no fal.ai key. Every other line reuses its location's master: a virtual camera pans and zooms onto the
speaker, and close-ups are tighter crops. Tune it in `config/skit.json → images`: `openai_quality` (`low` /
`medium`), `max_keyframes` (or the `MAX_KEYFRAMES` env var) and `ref_max_side`. Adding `FAL_KEY` animates
every shot on fal.ai, which costs several dollars per skit. `IMAGE_PROVIDER=none` makes a free doodle skit.

### Techniques adopted from MoneyPrinterTurbo

Ideas taken from [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) (MIT) and
rewritten for this pipeline:

- **Edge TTS** (`scripts/tts_edge.py`): free Microsoft neural voices, no key. Each skit character has its own
  `edge` voice in the niche config, and `generate_tts.py` uses it after the cloned voice (`--engine edge` / `auto`).
  It reports when every word is spoken, which is written to `build/word_timings.json`, so captions are exact
  with no Whisper call. It is an unofficial endpoint, so after one failure the rest of the run skips it, and its
  calls have a hard timeout (edge-tts's own `stream_sync()` can hang forever on a network error).
- **Captions corrected against the script** (`generate_captions.align_to_script`): Whisper's timing is kept, but
  the words shown are the script's. That fixes misheard names, digits for spelled-out numbers and dropped words.
  A transcript that doesn't resemble the script is left alone.
- **Downloaded AI clips are validated** (`animate_shots.valid_video`): every fal.ai clip is fully decoded before
  use. A truncated or empty clip is downloaded again, then replaced by the free push-in if still broken.

### Sad piano music

Both formats now use a **sad solo-piano piece composed and synthesized for each run**
(`scripts/generate_piano.py`, called by `generate_music.py`). It needs no samples and no API, and has no
copyright issues. Each note is synthesized as a piano string with inharmonic partials, two slightly detuned
strings, a hammer attack, pedal sustain and room reverb. The music is in a minor key at 52–62 BPM, using sad
progressions (i–VI–III–VII, i–VI–iv–V…): a broken-chord left hand, a repeated and varied right-hand motif, an
intro, the theme an octave higher, and an outro ending on the tonic. The seed follows the GitHub run, so every
video gets a different piece. In skits the piano ducks under the dialogue and swells back in the pauses.
Switch back to the old ambient pad with `config/music.json → "style": "pad"`. Tracks in `assets/music/`
still win over both.

### Mouth flaps on AI pictures

With `images.lipsync` on (default), every AI keyframe gets one extra low-quality image edit with everyone's
mouth open (about 1–2¢). `scripts/mouth_flaps.py` aligns the two pictures, finds each character's mouth (the
compact regions that turned dark), and while a character speaks it swaps in only their mouth from the open
picture, timed by the lip-sync track (Rhubarb on CI). The rest of the frame stays pixel-identical, and
listeners keep their mouths closed. If a mouth can't be found, that shot falls back to the still picture.

### Free doodle skits with lip sync

When a shot has no AI keyframe (`IMAGE_PROVIDER=none`, or an image failed), `scripts/doodle_skit.py` draws
**the shot itself**, at no cost:
- the shot's cast as stick figures, each with a cap in their configured colour and their name above their head;
- the speaker's mouth lip-synced to the voice, with talking gestures that follow its loudness;
- listeners that blink, breathe and react;
- background props taken from the location's description (sofa, window, desk, shelves...);
- close-ups when the framing asks for one.

`scripts/lipsync.py` picks the mouth shapes (the standard 9-shape cartoon set). It uses
[Rhubarb Lip Sync](https://github.com/DanielSWolf/rhubarb-lip-sync) (MIT) when it is installed: the workflow
installs it, and `RHUBARB_BIN` points to it. Otherwise it uses a built-in method: each word's letters become
shapes timed by the word timings, and the mouth closes wherever the voice is silent.

Offline tests: `python tests/test_skit.py`, `python tests/test_edge_captions.py`, `python tests/test_lipsync.py`. Full offline dry run (no keys): write `build/script.json` with
`generate_skit.normalise(...)`, then `generate_dialogue_tts.py --engines silent`,
`IMAGE_PROVIDER=none generate_keyframes.py`, `VIDEO_PROVIDER=local animate_shots.py`, `assemble_skit.py`.
