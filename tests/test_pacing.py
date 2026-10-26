"""Intra-beat narration pacing (braidio#64) — the planner and its wiring.

The bug this file exists to prevent: ``WeaveConfig.gap_turn_s`` and
``speed_jitter`` were read only by ``compose_narration``, never by
``render_production`` — which is what every ``Format`` template runs. Changing
them produced a **byte-identical** render, while a shipped skill told users to
set them. So the load-bearing test here is
:func:`test_gap_turn_s_changes_the_rendered_timeline`: it renders the same
script twice, changing only ``gap_turn_s``, and fails if the output does not get
longer. Against the pre-fix renderer it fails.

Offline: ``narrate`` is replaced by a tone writer (``tests/_audio.py``), so
nothing here touches ElevenLabs or the network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import braidio
from braidio.pacing import (
    BOUNDARIES,
    classify_boundary,
    plan_turns,
    planned_gap_total_s,
    split_units,
)
from braidio.script import Narration, Script
from braidio.weave import duration_s
from braidio.weave_config import WeaveConfig

from _audio import needs_ffmpeg, tone

# Three sentences in two paragraphs: enough to have a paragraph boundary, a
# sentence boundary and a final turn with no gap at all.
_TEXT = (
    "The first thing happens here. Then a second, slightly longer thing.\n\n"
    "And a new paragraph starts. It ends."
)


# --- the pure planner --------------------------------------------------------


def test_beat_unit_plans_nothing():
    """``segmentation_unit="beat"`` is the historical single-call path."""
    assert plan_turns(_TEXT, unit="beat") == []
    assert plan_turns("", unit="sentence") == []


def test_unknown_unit_is_refused():
    with pytest.raises(ValueError, match="unit must be one of"):
        plan_turns("A.", unit="phoneme")
    with pytest.raises(ValueError, match="segmentation_unit"):
        WeaveConfig(segmentation_unit="sentances")


def test_split_units_keeps_paragraphs_apart():
    assert split_units(_TEXT, unit="sentence") == [
        ["The first thing happens here.", "Then a second, slightly longer thing."],
        ["And a new paragraph starts.", "It ends."],
    ]
    # clause units cut inside a sentence, at its internal punctuation
    assert split_units("Yes, really. No.", unit="clause") == [
        ["Yes,", "really.", "No."]
    ]
    assert split_units(_TEXT, unit="paragraph") == [
        ["The first thing happens here. Then a second, slightly longer thing."],
        ["And a new paragraph starts. It ends."],
    ]


def test_boundary_classification():
    assert classify_boundary("Done.") == "sentence"
    assert classify_boundary('He said "stop."') == "sentence"
    assert classify_boundary("first,") == "clause"
    assert classify_boundary("and then —") == "flowing"
    assert classify_boundary("well…") == "ellipsis"
    assert classify_boundary("running on") == "none"


def test_gaps_scale_with_boundary_strength():
    """A paragraph break gets a longer pause than a sentence. That is the point."""
    turns = plan_turns(_TEXT, unit="sentence", min_turn=1, max_turn=1, gap_s=0.3)
    by_boundary = {t.boundary: t.gap_after_s for t in turns if t.gap_after_s}
    assert by_boundary["paragraph"] > by_boundary["sentence"] > 0
    assert by_boundary["sentence"] == pytest.approx(0.3)
    # the last turn of the beat never trails a gap — that is the renderer's job
    assert turns[-1].gap_after_s == 0.0
    assert BOUNDARIES["paragraph"].gap_scale > BOUNDARIES["clause"].gap_scale


def test_final_lengthening_is_correlated_with_the_gap():
    """A stronger boundary slows the approach to it, not just the silence after.

    "Pause without lengthening" is the robotic signature the research names.
    """
    for name, b in BOUNDARIES.items():
        stronger = b.gap_scale >= BOUNDARIES["sentence"].gap_scale
        assert (b.speed_scale <= 1.0) if stronger else True, name
    assert BOUNDARIES["paragraph"].speed_scale < BOUNDARIES["clause"].speed_scale


def test_speed_is_jittered_per_turn_and_deterministic():
    kw = dict(
        unit="sentence", min_turn=1, max_turn=1, speed_base=1.0, speed_jitter=0.07
    )
    turns = plan_turns(_TEXT, seed=3, **kw)
    speeds = [t.speed for t in turns]
    assert len(set(speeds)) > 1  # successive turns don't share one metronome
    assert all(0.85 < s < 1.15 for s in speeds)
    assert [t.speed for t in plan_turns(_TEXT, seed=3, **kw)] == speeds  # seeded
    assert [t.speed for t in plan_turns(_TEXT, seed=4, **kw)] != speeds


def test_speedless_model_plans_no_speed():
    """eleven v3 has no speed control — plan none rather than send an undefined one."""
    turns = plan_turns(_TEXT, unit="sentence", speed_base=None, speed_jitter=0.07)
    assert turns and all(t.speed is None for t in turns)


def test_turn_grouping_respects_min_and_max():
    units = sum(len(p) for p in split_units(_TEXT, unit="sentence"))
    turns = plan_turns(_TEXT, unit="sentence", min_turn=2, max_turn=2)
    assert len(turns) < units  # sentences were actually grouped
    assert " ".join(t.text for t in turns).count(".") == units


# --- the config seam ---------------------------------------------------------


def test_default_config_does_not_pace():
    assert WeaveConfig().paces_narration is False
    assert WeaveConfig(segmentation_unit="sentence").paces_narration is True


def test_narration_heavy_formats_ship_paced_and_tag_capable():
    """The acceptance bar: no overrides needed for a solo script to sound right."""
    for fmt in (braidio.SOLO_EXPLAINER, braidio.DOCUMENTARY_VO):
        assert fmt.weave.paces_narration, fmt.id
        assert fmt.weave.gap_turn_s > 0, fmt.id
        # v2 cannot render [audio tags] at all, so a solo script could not use them
        assert fmt.narration_delivery.supports_audio_tags, fmt.id
        assert not fmt.narration_delivery.supports_speed, fmt.id
        assert "[audio tags]" in fmt.scripting or "[audio tag" in fmt.scripting


# --- the wiring (the regression this file is named for) ----------------------


@pytest.fixture
def tone_narration(monkeypatch):
    """``narrate`` → a fixed tone, and a log of every call it received."""
    import braidio.render as render_mod

    calls: list[dict] = []

    def fake_narrate(text, out_path, **kw):
        calls.append({"text": text, "settings": dict(kw.get("voice_settings") or {})})
        return tone(Path(out_path), 440, 0.5)

    monkeypatch.setattr(render_mod, "narrate", fake_narrate)
    return calls


def _render(script, tmp_path, *, config, name, delivery=None):
    return braidio.render_production(
        script,
        source=None,
        config=config,
        delivery=delivery,
        out_path=tmp_path / f"{name}.mp3",
        normalize=False,
        end_fade_s=0.0,
        end_silence_s=0.0,
        tts_dir=tmp_path / f"tts-{name}",
        clips_dir=tmp_path / "clips",
    )


@pytest.fixture
def script():
    return Script(title="t", id_slug="pace", beats=[Narration(_TEXT)])


@needs_ffmpeg
def test_gap_turn_s_changes_the_rendered_timeline(script, tmp_path, tone_narration):
    """THE regression test. Pre-fix, all three renders were byte-identical.

    ``gap_turn_s`` is silence the renderer inserts, so more of it must make the
    production longer. A render that ignores the knob fails here.
    """
    base = braidio.SOLO_EXPLAINER.weave
    tight = _render(script, tmp_path, config=base.with_(gap_turn_s=0.0), name="tight")
    loose = _render(script, tmp_path, config=base.with_(gap_turn_s=0.5), name="loose")

    planned = planned_gap_total_s(
        plan_turns(
            _TEXT,
            unit=base.segmentation_unit,
            min_turn=base.min_turn,
            max_turn=base.max_turn,
            seed=base.voice_seed,
            gap_s=0.5,
        )
    )
    assert planned > 0
    assert duration_s(loose) - duration_s(tight) == pytest.approx(planned, abs=0.15)


@needs_ffmpeg
def test_unpaced_default_still_makes_one_call_per_beat(
    script, tmp_path, tone_narration
):
    """The opt-out is real: at the default unit the beat is one TTS call, and
    ``gap_turn_s`` is (correctly, documentedly) inert."""
    cfg = WeaveConfig()  # segmentation_unit="beat"
    a = _render(script, tmp_path, config=cfg, name="a")
    assert len(tone_narration) == 1
    assert tone_narration[0]["text"] == _TEXT  # the whole beat, uncut

    tone_narration.clear()
    b = _render(script, tmp_path, config=cfg.with_(gap_turn_s=3.0), name="b")
    assert len(tone_narration) == 1
    assert duration_s(a) == pytest.approx(duration_s(b), abs=0.05)


@needs_ffmpeg
def test_paced_beat_synthesizes_each_turn_separately(script, tmp_path, tone_narration):
    cfg = WeaveConfig(
        segmentation_unit="sentence", min_turn=1, max_turn=1, gap_turn_s=0.2
    )
    # V2_TUNED explicitly: the default delivery is eleven_v3, which has no speed
    # control at all, and the last assertion here is *about* per-turn speed.
    # (The speedless case is its own test, just below.)
    _render(script, tmp_path, config=cfg, name="paced", delivery=braidio.V2_TUNED)
    texts = [c["text"] for c in tone_narration]
    assert len(texts) == 4  # four sentences, one TTS call each
    assert " ".join(texts) == _TEXT.replace("\n\n", " ")
    # a speed-capable delivery gets a different speed per turn
    speeds = [c["settings"].get("speed") for c in tone_narration]
    assert len(set(speeds)) > 1


@needs_ffmpeg
def test_speedless_delivery_never_sends_a_speed(script, tmp_path, tone_narration):
    _render(
        script,
        tmp_path,
        config=WeaveConfig(segmentation_unit="sentence", speed_jitter=0.1),
        name="v3",
    )
    # default delivery (v2) does send one; the v3 one must not
    tone_narration.clear()
    braidio.render_production(
        script,
        source=None,
        config=WeaveConfig(segmentation_unit="sentence", speed_jitter=0.1),
        delivery=braidio.V3_PRESENTER,
        out_path=tmp_path / "v3b.mp3",
        normalize=False,
        end_fade_s=0.0,
        end_silence_s=0.0,
        tts_dir=tmp_path / "tts-v3b",
        clips_dir=tmp_path / "clips",
    )
    assert tone_narration
    assert all("speed" not in c["settings"] for c in tone_narration)


@needs_ffmpeg
def test_config_crossfade_is_honoured_on_the_concat_path(
    script, tmp_path, tone_narration
):
    """``WeaveConfig.crossfade_s`` used to be read only by the woven path, while
    the timeline breakdown reported it either way — so a narration-only render
    silently used the function default and the reported offsets were wrong."""
    cfg = WeaveConfig(segmentation_unit="sentence", min_turn=1, max_turn=1)
    script2 = Script(
        title="t", id_slug="pace", beats=[Narration("A."), Narration("B.")]
    )
    tight = _render(script2, tmp_path, config=cfg.with_(crossfade_s=0.0), name="cf0")
    wide = _render(script2, tmp_path, config=cfg.with_(crossfade_s=0.3), name="cf3")
    assert duration_s(tight) - duration_s(wide) == pytest.approx(0.3, abs=0.1)


# --- the graph path paces too (braidio#118) ----------------------------------


@needs_ffmpeg
def test_graph_narration_render_paces_like_the_fast_path(
    script, tmp_path, tone_narration, monkeypatch
):
    """``segmentation_unit`` reaches ``narration_render.tts``: same turns, one
    call each, and a rendered file as long as the fast path's."""
    import nw
    from nw import TransformInputs

    graph_calls: list[str] = []

    def graph_narrate(text, out_path, *, return_cache_status=False, **kw):
        graph_calls.append(text)
        path = tone(Path(out_path), 440, 0.5)
        return (path, False) if return_cache_status else path

    monkeypatch.setattr(braidio, "narrate", graph_narrate)
    cfg = WeaveConfig(
        segmentation_unit="sentence", min_turn=1, max_turn=1, gap_turn_s=0.2
    )
    fast = _render(script, tmp_path, config=cfg, name="fast")

    project = braidio.Project.init(tmp_path / "proj", title="t")
    braidio.transforms.ingest_script(project, script, source=None, config=cfg)
    beat = nw.annotations_at_tier(project.root, "narrative-beats")[0]
    voice = nw.get_transform("beat_to_voice_assignment.default")
    narration = nw.get_transform("narration_render.tts")
    voice.execute(project, *voice.plan(project, TransformInputs(primary=(beat,))))
    plan, skel = narration.plan(project, TransformInputs(primary=(beat,)))
    node = narration.execute(project, plan, skel).annotations[0]

    assert graph_calls == [c["text"] for c in tone_narration]  # the fast path's turns
    assert len(graph_calls) == 4
    assert node.body["duration_s"] == pytest.approx(duration_s(fast), abs=0.15)


def test_unpaced_graph_cache_key_is_unchanged_by_the_pacing_plan():
    from braidio.transforms._narration import _narration_cache_key

    class _T:
        name = "narration_render.tts"
        impl_version = 1

    plain = _narration_cache_key(_T, "t", "v", "m", {})
    assert plain == _narration_cache_key(_T, "t", "v", "m", {}, ())
    paced = plan_turns("One. Two.", unit="sentence", min_turn=1, max_turn=1)
    assert plain != _narration_cache_key(_T, "t", "v", "m", {}, paced)
