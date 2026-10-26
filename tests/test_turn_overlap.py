"""Speaker interruption (WeaveConfig.overlap_turn_s, Hamilton#22).

The layout is pure, so most of the contract is pinned on ``layout_placed`` with
durations and speakers alone; one offline render proves the mixer honours the same
offsets (``narrate`` is replaced by a tone writer — no network, no spend).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import braidio
from braidio.weave import duck_regions, layout_placed
from braidio.weave_config import MAX_OVERLAP_TURN_S, TurnOverlap, WeaveConfig

from _audio import needs_ffmpeg, tone

XF = 0.12  # the crossfade the layout is called with


def _layout(speakers, durs=None, *, overlap=None, kinds=None):
    n = len(speakers)
    return layout_placed(
        kinds or ["narration"] * n,
        durs or [2.0] * n,
        ["sequential"] * n,
        clip_edge_overlap_s=0.5,
        narration_crossfade_s=XF,
        speakers=speakers,
        overlap=overlap,
    )


def test_off_by_default_is_the_historical_crossfade_layout():
    on = _layout(["a", "b", "a"], overlap=TurnOverlap(max_s=0.0))
    assert on == _layout(["a", "b", "a"], overlap=None) == [0.0, 1.88, 3.76]


def test_overlap_only_when_the_voice_changes():
    starts = _layout(["a", "a", "b"], overlap=TurnOverlap(max_s=0.3, jitter=0.0))
    assert starts[1] == pytest.approx(2.0 - XF)  # same voice: crossfade only
    assert starts[2] == pytest.approx(starts[1] + 2.0 - 0.3)  # speaker change


def test_jitter_is_seeded_and_bounded():
    ov = TurnOverlap(max_s=0.4, jitter=0.5, seed=3)
    first = _layout(["a", "b", "a", "b"], overlap=ov)
    assert first == _layout(["a", "b", "a", "b"], overlap=ov)  # reproducible
    other = _layout(["a", "b", "a", "b"], overlap=TurnOverlap(0.4, 0.5, 0.0, 4))
    assert first != other  # the seed matters
    gaps = [2.0 - (b - a) for a, b in zip(first, first[1:])]
    assert all(0.2 - 1e-9 <= g <= 0.4 + 1e-9 for g in gaps)  # max_s*(1-jitter)..max_s


def test_overlap_is_capped_by_the_turn_lengths():
    starts = _layout(["a", "b"], [2.0, 0.15], overlap=TurnOverlap(0.4, 0.0))
    assert 2.0 - starts[1] == pytest.approx(0.15)  # next turn only 0.15s long
    starts = _layout(["a", "b"], [0.15, 2.0], overlap=TurnOverlap(0.4, 0.0))
    assert starts[1] >= 0.0 and 0.15 - starts[1] == pytest.approx(0.15)  # prev too


def test_never_more_than_two_voices_at_once():
    # three short turns by alternating voices, overlap larger than the middle turn
    durs = [1.0, 0.2, 1.0]
    starts = _layout(["a", "b", "c"], durs, overlap=TurnOverlap(0.4, 0.0))
    assert starts[2] >= starts[0] + durs[0]  # turn 3 never starts under turn 1


def test_clips_and_speakerless_parts_never_interrupt():
    ov = TurnOverlap(max_s=0.4, jitter=0.0)
    starts = _layout(["a", None, "b"], overlap=ov)
    assert starts == pytest.approx([0.0, 2.0 - XF, 4.0 - 2 * XF])
    clip = _layout(["a", "b"], overlap=ov, kinds=["narration", "clip"])
    assert clip[1] == pytest.approx(2.0 - 0.5)  # the clip edge rule, untouched


def test_duck_regions_are_the_talked_over_tail_of_the_outgoing_voice():
    ov = TurnOverlap(max_s=0.3, jitter=0.0, duck_db=-6.0)
    speakers = ["a", "b", "b"]
    starts = _layout(speakers, overlap=ov)
    regions = duck_regions(starts, [2.0] * 3, ["sequential"] * 3, speakers, ov)
    assert list(regions) == [0]  # only the change of speaker
    lo, hi = regions[0]
    assert (lo, hi) == (pytest.approx(1.7), 2.0)
    off = TurnOverlap(max_s=0.3, jitter=0.0, duck_db=0.0)
    assert duck_regions(starts, [2.0] * 3, ["sequential"] * 3, speakers, off) == {}


def test_config_validates_the_intelligible_range():
    WeaveConfig(overlap_turn_s=MAX_OVERLAP_TURN_S)
    for bad in (
        {"overlap_turn_s": MAX_OVERLAP_TURN_S + 0.01},
        {"overlap_turn_s": -0.1},
        {"overlap_jitter": 1.5},
        {"overlap_duck_db": 3.0},
    ):
        with pytest.raises(ValueError):
            WeaveConfig(**bad)
    cfg = WeaveConfig(overlap_turn_s=0.2, overlap_jitter=0.1, voice_seed=9)
    assert cfg.turn_overlap() == TurnOverlap(max_s=0.2, jitter=0.1, duck_db=0.0, seed=9)


@needs_ffmpeg
def test_render_production_interrupts_on_a_voice_change(monkeypatch, tmp_path):
    import braidio.render as render_mod
    from braidio.weave import duration_s

    monkeypatch.setattr(
        render_mod,
        "narrate",
        lambda text, out_path, **kw: tone(Path(out_path), 440, 1.0),
    )
    script = braidio.Script(
        "t",
        "ovl",
        [
            braidio.Narration("One.", voice="va"),
            braidio.Narration("Two.", voice="vb"),
            braidio.Narration("Three.", voice="vb"),
        ],
    )

    def render(config, name):
        return braidio.render_production(
            script,
            source=None,
            config=config,
            out_path=tmp_path / f"{name}.mp3",
            normalize=False,
            end_fade_s=0.0,
            end_silence_s=0.0,
            return_timeline=True,
            tts_dir=tmp_path / f"tts-{name}",
            clips_dir=tmp_path / "clips",
        )

    base_out, base = render(WeaveConfig(), "base")
    out, tl = render(
        WeaveConfig(overlap_turn_s=0.3, overlap_jitter=0.0, overlap_duck_db=-6.0),
        "ovl",
    )
    base_starts = [s.start for s in base.beats]
    starts = [s.start for s in tl.beats]
    assert starts[1] == pytest.approx(starts[0] + 1.0 - 0.3, abs=1e-3)  # a → b
    assert starts[2] - starts[1] == pytest.approx(1.0 - 0.12, abs=1e-3)  # b → b
    assert starts[1] < base_starts[1]
    assert duration_s(out) < duration_s(base_out) - 0.1  # the mix really is shorter
