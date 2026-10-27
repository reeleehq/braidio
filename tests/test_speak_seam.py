"""The ``speak=`` seam (braidio#123): a format renders with any TTS backend.

Every ElevenLabs entry point is booby-trapped, so these prove the render never
reaches ElevenLabs, not just that it used the stub; the stub is free and local
(a tone per line), which is the point: a test or CI can render ``DEEP_DIVE``.
"""

import io
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


def test_without_speak_the_settings_record_names_elevenlabs():
    from braidio.timeline import DEFAULT_TTS, render_settings

    rec = render_settings(
        config=None,
        crossfade_s=0.1,
        clip_edge_overlap_s=0.0,
        target_lufs=-16.0,
        duck_db=-15.0,
    )
    assert rec["resolved"]["tts"] == DEFAULT_TTS == "elevenlabs"
