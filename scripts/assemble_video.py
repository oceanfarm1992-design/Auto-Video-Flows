#!/usr/bin/env python3
"""
Stage 5: assemble the final 1080x1920 vertical short with ffmpeg.

Pipeline (single ffmpeg invocation with -filter_complex):
  1. Take the archive.org footage, scale-to-cover and crop to 1080x1920 (9:16),
     normalize to 30fps / yuv420p. Footage is looped (-stream_loop -1) and the
     output is cut to the voiceover length, so short clips still fill the video.
  2. Burn in a hook title card (drawtext) for the first few seconds.
  3. Burn in the animated captions from the SRT (subtitles filter).
  4. Burn in a small end-card CTA/watermark for the last few seconds.
  5. Mux with the TTS voiceover; drop the original footage audio.

ffmpeg is preinstalled on GitHub Actions Ubuntu runners.

# VERIFY: font paths. On ubuntu-latest the DejaVu fonts live at
#   /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
#   /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
# If drawtext errors with "Cannot find a valid font", run `fc-list | grep -i dejavu`
# in CI to find the real path, or `sudo apt-get install -y fonts-dejavu-core`.

Output: build/final.mp4

Usage:
    python scripts/assemble_video.py
    python scripts/assemble_video.py --footage build/footage.mp4 --audio build/voice.wav \
        --captions build/captions.srt --script build/script.json --out build/final.mp4
"""
import argparse
import json
import os
import subprocess
import sys

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise SystemExit(f"ffprobe failed on {path}: {out.stderr}")
    return float(out.stdout.strip())


def drawtext_escape(text):
    """Escape characters special to ffmpeg's drawtext text= option."""
    return (text.replace("\\", "\\\\")
                .replace(":", "\\:")
                .replace("'", "\\'")
                .replace("%", "\\%"))


def build_filter_complex(hook, cta, captions_path, duration):
    hook_e = drawtext_escape(hook)
    cta_e = drawtext_escape(cta)
    # subtitles filter path: colons/backslashes would need escaping on Windows,
    # but CI runs on Linux with a simple relative path.
    subs = captions_path.replace("\\", "/")
    hook_end = 4.0
    cta_start = max(0.0, duration - 4.0)

    caption_style = (
        "FontName=DejaVu Sans,Fontsize=16,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,"
        "Alignment=2,MarginV=280"
    )
    # VERIFY: force_style keys are ASS style names (case-sensitive-ish). If captions
    # look unstyled, check the ffmpeg build supports libass (`ffmpeg -filters | grep subtitles`).

    parts = [
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,setsar=1,fps=30,format=yuv420p[base]",

        # hook title card (top third), bold, semi-transparent box
        f"[base]drawtext=fontfile={FONT_BOLD}:text='{hook_e}':"
        "fontcolor=white:fontsize=54:line_spacing=8:"
        "box=1:boxcolor=black@0.5:boxborderw=24:"
        f"x=(w-text_w)/2:y=h*0.14:enable='between(t,0,{hook_end})'[v1]",

        # burned-in captions
        f"[v1]subtitles='{subs}':force_style='{caption_style}'[v2]",

        # end-card CTA / watermark (bottom)
        f"[v2]drawtext=fontfile={FONT_BOLD}:text='{cta_e}':"
        "fontcolor=white:fontsize=44:"
        "box=1:boxcolor=black@0.55:boxborderw=20:"
        f"x=(w-text_w)/2:y=h*0.86:enable='gte(t,{cta_start:.2f})'[vout]",
    ]
    return ";".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--footage", default="build/footage.mp4")
    ap.add_argument("--audio", default="build/voice.wav")
    ap.add_argument("--captions", default="build/captions.srt")
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--config", default="config/sources.json")
    ap.add_argument("--out", default="build/final.mp4")
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as f:
        script = json.load(f)
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)["video"]

    hook = script.get("hook", "STAY STRONG")
    cta = cfg.get("cta_text", "Follow for daily wisdom")

    duration = ffprobe_duration(args.audio)
    # clamp to configured bounds (in case an excerpt is unusually long/short)
    duration = max(cfg["min_seconds"], min(duration + 0.6, cfg["max_seconds"]))
    print(f"[assemble_video] target duration {duration:.1f}s")

    filter_complex = build_filter_complex(hook, cta, args.captions, duration)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", args.footage,   # loop footage to cover audio
        "-i", args.audio,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "1:a",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart",
        args.out,
    ]
    print("[assemble_video] running ffmpeg...")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print("ERROR: ffmpeg assembly failed.", file=sys.stderr)
        sys.exit(proc.returncode)
    print(f"[assemble_video] wrote {args.out}")


if __name__ == "__main__":
    main()
