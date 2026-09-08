#!/usr/bin/env python3
"""
Stage 6: Assemble the final 1080x1920 vertical short with ffmpeg.

Reads build/footage_manifest.json for a list of clips/photos (one per
story segment), prepares each to its target duration, then concatenates
them with a soft crossfade. Burns in hook title, word-level subtitles,
and CTA end-card. Mixes OpenAI TTS voice + background music.

Falls back to the legacy single-clip path if no manifest is found.

Output: build/final.mp4

Usage:
    python scripts/assemble_video.py
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD    = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
MUSIC_EXTS   = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac")
CROSSFADE_S  = 0.7   # seconds of crossfade between clips
# Cycle through visually distinct transitions so each cut feels different
TRANSITIONS  = ["slideleft", "slideright", "wipeleft", "fade"]


def pick_music(music_dir: str) -> str | None:
    if not music_dir or not os.path.isdir(music_dir):
        return None
    tracks = sorted(f for f in os.listdir(music_dir)
                    if f.lower().endswith(MUSIC_EXTS))
    if not tracks:
        return None
    idx = datetime.date.today().timetuple().tm_yday % len(tracks)
    return os.path.join(music_dir, tracks[idx])


def ffprobe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise SystemExit(f"ffprobe failed on {path}: {out.stderr}")
    return float(out.stdout.strip())


def drawtext_escape(text: str) -> str:
    return (text.replace("\\", "\\\\")
                .replace(":", "\\:")
                .replace("'", "\\'")
                .replace("%", "\\%"))


def build_audio_filter(n_video_inputs: int, has_music: bool,
                       duration: float, music_vol: float) -> str:
    voice_idx = n_video_inputs  # audio is the input after all video clips
    voice = f"[{voice_idx}:a]afftdn=nr=12,highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11"
    if not has_music:
        return voice + "[aout]"
    music_idx = n_video_inputs + 1
    fade_out = max(0.0, duration - 2.0)
    return (
        voice + "[va];"
        f"[{music_idx}:a]volume={music_vol},afade=t=in:st=0:d=1.5,"
        f"afade=t=out:st={fade_out:.2f}:d=2[mus];"
        "[va][mus]amix=inputs=2:duration=longest:normalize=0[aout]"
    )


def prepare_clip_filter(idx: int, clip: dict) -> str:
    """Return a filter chain that normalises one clip to 1080x1920 @ 30fps."""
    dur = clip["duration"]
    if clip["type"] == "photo":
        # Ken Burns: slow zoom-in over the segment duration
        frames = max(1, int(30 * dur))
        return (
            f"[{idx}:v]"
            f"scale=1920:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,"
            f"zoompan=z='min(zoom+0.0015,1.25)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:fps=30:s=1080x1920,"
            f"setsar=1,format=yuv420p"
            f"[pre{idx}]"
        )
    else:
        return (
            f"[{idx}:v]"
            f"scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,setsar=1,fps=30,format=yuv420p,"
            f"trim=duration={dur:.3f},setpts=PTS-STARTPTS"
            f"[pre{idx}]"
        )


def build_filter_complex(clips: list, hook: str, cta: str,
                         captions_path: str, total_duration: float) -> str:
    parts = []
    n = len(clips)

    # 1. Prepare each clip
    for i, clip in enumerate(clips):
        parts.append(prepare_clip_filter(i, clip))

    # 2. Crossfade concatenation
    if n == 1:
        parts.append("[pre0]copy[vconcat]")
    else:
        # chain xfade with rotating transition types so each cut looks distinct
        offset = 0.0
        for i in range(n - 1):
            a   = f"[xf{i-1}]" if i > 0 else "[pre0]"
            b   = f"[pre{i+1}]"
            out = "[vconcat]" if i == n - 2 else f"[xf{i}]"
            offset += clips[i]["duration"] - CROSSFADE_S
            transition = TRANSITIONS[i % len(TRANSITIONS)]
            parts.append(
                f"{a}{b}xfade=transition={transition}:duration={CROSSFADE_S}:"
                f"offset={offset:.3f}{out}"
            )

    # 3. Hook title card (top, first 4 s)
    hook_e = drawtext_escape(hook)
    parts.append(
        f"[vconcat]drawtext=fontfile={FONT_BOLD}:text='{hook_e}':"
        "fontcolor=white:fontsize=52:line_spacing=8:"
        "box=1:boxcolor=black@0.55:boxborderw=22:"
        "x=(w-text_w)/2:y=h*0.13:enable='between(t,0,4.0)'[v1]"
    )

    # 4. Burned-in subtitles (word-level SRT)
    subs = captions_path.replace("\\", "/")
    caption_style = (
        "FontName=DejaVu Sans,Fontsize=17,Bold=1,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,"
        "Alignment=2,MarginV=144"
    )
    parts.append(f"[v1]subtitles='{subs}':force_style='{caption_style}'[v2]")

    # 5. CTA end-card (bottom, last 4 s)
    cta_e  = drawtext_escape(cta)
    cta_st = max(0.0, total_duration - 4.0)
    parts.append(
        f"[v2]drawtext=fontfile={FONT_BOLD}:text='{cta_e}':"
        "fontcolor=white:fontsize=40:"
        "box=1:boxcolor=black@0.55:boxborderw=18:"
        f"x=(w-text_w)/2:y=h*0.87:enable='gte(t,{cta_st:.2f})'[vout]"
    )

    return ";".join(parts)


def load_manifest(manifest_path: str, legacy_footage: str) -> list:
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        # filter to clips that actually exist on disk
        manifest = [c for c in manifest
                    if os.path.exists(c["file"]) and os.path.getsize(c["file"]) > 0]
        if manifest:
            return manifest
    # Legacy single-clip fallback
    if os.path.exists(legacy_footage):
        dur = ffprobe_duration(legacy_footage)
        return [{"file": legacy_footage, "type": "video", "duration": dur,
                 "source": "legacy", "query": ""}]
    raise SystemExit("No footage manifest and no legacy footage found.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest",    default="build/footage_manifest.json")
    ap.add_argument("--footage",     default="build/footage.mp4",
                    help="Legacy single-clip fallback if no manifest.")
    ap.add_argument("--audio",       default="build/voice.wav")
    ap.add_argument("--captions",    default="build/captions.srt")
    ap.add_argument("--script",      default="build/script.json")
    ap.add_argument("--config",      default="config/sources.json")
    ap.add_argument("--out",         default="build/final.mp4")
    ap.add_argument("--music",       default="build/music.mp3")
    ap.add_argument("--music-dir",   default="assets/music")
    ap.add_argument("--music-volume",type=float, default=0.28)
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as f:
        script = json.load(f)
    cfg = {}
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f).get("video", {})

    hook = script.get("hook", script.get("title", "STAY STRONG"))
    cta  = cfg.get("cta_text", "Follow for daily wisdom")

    # Total duration = voice length (clamped to config bounds)
    voice_dur = ffprobe_duration(args.audio)
    min_s = cfg.get("min_seconds", 30)
    max_s = cfg.get("max_seconds", 62)
    total_dur = max(min_s, min(voice_dur + 0.5, max_s))
    print(f"[assemble_video] voice={voice_dur:.1f}s  target={total_dur:.1f}s")

    clips = load_manifest(args.manifest, args.footage)
    print(f"[assemble_video] {len(clips)} clip(s)/photo(s)")

    # Scale clip durations so they sum to total_dur
    raw_sum = sum(c["duration"] for c in clips)
    if raw_sum > 0:
        scale = total_dur / raw_sum
        for c in clips:
            c["duration"] = max(MIN_CLIP := 2.0, c["duration"] * scale)

    # Re-scale once more so sum matches exactly
    actual_sum = sum(c["duration"] for c in clips)
    if actual_sum > 0:
        factor = total_dur / actual_sum
        for c in clips:
            c["duration"] *= factor

    # Music selection
    folder_track = pick_music(args.music_dir)
    music_path   = (folder_track or
                    (args.music if os.path.exists(args.music) else None))
    if music_path:
        print(f"[assemble_video] music: {music_path}")

    # Build ffmpeg command
    cmd = ["ffmpeg", "-y"]
    for clip in clips:
        if clip["type"] == "photo":
            cmd += ["-loop", "1", "-framerate", "30",
                    "-t", str(clip["duration"] + 2), "-i", clip["file"]]
        else:
            cmd += ["-stream_loop", "-1",
                    "-t", str(clip["duration"] + 2), "-i", clip["file"]]
    cmd += ["-i", args.audio]
    if music_path:
        cmd += ["-stream_loop", "-1", "-i", music_path]

    n = len(clips)
    fc_video = build_filter_complex(clips, hook, cta, args.captions, total_dur)
    fc_audio = build_audio_filter(n, bool(music_path), total_dur, args.music_volume)
    filter_complex = fc_video + ";" + fc_audio

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{total_dur:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart",
        args.out,
    ]

    print("[assemble_video] running ffmpeg...")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print("ERROR: ffmpeg failed.", file=sys.stderr)
        sys.exit(proc.returncode)
    print(f"[assemble_video] wrote {args.out}")


if __name__ == "__main__":
    main()
