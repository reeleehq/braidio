"""Spoken spans inside a narration beat (braidio#232).

A paced render synthesizes a narration beat turn by turn, so it *knows* when each
turn is spoken. ``TimelineBreakdown`` used to drop that, leaving a caller to
re-measure the finished audio, and ``captions_for`` to share a beat's time out by
text length.
"""

from __future__ import annotations

import pytest

import braidio
from braidio.captions import captions_for, cues_for
from braidio.script import Narration, Script
from braidio.timeline import BeatSpan, TimelineBreakdown, TurnSpan, build_timeline
from braidio.weave_config import WeaveConfig

from _audio import needs_ffmpeg
from test_pacing import _render, tone_narration  # noqa: F401  (fixture)

_TEXT = "One short sentence. Another one follows it.\n\nThen a new paragraph. The end."
_PACED = WeaveConfig(
    segmentation_unit="sentence", min_turn=1, max_turn=1, gap_turn_s=0.3
)


def _timeline(tmp_path, *, config, lead_gap_s=0.0):
    script = Script(
        title="t", id_slug="ts", beats=[Narration(_TEXT, lead_gap_s=lead_gap_s)]
    )
    out = braidio.render_production(
        script,
        source=None,
        config=config,
        out_path=tmp_path / "o.mp3",
        normalize=False,
        end_fade_s=0.0,
        end_silence_s=0.0,
        tts_dir=tmp_path / "tts",
        clips_dir=tmp_path / "clips",
        return_timeline=True,
    )
    return script, out[1]


@needs_ffmpeg
def test_paced_beat_records_when_each_turn_is_spoken(tmp_path, tone_narration):
    script, tl = _timeline(tmp_path, config=_PACED)
    beat = tl.beats[0]
    assert [t.text for t in beat.turns] == [
        "One short sentence.",
        "Another one follows it.",
        "Then a new paragraph.",
        "The end.",
    ]
    # each turn is the 0.5 s tone the fake voice wrote; the planned gap sits between
    for t in beat.turns:
        assert t.end - t.start == pytest.approx(0.5, abs=0.06)
    for a, b in zip(beat.turns, beat.turns[1:]):
        assert b.start - a.end > 0.25  # gap_turn_s=0.3 at least
    assert beat.turns[0].start == pytest.approx(0.0, abs=0.06)
    assert beat.turns[-1].end == pytest.approx(beat.duration, abs=0.1)


@needs_ffmpeg
def test_lead_gap_shifts_the_turns(tmp_path, tone_narration):
    _, plain = _timeline(tmp_path, config=_PACED)
    _, led = _timeline(tmp_path, config=_PACED, lead_gap_s=1.0)
    assert led.beats[0].turns[0].start - plain.beats[0].turns[0].start == pytest.approx(
        1.0, abs=0.1
    )


@needs_ffmpeg
def test_unpaced_beat_records_none(tmp_path, tone_narration):
    _, tl = _timeline(tmp_path, config=WeaveConfig())  # segmentation_unit="beat"
    assert tl.beats[0].turns == ()
    assert "turns" not in tl.to_dict()["beats"][0]  # older bytes unchanged


@needs_ffmpeg
def test_captions_follow_the_measured_turns(tmp_path, tone_narration):
    script, tl = _timeline(tmp_path, config=_PACED)
    cues = cues_for(script, tl)
    beat = tl.beats[0]
    assert [c.text for c in cues] == [t.text for t in beat.turns]
    for c, t in zip(cues, beat.turns):
        assert c.start_s == pytest.approx(beat.start + t.start, abs=0.01)
        assert c.end_s == pytest.approx(beat.start + t.end, abs=0.01)
    assert captions_for(script, tl).count("-->") == 4


def test_turns_round_trip_through_the_persisted_form():
    tl = TimelineBreakdown(
        (
            BeatSpan(
                0,
                "narration",
                duration=3.0,
                turns=(TurnSpan("A.", 0.0, 1.0), TurnSpan("B.", 1.5, 3.0)),
            ),
        )
    )
    assert TimelineBreakdown.from_dict(tl.to_dict()) == tl


def test_build_timeline_accepts_turns():
    tl = build_timeline(
        kinds=["narration"],
        durations=[2.0],
        turns=[(TurnSpan("A.", 0.0, 1.0),)],
    )
    assert tl.beats[0].turns == (TurnSpan("A.", 0.0, 1.0),)
