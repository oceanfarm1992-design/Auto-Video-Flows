"""Offline tests for Edge TTS word timings, caption alignment to the script and AI-clip
validation (techniques adopted from MoneyPrinterTurbo). No network: edge_tts is faked.

Run from the repo root:  python tests/test_edge_captions.py
"""
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

FAILED = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILED.append(name)


# ── fake edge_tts: two words, offsets in 100 ns ticks, optional empty audio ──────────
class FakeCommunicate:
    no_audio = False

    def __init__(self, text, voice, *, rate="+0%", volume="+0%", pitch="+0Hz",
                 boundary="SentenceBoundary", connector=None, proxy=None):
        self.boundary = boundary

    async def stream(self):
        if not FakeCommunicate.no_audio:
            yield {"type": "audio", "data": b"\xff\xf3" * 400}
        if self.boundary == "WordBoundary":
            yield {"type": "WordBoundary", "offset": 1_000_000, "duration": 3_000_000, "text": "Hello"}
            yield {"type": "WordBoundary", "offset": 5_000_000, "duration": 4_000_000, "text": "there"}


sys.modules["edge_tts"] = types.SimpleNamespace(Communicate=FakeCommunicate)

import generate_captions as gc  # noqa: E402
import generate_dialogue_tts as gd  # noqa: E402
import tts_edge  # noqa: E402

check("100 ns ticks -> seconds", tts_edge.boundary_to_word({"offset": 5_000_000, "duration": 2_500_000,
                                                             "text": "x"}) == ("x", 0.5, 0.75))
with tempfile.TemporaryDirectory() as tmp:
    mp3 = os.path.join(tmp, "a.mp3")
    words = tts_edge.synth_edge("Hello there", mp3, "en-US-AndrewNeural")
    check("edge asks for word boundaries and returns timings", words == [("Hello", 0.1, 0.4), ("there", 0.5, 0.9)])
    check("edge writes the audio", os.path.getsize(mp3) > 0)
    FakeCommunicate.no_audio = True
    try:
        tts_edge.synth_edge("Hello there", mp3, "v", retries=2)
        check("edge with timing but no audio is a failure", False)
    except RuntimeError:
        check("edge with timing but no audio is a failure", True)
    FakeCommunicate.no_audio = False

    raw = os.path.join(tmp, "raw.mp3")
    eng, w = gd.synth_line("Hello there", {"voice": {"edge": "en-US-GuyNeural"}}, "calm", raw, ["edge"], {})
    check("dialogue TTS uses the character's edge voice and keeps its timings", eng == "edge" and len(w) == 2)

class Broken(FakeCommunicate):
    calls = 0

    async def stream(self):
        Broken.calls += 1
        raise OSError("endpoint unreachable")
        yield  # pragma: no cover


sys.modules["edge_tts"] = types.SimpleNamespace(Communicate=Broken)
with tempfile.TemporaryDirectory() as tmp:
    gd._DEAD_ENGINES.clear()
    char = {"voice": {"edge": "en-US-GuyNeural"}}
    for k in range(3):
        eng, _ = gd.synth_line("Hello there", char, "calm", os.path.join(tmp, f"r{k}.wav"), ["edge", "silent"], {})
    check("unreachable edge is tried once per run, then skipped", eng == "silent" and Broken.calls == 2)
    gd._DEAD_ENGINES.clear()
sys.modules["edge_tts"] = types.SimpleNamespace(Communicate=FakeCommunicate)

check("line words shift onto the video clock",
      gd.line_words("Hello there", [("Hello", 0.1, 0.4), ("there", 0.5, 0.9)], 10.0, 1.0)
      == [["Hello", 10.1, 10.4], ["there", 10.5, 10.9]])
check("line words without timings spread evenly",
      gd.line_words("a b", None, 2.0, 1.0) == [["a", 2.0, 2.5], ["b", 2.5, 3.0]])

# ── alignment to the script ──────────────────────────────────────────────────
heard = [("Theodore", 0, .4), ("has", .4, .6), ("not", .6, .8), ("bought", .8, 1.0), ("a", 1.0, 1.1),
         ("coat", 1.1, 1.4), ("in", 1.4, 1.5), ("9", 1.5, 1.7), ("years", 1.7, 2.0)]
fixed = gc.align_to_script(heard, "Teodor hasn't bought himself a coat in nine years.")
check("misheard words replaced by the script's words",
      [w for w, _, _ in fixed] == ["Teodor", "hasn't", "bought", "himself", "a", "coat", "in", "nine", "years."])
check("aligned timings stay in order and inside the audio",
      all(fixed[i][2] <= fixed[i + 1][1] + 1e-9 for i in range(len(fixed) - 1)) and fixed[-1][2] == 2.0)
check("unrelated transcript is left alone", gc.align_to_script([("pizza", 0, 1)], "Silence is power.")
      == [("pizza", 0, 1)])
check("identical transcript unchanged apart from punctuation",
      [w for w, _, _ in gc.align_to_script([("Be", 0, .2), ("the", .2, .3), ("pause", .3, .6)], "Be the pause.")]
      == ["Be", "the", "pause."])

# ── caption stage uses exact TTS timings first ───────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    script, audio, words, out = (os.path.join(tmp, n) for n in ("s.txt", "v.wav", "w.json", "c.srt"))
    Path(script).write_text("Hello there.", encoding="utf-8")
    Path(audio).write_bytes(b"")
    Path(words).write_text(json.dumps({"exact": True, "words": [["hello", 0.1, 0.4], ["their", 0.5, 0.9]]}))
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    r = subprocess.run([sys.executable, "scripts/generate_captions.py", "--script", script, "--audio", audio,
                        "--words", words, "--out", out], capture_output=True, text=True, env=env)
    ass = Path(out[:-4] + ".ass").read_text(encoding="utf-8") if r.returncode == 0 else ""
    check("captions built from exact TTS timings, spelled like the script",
          "exact TTS word timings" in r.stdout and "THERE." in ass and "0:00:00.10" in ass)

# ── AI clip validation ───────────────────────────────────────────────────────
import animate_shots as an  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    good, bad = os.path.join(tmp, "g.mp4"), os.path.join(tmp, "b.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x568:d=1.5:r=24",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", good], check=True)
    data = Path(good).read_bytes()
    Path(bad).write_bytes(data[: len(data) // 3])
    check("complete clip passes validation", an.valid_video(good))
    check("truncated clip is rejected", not an.valid_video(bad))
    check("missing clip is rejected", not an.valid_video(os.path.join(tmp, "none.mp4")))

sys.exit(1 if FAILED else 0)
