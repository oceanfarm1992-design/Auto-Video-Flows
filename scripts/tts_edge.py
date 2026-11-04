#!/usr/bin/env python3
"""
Edge TTS: free Microsoft neural voices (no API key) with exact word timings.

Technique adopted from MoneyPrinterTurbo (MIT, github.com/harry0703/MoneyPrinterTurbo,
app/services/voice.py): stream the voice with WordBoundary events switched on and keep
each word's offset/duration, so captions can be timed exactly without Whisper.

The service is Microsoft's unofficial read-aloud endpoint, so it can rate-limit or change
without notice; callers keep their own fallbacks (Kokoro / espeak).

    words = synth_edge("Hello there.", "build/x.mp3", "en-US-AndrewNeural", rate="+0%")
    # -> [("Hello", 0.1, 0.42), ("there", 0.45, 0.8)]
"""
from __future__ import annotations

import asyncio
import inspect
import os

TICKS_PER_SECOND = 10_000_000          # Edge reports offsets/durations in 100 ns units
TOTAL_TIMEOUT_S = 90                   # one line/narration; Edge normally answers in a few seconds


def boundary_to_word(chunk: dict) -> tuple[str, float, float]:
    start = chunk["offset"] / TICKS_PER_SECOND
    return chunk["text"], start, start + chunk["duration"] / TICKS_PER_SECOND


def synth_edge(text: str, out_path: str, voice: str, rate: str = "+0%", pitch: str = "+0Hz",
               retries: int = 2) -> list[tuple[str, float, float]]:
    """Write `text` spoken by `voice` to out_path (mp3) and return (word, start, end) tuples."""
    import edge_tts

    kwargs = {"rate": rate, "pitch": pitch}
    params = inspect.signature(edge_tts.Communicate).parameters
    if "boundary" in params:                      # edge-tts >= 7 defaults to sentence boundaries
        kwargs["boundary"] = "WordBoundary"
    if "receive_timeout" in params:              # fail fast; the caller has fallbacks
        kwargs["connect_timeout"], kwargs["receive_timeout"] = 10, 30
    proxy = os.environ.get("EDGE_TTS_PROXY")
    if proxy and "proxy" in params:
        kwargs["proxy"] = proxy

    last = None
    for _ in range(retries):
        words: list[tuple[str, float, float]] = []
        try:
            comm = edge_tts.Communicate(text, voice, **kwargs)

            # NOT comm.stream_sync(): when the connection fails, its worker thread dies without
            # signalling the end of the stream and the caller waits forever (edge-tts 7.x).
            # Driving the async stream ourselves makes errors raise, with a hard total timeout.
            async def collect() -> None:
                with open(out_path, "wb") as f:
                    async for chunk in comm.stream():
                        if chunk["type"] == "audio":
                            f.write(chunk["data"])
                        elif chunk["type"] == "WordBoundary":
                            words.append(boundary_to_word(chunk))

            asyncio.run(asyncio.wait_for(collect(), TOTAL_TIMEOUT_S))
            # Edge can end a stream with timing events but no audio: treat that as a failure.
            if os.path.getsize(out_path) > 0:
                return words
            last = RuntimeError("edge-tts returned no audio")
        except Exception as exc:  # noqa: BLE001 - retry, then let the caller fall back
            last = exc
    raise RuntimeError(f"edge-tts failed for voice {voice}: {last}")
