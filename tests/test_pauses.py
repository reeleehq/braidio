"""Explicit pauses (SSML breaks) in narration, and the eleven_v4 deliveries (#210).

A ``<break time="6s"/>`` in a narration beat is exact silence on every model:
kept inline where the voice renders it (ElevenLabs v2 family, up to 3 s), cut
into turns everywhere else (v3, v4, any non-ElevenLabs voice). Pause-free beats
plan exactly as before.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import braidio
from braidio.render import plan_beat_turns
from braidio.script import Narration, Script
from braidio.weave import duration_s
from braidio.weave_config import WeaveConfig

from _audio import decode, needs_ffmpeg, rms, tone

BREATH = 'Breathe in.<break time="2s"/> And out.<break time="1s"/>'


def _plan(text, *, config=None, native_break_s=0.0):
    return plan_beat_turns(
        text,
        config=config,
        supports_speed=False,
        settings={},
        seed_offset=0,
        native_break_s=native_break_s,
    )


def test_breaks_cut_an_unpaced_beat_into_turns():
    turns = _plan(BREATH)
    assert [(t.text, t.gap_after_s, t.boundary) for t in turns] == [
        ("Breathe in.", 2.0, "break"),
        ("And out.", 1.0, "break"),  # a trailing break is kept: the author asked
    ]


def test_pause_free_beat_plans_as_before():
    assert _plan("One. Two.") == []
    cfg = WeaveConfig(segmentation_unit="sentence", min_turn=1, max_turn=1)
    assert _plan("One. Two.", config=cfg) == plan_beat_turns(
        "One. Two.", config=cfg, supports_speed=False, settings={}, seed_offset=0
    )


def test_breaks_the_voice_renders_stay_inline():
    (turn,) = _plan('A.<break time="2s"/> B.<break time="8s"/>', native_break_s=3.0)
    assert turn.text == 'A. <break time="2s" /> B.' and turn.gap_after_s == 8.0


def test_paced_sections_end_on_the_break_not_on_the_3s_cap():
    cfg = WeaveConfig(
        segmentation_unit="sentence", min_turn=1, max_turn=1, gap_turn_s=0.3
    )
    turns = _plan('One. Two.<break time="20s"/> Three.', config=cfg)
    assert [(t.text, t.gap_after_s) for t in turns] == [
        ("One.", 0.3),
        ("Two.", 20.0),
        ("Three.", 0.0),
    ]


def test_a_beat_opening_on_a_break_starts_with_silence():
    turns = _plan('<break time="3s"/>Hello.')
    assert [(t.text, t.gap_after_s) for t in turns] == [("", 3.0), ("Hello.", 0.0)]


@pytest.fixture
def tone_narration(monkeypatch):
    calls: list[dict] = []

    def fake_narrate(text, out_path, **kw):
        calls.append({"text": text, **kw})
        return tone(Path(out_path), 440, 0.5)

    monkeypatch.setattr(braidio, "narrate", fake_narrate)
    return calls


def _render(beats, tmp_path, *, delivery, name="p"):
    return braidio.render_production(
        Script(title="t", id_slug=name, beats=beats),
        source=None,
        delivery=delivery,
        out_path=tmp_path / f"{name}.mp3",
        normalize=False,
        end_fade_s=0.0,
        end_silence_s=0.0,
        tts_dir=tmp_path / f"tts-{name}",
        clips_dir=tmp_path / "clips",
    )


@needs_ffmpeg
def test_v4_render_inserts_the_exact_silence(tmp_path, tone_narration):
    out = _render(
        [Narration('<break time="1s"/>In.<break time="4s"/> Out.')],
        tmp_path,
        delivery=braidio.V4_PRESENTER,
    )
    assert [c["text"] for c in tone_narration] == ["In.", "Out."]
    assert {c["model_id"] for c in tone_narration} == {"eleven_v4"}
    assert duration_s(out) == pytest.approx(1.0 + 0.5 + 4.0 + 0.5, abs=0.2)
    pcm = decode(out)
    assert rms(pcm, 0.1, 0.9) < 30 and rms(pcm, 2.0, 5.3) < 30  # the pauses
    assert rms(pcm, 1.1, 1.4) > 300  # the first turn, after the opening second


@needs_ffmpeg
def test_v2_keeps_a_short_break_inline_in_one_call(tmp_path, tone_narration):
    _render(
        [Narration('In.<break time="2s"/> Out.')], tmp_path, delivery=braidio.V2_TUNED
    )
    assert [c["text"] for c in tone_narration] == ['In. <break time="2s" /> Out.']


@needs_ffmpeg
def test_language_code_reaches_the_synthesis(tmp_path, tone_narration):
    fr = replace(braidio.V4_NARRATOR, language_code="fr")
    _render([Narration("Bonjour.")], tmp_path, delivery=fr)
    assert tone_narration[0]["language_code"] == "fr"
    tone_narration.clear()
    _render([Narration("Hello.")], tmp_path, delivery=braidio.V4_NARRATOR, name="q")
    assert "language_code" not in tone_narration[0]


def test_v4_deliveries_are_registered_tag_capable_and_speedless():
    for name in ("v4-presenter", "v4-narrator"):
        d = braidio.DELIVERIES[name]
        assert d.model_id == "eleven_v4" and d.supports_audio_tags
        assert not d.supports_speed
    assert not braidio.delivery.model_supports_speed("eleven_v4")


def test_language_code_rides_the_weave_config_only_when_set():
    from braidio.transforms import format_config

    fr = replace(braidio.V4_PRESENTER, language_code="fr")
    assert format_config(delivery=fr).to_dict()["language_code"] == "fr"
    assert "language_code" not in format_config(delivery="v4-presenter").to_dict()
