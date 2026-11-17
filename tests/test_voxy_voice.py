"""VoxyVoice: any voxy engine as a render's voice, with directions rendered in its dialect."""

import io
import shutil

import numpy as np
import pytest
from scipy.io import wavfile

import braidio
from braidio.tts import VoxyVoice, register_voxy_voice, unregister_tts

voxy = pytest.importorskip("voxy")
from voxy import Speech, SpeechModel, instruction_dialect, register_dialect  # noqa: E402
from voxy.base import speech_model_factories  # noqa: E402
from voxy.direction import _DIALECTS  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
_RATE = 24000
_CALLS: list = []


class _Instructed(SpeechModel):
    """A fake instruction-dialect engine: a short tone per call, recording (text, voice, options)."""

    name = "fake-instructed"

    def __init__(self, **_):
        super().__init__(device="cpu")

    def synthesize(self, text, voice=None, **kw):
        _CALLS.append((text, voice, kw))
        tone = 0.1 * np.sin(2 * np.pi * 220 * np.arange(int(_RATE * len(text) / 14)) / _RATE)
        buf = io.BytesIO()
        wavfile.write(buf, _RATE, tone.astype(np.float32))
        return Speech(buf.getvalue(), format="wav", backend=self.name, voice=voice, text=text)


@pytest.fixture
def engine(monkeypatch, tmp_path):
    def _refuse(*a, **k):
        raise AssertionError("ElevenLabs was called")

    monkeypatch.setattr(braidio, "narrate", _refuse)
    monkeypatch.setattr(braidio.tts, "text_to_speech", _refuse)
    voxy.register_speech_model("fake-instructed", _Instructed, overwrite=True)
    voxy.clear_speech_models()
    register_dialect("fake-instructed", "*", instruction_dialect("style"))
    register_voxy_voice(
        "vx", backend="fake-instructed", voice="anna", voice_map={"RECORD": "bob"},
        cache=tmp_path / "speech", usd_per_1k_chars=0.03,
    )
    _CALLS.clear()
    yield _CALLS
    unregister_tts("vx")
    _DIALECTS["fake-instructed"].pop(0)
    speech_model_factories.pop("fake-instructed", None)
    voxy.clear_speech_models()


def _script():
    return braidio.Script(
        title="v",
        id_slug="v",
        beats=[
            braidio.Narration("[dryly] We all agree. [beat] [emphatic] Never on how."),
            braidio.Narration("[earnest] The record speaks.", voice="RECORD"),
        ],
    )


def test_directions_reach_the_engine_as_instructions_never_as_text(engine, tmp_path):
    braidio.render_production(
        _script(), source=None, config=braidio.WeaveConfig(tts="vx"), out_path=tmp_path / "a.mp3",
        tts_dir=tmp_path / "tts",
    )
    texts = [t for t, _, _ in engine]
    assert texts and all("[" not in t for t in texts)
    styles = {kw.get("style") for _, _, kw in engine}
    assert any("dry" in s for s in styles if s) and any("emphatic" in s for s in styles if s)
    # the configured voice speaks unmapped lines (an ElevenLabs id is never sent); mapped lines map
    assert {v for _, v, _ in engine} == {"anna", "bob"}
    assert all("model_id" not in kw and "voice_settings" not in kw for _, _, kw in engine)


def test_an_unchanged_render_is_served_from_the_speech_cache(engine, tmp_path):
    for out in ("a.mp3", "b.mp3"):
        braidio.render_production(
            _script(), source=None, config=braidio.WeaveConfig(tts="vx"), out_path=tmp_path / out,
            tts_dir=tmp_path / f"tts-{out}",
        )
        if out == "a.mp3":
            n = len(engine)
    assert len(engine) == n  # the second render synthesized nothing


def test_narrate_reports_cache_status_and_writes_the_engine_format(engine, tmp_path):
    v = VoxyVoice(name="direct", backend="fake-instructed", voice="anna", cache=tmp_path / "c")
    p1, hit1 = v.narrate("[curious] What gives?", tmp_path / "x.mp3", return_cache_status=True,
                         model_id="eleven_v3", voice_settings={"stability": 0.0})
    p2, hit2 = v.narrate("[curious] What gives?", tmp_path / "y.mp3", return_cache_status=True)
    assert (p1.suffix, hit1, hit2) == (".wav", False, True)
    assert p2.read_bytes() == p1.read_bytes()


def test_the_registered_rate_prices_it(engine):
    assert braidio.tts.tts_backend("vx").cost_usd("x" * 1000) == pytest.approx(0.03)


def test_a_broken_take_is_rejected_by_default(engine, tmp_path, monkeypatch):
    """A take far too long for its text (breath, noise) is re-asked, never cached."""
    long = "You aren't gonna need it is good advice about features, and never meant for seams."
    real = _Instructed.synthesize
    calls = []

    def flaky(self, text, voice=None, **kw):
        calls.append(text)
        speech = real(self, text, voice, **kw)
        if len(calls) == 1:  # first take: 30 s of tone
            buf = io.BytesIO()
            wavfile.write(buf, _RATE, np.zeros(_RATE * 30, np.float32))
            speech = Speech(buf.getvalue(), format="wav", backend=self.name, voice=voice, text=text)
        return speech

    monkeypatch.setattr(_Instructed, "synthesize", flaky)
    v = VoxyVoice(name="direct", backend="fake-instructed", voice="anna", cache=tmp_path / "c")
    v.narrate(long, tmp_path / "x.wav")
    assert len(calls) == 2
