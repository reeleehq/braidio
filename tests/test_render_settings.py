"""The render record: a breakdown says which settings produced its timings.

Since braidio#63 the pacing knobs are real, so one script has many possible
cuts — and a persisted ``TimelineBreakdown`` carrying only timings is
unattributable: a re-render months later can differ and nothing says why, while
anything keyed to the old timings (panel cues, captions) silently stops
matching. These tests pin the closing of that hole: the settings travel with the
timings, they are plain JSON, and two renders that differ in a pacing knob
record differently.

Offline — ``narrate`` is replaced by a tone writer, so no network, no spend.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import braidio
from braidio.timeline import build_timeline, render_settings
from braidio.weave_config import WeaveConfig

from _audio import needs_ffmpeg, tone

_TEXT = "One thing happened. Then another. A third, briefly. And then the end."


@pytest.fixture
def tone_narration(monkeypatch):
    """``narrate`` → a fixed tone; any real synthesis here would be a bug."""
    import braidio.render as render_mod

    def fake_narrate(text, out_path, **kw):
        return tone(Path(out_path), 440, 0.5)

    monkeypatch.setattr(braidio, "narrate", fake_narrate)


@pytest.fixture
def script():
    return braidio.Script(title="t", id_slug="rec", beats=[braidio.Narration(_TEXT)])


def _render(script, tmp_path, *, config, name):
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


# --- the record itself (pure) ------------------------------------------------


def test_build_timeline_records_nothing_by_default():
    """The field is additive and optional — hand-built timelines still work."""
    tl = build_timeline(kinds=["narration"], durations=[4.0])
    assert tl.settings is None
    assert tl.to_dict()["settings"] is None


def test_record_carries_the_whole_weave_config_not_a_subset():
    cfg = WeaveConfig(segmentation_unit="sentence", gap_turn_s=0.28)
    rec = render_settings(
        config=cfg,
        crossfade_s=0.12,
        clip_edge_overlap_s=0.5,
        target_lufs=-16.0,
        duck_db=-15.0,
        delivery=braidio.V2_TUNED,
        profile=braidio.Profile.PUBLISHED,
    )
    # every knob, so adding one keeps the record correct without an edit here
    assert rec["weave"] == cfg.to_dict()
    for knob in (
        "segmentation_unit",
        "gap_turn_s",
        "speed_jitter",
        "speed_base",
        "min_turn",
        "max_turn",
        "crossfade_s",
    ):
        assert knob in rec["weave"]
    assert rec["delivery"]["name"] == "v2-tuned"
    assert rec["delivery"]["model_id"] == "eleven_multilingual_v2"
    assert rec["profile"] == braidio.Profile.PUBLISHED.value


def test_record_reports_what_was_resolved_not_what_was_passed():
    """No config = no pacing at all; the record must say so rather than echo
    ``WeaveConfig()``'s defaults, which the renderer never consulted."""
    rec = render_settings(
        config=None,
        crossfade_s=0.3,
        clip_edge_overlap_s=0.0,
        target_lufs=-16.0,
        duck_db=-15.0,
    )
    assert rec["weave"] is None
    assert rec["resolved"]["segmentation_unit"] == "beat"
    assert rec["resolved"]["paces_narration"] is False
    assert rec["resolved"]["crossfade_s"] == 0.3  # the argument, not a default


def test_record_is_json_serialisable():
    """Consumers persist the breakdown; a non-JSON value would break them."""
    tl = build_timeline(
        kinds=["narration"],
        durations=[4.0],
        settings=render_settings(
            config=braidio.SOLO_EXPLAINER.weave,
            crossfade_s=0.12,
            clip_edge_overlap_s=0.5,
            target_lufs=-16.0,
            duck_db=-15.0,
            delivery=braidio.SOLO_EXPLAINER.narration_delivery,
            profile=braidio.Profile.PUBLISHED,
        ),
    )
    round_tripped = json.loads(json.dumps(tl.to_dict()))
    assert round_tripped["settings"]["weave"]["gap_turn_s"] > 0


# --- the invariant that makes it worth having --------------------------------


@needs_ffmpeg
def test_two_renders_differing_only_in_pacing_record_differently(
    script, tmp_path, tone_narration
):
    """THE point of the record. Both renders are of one script; only the knobs
    differ. The durations differ (that is braidio#63 working), so the recorded
    settings must differ too — otherwise the two files are indistinguishable
    after the fact."""
    base = braidio.SOLO_EXPLAINER.weave
    _, unpaced = _render(
        script, tmp_path, config=base.with_(segmentation_unit="beat"), name="beat"
    )
    _, paced = _render(
        script, tmp_path, config=base.with_(segmentation_unit="sentence"), name="sent"
    )

    assert unpaced.settings["resolved"]["segmentation_unit"] == "beat"
    assert paced.settings["resolved"]["segmentation_unit"] == "sentence"
    assert unpaced.settings["resolved"]["paces_narration"] is False
    assert paced.settings["resolved"]["paces_narration"] is True
    assert unpaced.settings["weave"] != paced.settings["weave"]
    # the renders really are different cuts — the record is not decoration
    assert unpaced.duration != paced.duration

    _, loose = _render(
        script,
        tmp_path,
        config=base.with_(segmentation_unit="sentence", gap_turn_s=0.5),
        name="loose",
    )
    assert loose.settings["weave"]["gap_turn_s"] == 0.5
    assert paced.settings["weave"]["gap_turn_s"] != 0.5


@needs_ffmpeg
def test_rendered_record_names_the_delivery_and_profile(
    script, tmp_path, tone_narration
):
    _, tl = _render(script, tmp_path, config=WeaveConfig(), name="d")
    # The resolved default, not a hard-coded name: this is exactly the
    # assertion that should follow a defaults change rather than block it.
    assert tl.settings["delivery"]["name"] == braidio.default_delivery().name
    assert tl.settings["profile"] == braidio.DEFAULT_PROFILE.value
    json.dumps(tl.to_dict())  # still JSON on the real render path
