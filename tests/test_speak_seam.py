"""The ``speak=`` seam (braidio#123): a format renders with any TTS backend.

Every ElevenLabs entry point is booby-trapped, so these prove the render never
reaches ElevenLabs, not just that it used the stub; the stub is free and local
(a tone per line), which is the point: a test or CI can render ``DEEP_DIVE``.
"""

import io
from pathlib import Path
import shutil

import numpy as np
import pytest
from scipy.io import wavfile

import braidio
from braidio.formats import DEEP_DIVE, SOLO_EXPLAINER

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

_RATE = 24000
_SECONDS_PER_WORD = 0.1


class _ToneSpeaker:
    """A free ``speak``: a tone as long as the line, recording what it was asked."""

    def __init__(self):
        self.calls = []

    def __call__(self, text, *, voice):
        self.calls.append((text, voice))
        n = int(_RATE * _SECONDS_PER_WORD * max(1, len(text.split())))
        tone = 0.1 * np.sin(2 * np.pi * 220 * np.arange(n) / _RATE)
        buf = io.BytesIO()
        wavfile.write(buf, _RATE, tone.astype(np.float32))
        return buf.getvalue(), "wav"


@pytest.fixture
def no_elevenlabs(monkeypatch):
    def _refuse(*a, **k):
        raise AssertionError("ElevenLabs was called")

    import braidio.conversation
    import braidio.tts

    monkeypatch.setattr(braidio.tts, "text_to_speech", _refuse)
    monkeypatch.setattr(braidio.conversation, "text_to_dialogue", _refuse)


def test_deep_dive_renders_narration_and_dialogue_through_speak(
    tmp_path, no_elevenlabs
):
    from braidio.conversation import CHRIS, JESSICA
    from braidio.formats import GEORGE

    script = braidio.Script(
        title="Demo",
        id_slug="demo",
        beats=[
            braidio.Narration(text="A bridge before the hosts."),
            braidio.Dialogue(
                turns=[("host_a", "So what is going on here?"), ("host_b", "Listen.")]
            ),
        ],
    )
    speak = _ToneSpeaker()
    out, timeline = braidio.render_format(
        DEEP_DIVE,
        script,
        source=None,
        out_path=tmp_path / "ep.mp3",
        speak=speak,
        return_timeline=True,
        tts_dir=tmp_path / "tts",
        episodes_dir=tmp_path / "eps",
    )
    # the format's voices reach the backend: the narrator, then the cast, per turn
    assert [voice for _, voice in speak.calls] == [GEORGE, JESSICA, CHRIS]
    assert out.exists() and braidio.duration_s(out) > 1.0
    assert timeline.settings["resolved"]["tts"].endswith("_ToneSpeaker")
    # the turn-by-turn dialogue cleans up its per-turn parts (braidio#137)
    leftovers = [
        d
        for d in (tmp_path / "tts").iterdir()
        if d.is_dir() and not d.name.startswith("_render_")  # the render's own
    ]
    assert leftovers == []


def test_an_explicit_speak_wins_over_the_config_s_backend(tmp_path, no_elevenlabs):
    """``speak=`` beats ``config.tts`` (braidio#137's one resolver)."""
    registered, explicit = _ToneSpeaker(), _ToneSpeaker()
    braidio.tts.register_tts("test-registered", registered)
    try:
        braidio.render_production(
            braidio.Script(
                title="t", id_slug="w", beats=[braidio.Narration(text="Hello.")]
            ),
            source=None,
            config=braidio.WeaveConfig(tts="test-registered"),
            speak=explicit,
            out_path=tmp_path / "ep.mp3",
            tts_dir=tmp_path / "tts",
        )
    finally:
        braidio.tts.unregister_tts("test-registered")
    assert explicit.calls and not registered.calls


def test_a_paced_format_speaks_every_turn_through_speak(tmp_path, no_elevenlabs):
    # SOLO_EXPLAINER paces narration (segmentation_unit != "beat"): one beat
    # becomes several turns, each a separate call to the backend
    script = braidio.Script(
        title="Solo",
        id_slug="solo",
        beats=[
            braidio.Narration(
                text=" ".join(f"Sentence number {k} of eight." for k in range(1, 9))
            )
        ],
    )
    speak = _ToneSpeaker()
    out = braidio.render_format(
        SOLO_EXPLAINER,
        script,
        source=None,
        out_path=tmp_path / "solo.mp3",
        speak=speak,
        tts_dir=tmp_path / "tts",
        episodes_dir=tmp_path / "eps",
    )
    # 1-3 sentences a turn: eight sentences are at least three calls
    assert len(speak.calls) >= 3
    assert " ".join(t for t, _ in speak.calls).startswith("Sentence number 1 ")
    assert out.exists()


def test_audio_tags_are_not_read_aloud_by_another_backend(tmp_path, no_elevenlabs):
    # eleven_v3 markup the formats' authoring guides ask for ([pause], [dryly])
    script = braidio.Script(
        title="Tags",
        id_slug="tags",
        beats=[
            braidio.Narration(text="[pause] Hello [dryly] there [laughs]."),
            braidio.Dialogue(
                turns=[("host_a", "[laughs] Really?"), ("host_b", "Yes.")]
            ),
        ],
    )
    speak = _ToneSpeaker()
    braidio.render_format(
        DEEP_DIVE,
        script,
        source=None,
        out_path=tmp_path / "tags.mp3",
        speak=speak,
        tts_dir=tmp_path / "tts",
        episodes_dir=tmp_path / "eps",
    )
    assert [text for text, _ in speak.calls] == ["Hello there.", "Really?", "Yes."]


def test_without_speak_the_render_records_elevenlabs(tmp_path, monkeypatch):
    import braidio.render as render_mod

    tone = _ToneSpeaker()

    def _narrate(text, out_path, **kw):  # the ElevenLabs call, stubbed
        audio, _ = tone(text, voice=None)
        out = tmp_path / "n.wav"
        out.write_bytes(audio)
        return out

    monkeypatch.setattr(braidio, "narrate", _narrate)
    script = braidio.Script(
        title="Plain", id_slug="plain", beats=[braidio.Narration(text="Plain words.")]
    )
    _, timeline = braidio.render_production(
        script,
        source=None,
        out_path=tmp_path / "plain.mp3",
        return_timeline=True,
        tts_dir=tmp_path / "tts",
        episodes_dir=tmp_path / "eps",
    )
    assert timeline.settings["resolved"]["tts"] == "elevenlabs"


class _OnePassVoice:
    """A whole voice strategy: its own narration and a one-pass dialogue."""

    name = "test-one-pass"

    def __init__(self):
        self.calls = []

    def narrate(self, text, out_path, *, return_cache_status=False, **kw):
        self.calls.append(("narrate", text))
        path = _tone(Path(out_path).with_suffix(".wav"))
        return (path, False) if return_cache_status else path

    def render_dialogue(
        self, turns, cast, *, out_path, return_cache_status=False, **kw
    ):
        self.calls.append(("dialogue", tuple(t for _, t in turns)))
        path = _tone(Path(out_path).with_suffix(".wav"))
        return (path, False) if return_cache_status else path


def _tone(path):
    rate = 44100
    samples = (np.sin(np.linspace(0, 440 * 2 * np.pi, rate // 2)) * 8000).astype(
        np.int16
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, rate, samples)
    return path


def test_a_ready_voice_renders_narration_and_one_pass_dialogue(tmp_path, no_elevenlabs):
    """braidio#199: a Voice strategy is used whole, its dialogue in one pass."""
    voice = _OnePassVoice()
    out, tl = braidio.render_production(
        braidio.Script(
            title="t",
            id_slug="v",
            beats=[
                braidio.Narration(text="Hello."),
                braidio.Dialogue(turns=[("host_a", "Yes?"), ("host_b", "No.")]),
            ],
        ),
        source=None,
        voice=voice,
        out_path=tmp_path / "ep.mp3",
        tts_dir=tmp_path / "tts",
        return_timeline=True,
    )
    assert voice.calls == [("narrate", "Hello."), ("dialogue", ("Yes?", "No."))]
    assert tl.settings["resolved"]["tts"] == "test-one-pass"


def test_voice_for_refuses_what_is_not_a_voice():
    with pytest.raises(TypeError, match="braidio.tts.VoiceBackend"):
        braidio.tts.voice_for(voice=lambda text, *, voice: (b"", "wav"))
    with pytest.raises(TypeError, match="an instance"):
        braidio.tts.voice_for(voice=braidio.tts.ElevenLabsVoice)  # the class
    with pytest.raises(TypeError, match="not both"):
        braidio.tts.voice_for(
            voice=_OnePassVoice(), speak=lambda t, *, voice: (b"", "wav")
        )


def test_replacing_braidio_narrate_reaches_every_elevenlabs_path(
    tmp_path, monkeypatch, no_elevenlabs
):
    """The one replacement point (braidio#199): multivoice and the per-line
    dialogue baseline no longer bind ``narrate`` at import."""
    from braidio.conversation import DEFAULT_CAST, render_turns_sequential
    from braidio.multivoice import POOL_4, render_multivoice

    spoken = []

    def fake(text, out_path, **kw):
        spoken.append(text)
        return _tone(Path(out_path))

    monkeypatch.setattr(braidio, "narrate", fake)
    monkeypatch.setattr("braidio.multivoice.level_part", lambda src, dst, **kw: src)
    render_multivoice(
        ["One.", "Two."], POOL_4, out_path=tmp_path / "mv.wav", work_dir=tmp_path / "mv"
    )
    render_turns_sequential(
        [("A", "Three.")],
        DEFAULT_CAST,
        out_path=tmp_path / "seq.wav",
        work_dir=tmp_path / "seq",
    )
    assert "Three." in spoken and any("One." in s for s in spoken)
