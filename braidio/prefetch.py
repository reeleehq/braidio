"""Synthesize a render's turns concurrently, ahead of the render (braidio#239).

A render speaks its turns one after another. A hosted voice that answers in
seconds per request (``VoxyVoice``) makes a long narration take hours, though
the requests are independent. :func:`prefetch` walks the script **exactly as the
render will** (same segmentation, same arguments: it *is* a render, with a
recording voice and silent stand-in audio), collects the distinct requests, and
synthesizes them with bounded concurrency and per-request retries. The voice's
own cache is keyed on the request, so the real render afterwards is served from
it and is unchanged and deterministic.

Only useful for a voice that caches (``VoxyVoice``); for an uncached voice it
just pays twice. A dialogue beat is prefetched only for a voice that renders it
turn by turn; a one-pass voice (ElevenLabs) is left to the render.

>>> prefetch(script, source=None, config=fmt.weave.with_(tts="seed-tim"), workers=8)  # doctest: +SKIP
"""

from __future__ import annotations

import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from braidio.structure import prepare_pause
from braidio.tts import ElevenLabsVoice, SpeakVoice, VoiceBackend, voice_for

DEFAULT_WORKERS = 8
DEFAULT_RETRIES = 5
DEFAULT_BACKOFF_S = 2.0
_STAND_IN_S = 0.5  # length of the silent audio the recording voice returns


@dataclass(frozen=True)
class PrefetchReport:
    """What :func:`prefetch` did. ``failed`` maps a request's text to its last error;
    those requests are simply made again, sequentially, by the render."""

    requests: int
    fetched: int  # new takes synthesized
    cached: int  # already in the voice's cache
    failed: dict[str, str] = field(default_factory=dict)


class _RecordingVoice:
    """A voice that records each ``narrate`` call and answers with silence."""

    def __init__(self, real: VoiceBackend):
        self.real = real
        self.name = real.name
        self.requests: dict[str, tuple[str, dict]] = {}

    def narrate(self, text, out_path, *, return_cache_status: bool = False, **kw):
        key = json.dumps([text, kw], sort_keys=True, default=repr)
        self.requests.setdefault(key, (text, dict(kw)))
        out = prepare_pause(_STAND_IN_S, Path(out_path).with_suffix(".mp3"))
        return (out, True) if return_cache_status else out

    def render_dialogue(
        self, turns, cast, *, out_path, return_cache_status=False, **kw
    ):
        if isinstance(self.real, ElevenLabsVoice):  # one pass: nothing to prefetch
            out = prepare_pause(_STAND_IN_S, Path(out_path).with_suffix(".mp3"))
            return (out, True) if return_cache_status else out
        return SpeakVoice.render_dialogue(
            self,
            turns,
            cast,
            out_path=out_path,
            return_cache_status=return_cache_status,
            **kw,
        )


def prefetch(
    script,
    *,
    source=None,
    voice: VoiceBackend | None = None,
    workers: int = DEFAULT_WORKERS,
    retries: int = DEFAULT_RETRIES,
    backoff_s: float = DEFAULT_BACKOFF_S,
    sleep: Callable[[float], None] = time.sleep,
    **render_kwargs,
) -> PrefetchReport:
    """Warm the voice's cache for ``script`` with ``workers`` concurrent requests.

    ``render_kwargs`` are the arguments you will give the render
    (:func:`braidio.render_production`: ``config``, ``delivery``, ``cast``,
    ``profile``, ``voice_id``...). The voice is the one the render will pick
    (``voice=``, else ``config.tts``). A failed request is retried ``retries``
    times with ``backoff_s * attempt`` seconds between tries, then reported.
    """
    from braidio.render import render_production

    config = render_kwargs.get("config")
    real = voice_for(getattr(config, "tts", None), voice=voice)
    recorder = _RecordingVoice(real)
    with tempfile.TemporaryDirectory(prefix="braidio-prefetch-") as tmp:
        work = Path(tmp)
        render_production(
            script,
            source=source,
            voice=recorder,
            out_path=work / "dry.mp3",
            tts_dir=work / "tts",
            clips_dir=work / "clips",
            episodes_dir=work / "episodes",
            normalize=False,
            **{k: v for k, v in render_kwargs.items() if k != "out_path"},
        )
        todo = list(recorder.requests.values())

        def fetch(item):
            index, (text, kw) = item
            kw = {k: v for k, v in kw.items() if k != "return_cache_status"}
            out = work / "fetch" / f"{index:05d}.mp3"
            last = None
            for attempt in range(1, retries + 2):
                try:
                    _, hit = real.narrate(text, out, return_cache_status=True, **kw)
                    return text, hit, None
                except Exception as e:  # noqa: BLE001 - a hosted engine's transient 5xx; reported, never swallowed
                    last = f"{type(e).__name__}: {e}"
                    if attempt <= retries:
                        sleep(backoff_s * attempt)
            return text, None, last

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            results = list(pool.map(fetch, enumerate(todo)))
    failed = {t: err for t, hit, err in results if err}
    cached = sum(1 for _, hit, err in results if hit and not err)
    return PrefetchReport(
        requests=len(todo),
        fetched=len(results) - cached - len(failed),
        cached=cached,
        failed=failed,
    )
