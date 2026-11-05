#!/usr/bin/env python3
"""
Stage 5 (skit format): the edit.

- Hard cuts timed to the dialogue: shot N runs from its line's start to the next line's
  start (build/skit_timeline.json), so the picture changes exactly when a new character
  speaks. AI clips longer than the line are trimmed; shorter ones hold their last frame.
- Everything is conformed to config/skit.json edit.fps (24 by default). AI video models
  render at 24 fps; exporting at 30 by duplicating every 4th frame is what makes many
  AI skits judder.
- Soft vignette + light film grain (the "finished cartoon" look), hook title card for the
  first seconds, word-by-word captions with the active word highlighted, CTA at the end.
- Voice + quiet music bed (assets/music/ track if present, else build/music.mp3).

Output: build/final.mp4

Usage:
    python scripts/assemble_skit.py --niche config/niches/men_psychology.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from assemble_video import FONT_BOLD, balanced_wrap, drawtext_escape, pick_music
from skit_common import load_configs, load_script


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-3000:])
        raise SystemExit(f"ffmpeg failed ({proc.returncode})")


def conform(src: str, dest: str, dur: float, w: int, h: int, fps: int) -> None:
    """Scale/crop to cover the frame, force fps, and make the clip exactly `dur` long."""
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},"
          f"tpad=stop_mode=clone:stop_duration={dur + 1:.3f},trim=duration={dur:.3f},setpts=PTS-STARTPTS,"
          "format=yuv420p")
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-an", "-vf", vf, "-r", str(fps),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", dest])


def iris_filter(total: float, t_in: float = 0.6, t_out: float = 0.8) -> str:
    """Old-cartoon iris: a circle opens on black at the start and closes at the end."""
    R = "hypot(W/2,H/2)"
    rad = (f"if(lt(T,{t_in}),{R}*T/{t_in},if(gt(T,{total - t_out:.3f}),{R}*max(0,{total:.3f}-T)/{t_out},{R}))")
    inside = f"lte(hypot(X-W/2,Y-H/2),{rad})"
    return (f"geq=lum='if({inside},lum(X,Y),0)':cb='if({inside},cb(X,Y),128)':cr='if({inside},cr(X,Y),128)':"
            f"enable='lt(t,{t_in})+gt(t,{total - t_out:.3f})'")


def overlay_filter(title: str, cta: str, captions: str | None, total: float, ecfg: dict) -> str:
    chain = []
    if ecfg.get("vignette", True):
        chain.append("vignette=angle=PI/4.6")
    if ecfg.get("grain", 0):
        chain.append(f"noise=alls={int(ecfg['grain'])}:allf=t")
    title_s = float(ecfg.get("title_seconds", 3.0))
    for li, line in enumerate(balanced_wrap(title, 18)):
        chain.append(
            f"drawtext=fontfile={FONT_BOLD}:text='{drawtext_escape(line)}':fontcolor=white:fontsize=74:"
            "borderw=7:bordercolor=black:shadowcolor=black@0.6:shadowx=4:shadowy=5:"
            f"x=(w-text_w)/2:y=h*0.075+{li * 100}:enable='between(t,0,{title_s:.2f})'"
        )
    if captions:
        subs = captions.replace("\\", "/").replace(":", "\\:")
        chain.append(f"subtitles='{subs}'")
    if ecfg.get("iris"):
        chain.append(iris_filter(total))
    cta_s = float(ecfg.get("cta_seconds", 2.5))
    chain.append(
        f"drawtext=fontfile={FONT_BOLD}:text='{drawtext_escape(cta)}':fontcolor=white:fontsize=48:"
        "box=1:boxcolor=0xDE4630@0.95:boxborderw=22:"
        f"x=(w-text_w)/2:y=h*0.085:enable='gte(t,{max(0.0, total - cta_s):.2f})'"
    )
    return ",".join(chain)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", required=True)
    ap.add_argument("--skit-config", default="config/skit.json")
    ap.add_argument("--script", default="build/script.json")
    ap.add_argument("--timeline", default="build/skit_timeline.json")
    ap.add_argument("--clips", default="build/skit_clips.json")
    ap.add_argument("--audio", default="build/voice.wav")
    ap.add_argument("--captions", default="build/captions.ass")
    ap.add_argument("--music", default="build/music.mp3")
    ap.add_argument("--music-dir", default="assets/music")
    ap.add_argument("--out", default="build/final.mp4")
    args = ap.parse_args()

    niche, skit = load_configs(args.niche, args.skit_config)
    ecfg = skit["edit"]
    w, h, fps = int(ecfg["width"]), int(ecfg["height"]), int(ecfg["fps"])
    data = load_script(args.script)
    timeline = json.loads(Path(args.timeline).read_text(encoding="utf-8"))
    clips = json.loads(Path(args.clips).read_text(encoding="utf-8"))
    total = float(timeline["duration"])

    # Frame-accurate shot lengths: round each cut to the frame grid so the parts add up to `total`.
    cuts = [round(t["shot_start"] * fps) for t in timeline["lines"]] + [round(total * fps)]
    norm_dir = Path("build/norm")
    norm_dir.mkdir(parents=True, exist_ok=True)
    listing = []
    for k, clip in enumerate(clips):
        dur = max(1, cuts[k + 1] - cuts[k]) / fps
        dest = norm_dir / f"shot_{k:02d}.mp4"
        conform(clip["file"], str(dest), dur, w, h, fps)
        listing.append(f"file '{dest.resolve()}'")
    concat_list = norm_dir / "list.txt"
    concat_list.write_text("\n".join(listing) + "\n", encoding="utf-8")
    picture = norm_dir / "picture.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy",
         str(picture)])

    music = pick_music(args.music_dir) or (args.music if os.path.exists(args.music) else None)
    captions = args.captions if os.path.exists(args.captions) else None
    cta = ecfg.get("cta_text") or niche.get("video", {}).get("cta_text", "Follow for more")
    vf = overlay_filter(data["title"], cta, captions, total, ecfg)

    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(picture), "-i", args.audio]
    voice = f"[1:a]highpass=f=70,loudnorm=I=-15:TP=-1.5:LRA=9,apad=whole_dur={total:.3f}"
    if music:
        cmd += ["-stream_loop", "-1", "-i", music]
        vol = float(ecfg.get("music_volume", 0.1))
        # the music ducks under the dialogue and swells back in the pauses (sidechain compression)
        afilter = (f"{voice},asplit=2[va][vkey];[2:a]volume={vol},afade=t=in:st=0:d=1,"
                   f"afade=t=out:st={max(0.0, total - 1.5):.2f}:d=1.5[mu0];"
                   "[mu0][vkey]sidechaincompress=threshold=0.03:ratio=4:attack=40:release=700:makeup=1[mu];"
                   "[va][mu]amix=inputs=2:duration=first:normalize=0[aout]")
    else:
        afilter = voice + "[aout]"
    cmd += ["-filter_complex", f"[0:v]{vf}[vout];{afilter}", "-map", "[vout]", "-map", "[aout]",
            "-t", f"{total:.3f}", "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart",
            args.out]
    print(f"[assemble_skit] {len(clips)} shots, {total:.1f}s at {fps} fps, music={'yes' if music else 'no'}")
    run(cmd)
    print(f"[assemble_skit] wrote {args.out}")


if __name__ == "__main__":
    main()
