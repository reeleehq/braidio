"""braidio.prefetch: concurrent, retried synthesis into the voice's cache (#239)."""

import threading
import time
from pathlib import Path

import braidio
from braidio.structure import prepare_pause
from braidio.weave_config import WeaveConfig


class CachingVoice:
    """A cached voice: remembers every text it has synthesized."""

    name = "caching"

    def __init__(self, *, fail_first=0, delay=0.05):
        self.cache: set[str] = set()
        self.fail_first = fail_first
        self.delay = delay
        self.live = self.peak = 0
        self.lock = threading.Lock()
        self.failed: dict[str, int] = {}

    def narrate(self, text, out_path, *, return_cache_status=False, **kw):
        hit = text in self.cache
        if not hit:
            with self.lock:
                self.live += 1
                self.peak = max(self.peak, self.live)
                n = self.failed.get(text, 0)
                self.failed[text] = n + 1
            try:
                time.sleep(self.delay)
                if n < self.fail_first:
                    raise RuntimeError("downstream_service_error")
            finally:
                with self.lock:
                    self.live -= 1
            self.cache.add(text)
        out = prepare_pause(0.4, Path(out_path).with_suffix(".mp3"))
        return (out, hit) if return_cache_status else out

    def render_dialogue(self, turns, cast, *, out_path, return_cache_status=False, **kw):
        raise NotImplementedError


def _script():
    return braidio.Script(
        title="t", id_slug="t",
        beats=[braidio.Narration(f"Sentence number {i}.") for i in range(6)],
    )


def test_prefetch_is_concurrent_and_makes_the_render_cache_only(tmp_path):
    voice = CachingVoice()
    cfg = WeaveConfig()
    report = braidio.prefetch(_script(), voice=voice, workers=4, config=cfg)
    assert (report.requests, report.fetched, report.cached, report.failed) == (6, 6, 0, {})
    assert voice.peak > 1
    braidio.render_production(
        _script(), source=None, voice=voice, config=cfg, normalize=False,
        out_path=tmp_path / "out.mp3", tts_dir=tmp_path / "tts",
        clips_dir=tmp_path / "clips", episodes_dir=tmp_path / "ep",
    )
    # the render synthesized nothing new: every text was attempted exactly once
    assert set(voice.failed.values()) == {1}


def test_prefetch_retries_transient_failures_and_reports_exhausted_ones():
    flaky = CachingVoice(fail_first=2)
    r = braidio.prefetch(_script(), voice=flaky, workers=3, retries=3, backoff_s=0, config=WeaveConfig())
    assert r.fetched == 6 and not r.failed
    dead = CachingVoice(fail_first=99)
    r = braidio.prefetch(_script(), voice=dead, workers=3, retries=1, backoff_s=0, config=WeaveConfig())
    assert len(r.failed) == 6 and r.fetched == 0


def test_a_second_prefetch_finds_everything_cached():
    voice = CachingVoice()
    braidio.prefetch(_script(), voice=voice, config=WeaveConfig())
    r = braidio.prefetch(_script(), voice=voice, config=WeaveConfig())
    assert (r.cached, r.fetched) == (6, 0)
