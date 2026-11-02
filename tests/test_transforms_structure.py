"""Structural music through the **graph** path (thorwhalen/braidio#39).

The reelee-shaped route — an ``nw`` project driven by ``braidio.weave_project``
/ the ``commentary_weave`` genre — used to refuse a ``SceneBreak`` and call
``weave_timeline`` with no ``bed``, so a graph user got no sting and no
fade-to-spotlight. These tests drive that route with real ffmpeg over synthetic
audio (``tests/_audio.py``) and assert on the *decoded* PCM.

The characterization tests pin that a production declaring no structure renders
exactly what it rendered before this layer existed. **Know what they do and do
not catch.** Both references are built in *this* tree:

- :func:`test_no_structure_renders_exactly_the_pre_change_weave` compares the
  graph render against a hand-written ``weave_timeline`` call with the
  pre-change arguments. A regression *inside* ``weave_timeline`` moves both
  sides equally and this test stays green.
- :func:`test_no_structure_calls_weave_timeline_exactly_as_before` closes that
  gap from the other side: it pins the transform's **call** — the items and
  every keyword — which no change to the weave's internals can hide.

A literal decoded-PCM hash captured from ``origin/main`` would catch both, but
is not portable: CI renders on three ffmpeg builds (ubuntu apt on two Pythons,
Windows choco) and mp3 output is not stable across them, which is why this
suite (and ``tests/test_structure.py`` before it) compares renders made in the
same run. Pre/post byte-identity against pristine ``origin/main`` was verified
out of tree instead — see thorwhalen/braidio#45.

Nothing here reaches a network or a paid API; ``narrate`` is a silence writer.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import braidio

from _audio import (
    AUDIBLE_RMS,
    SILENT_RMS,
    FixedSource,
    decode,
    needs_ffmpeg,
    rms,
    seconds,
    silence,
    tone,
)

pytestmark = [
    pytest.mark.skipif(not braidio.HAS_NW, reason="nw (and lacing) not available"),
    needs_ffmpeg,
]

# The pre-change episode transform's literal weave defaults — what the
# characterization reference reproduces. Keep in step with
# braidio.transforms._episode's module constants.
PRE_CHANGE_DEFAULTS = {
    "clip_edge_overlap_s": 0.5,
    "crossfade_s": 0.12,
    "target_lufs": -16.0,
    "true_peak_dbtp": -1.0,
    "sample_rate": 44100,
}


@pytest.fixture
def silent_narration(monkeypatch):
    """narrate() → a fixed length of silence, so only the music is audible."""

    def fake_narrate(text, out, *, return_cache_status=False, **kw):
        p = silence(Path(out), 1.0)
        return (p, False) if return_cache_status else p

    monkeypatch.setattr(braidio, "narrate", fake_narrate)


@pytest.fixture
def tone_narration(monkeypatch):
    """narrate() → a fixed length of *tone*.

    Audible talk on either side of a pause, so a render whose only other part
    is that pause still has something to measure.
    """

    def fake_narrate(text, out, *, return_cache_status=False, **kw):
        p = tone(Path(out), 220, 1.0)
        return (p, False) if return_cache_status else p

    monkeypatch.setattr(braidio, "narrate", fake_narrate)


@pytest.fixture
def project(tmp_path):
    return braidio.Project.init(tmp_path / "proj", title="graph structure")


@pytest.fixture
def source(tmp_path):
    """A source resolving every reference to a 2s window of a 440 Hz tone."""
    return FixedSource(tone(tmp_path / "song.wav", 440, 8.0), start_s=1.0, end_s=3.0)


def _members(project, episode) -> list[tuple[str, Path]]:
    """``(timeline kind, audio file)`` per member of ``episode``, in play order."""
    import nw

    from braidio.transforms._common import TIER_NARRATION_RENDER, url_to_path

    index = {str(a.id): a for a in nw.iter_all_annotations(project.root)}
    members = [index[mid] for mid in episode.body["ordered_member_ids"]]
    return [
        (
            "narration" if m.tier == TIER_NARRATION_RENDER else "clip",
            url_to_path(m.body["url"]),
        )
        for m in members
    ]


def _script(*beats):
    return braidio.Script(title="Graph", id_slug="graph", beats=list(beats))


@needs_ffmpeg
def test_no_structure_renders_exactly_the_pre_change_weave(
    project, source, silent_narration, tmp_path
):
    """Characterization: without a declared structure the graph render is still
    one plain ``weave_timeline`` over the members — same decoded PCM, to the
    byte.

    Same-tree reference (see the module docstring): this pins the transform, not
    the weave. ``test_no_structure_calls_weave_timeline_exactly_as_before`` is
    the half that would survive a regression inside ``weave_timeline``.
    """
    script = _script(
        braidio.Narration(text="one"),
        braidio.SegmentBeat(reference="the hook", label="hook"),
        braidio.Narration(text="two"),
    )
    episode = braidio.weave_project(project, script, source=source)
    rendered = braidio.transforms._common.url_to_path(episode.body["url"])

    # The pre-change transform's whole audio behaviour, reproduced verbatim.
    reference = braidio.weave_timeline(
        [
            braidio.TimelineItem(kind=kind, path=str(path), placement="sequential")
            for kind, path in _members(project, episode)
        ],
        tmp_path / "reference.mp3",
        clip_edge_overlap_s=PRE_CHANGE_DEFAULTS["clip_edge_overlap_s"],
        narration_crossfade_s=PRE_CHANGE_DEFAULTS["crossfade_s"],
        target_lufs=PRE_CHANGE_DEFAULTS["target_lufs"],
        true_peak=PRE_CHANGE_DEFAULTS["true_peak_dbtp"],
        sample_rate=PRE_CHANGE_DEFAULTS["sample_rate"],
    )
    assert decode(rendered) == decode(reference)


@needs_ffmpeg
def test_no_structure_calls_weave_timeline_exactly_as_before(
    project, source, silent_narration, monkeypatch, tmp_path
):
    """The other half of the characterization: pin the transform's *call*.

    A hand-built reference render can only prove the two sides agree; if
    ``weave_timeline`` itself regressed, both would move together. This asserts
    the arguments the episode transform hands it for a structure-free
    production — no ``bed``, no spotlit item, no extra part, the pre-change
    keyword values — which nothing inside the weave can mask.
    """
    calls = []
    real_weave = braidio.weave_timeline

    def recording_weave(items, out, **kw):
        calls.append((items, kw))
        return real_weave(items, out, **kw)

    monkeypatch.setattr(braidio, "weave_timeline", recording_weave)
    braidio.weave_project(
        project,
        _script(
            braidio.Narration(text="one"),
            braidio.SegmentBeat(reference="the hook", label="hook"),
            braidio.Narration(text="two"),
        ),
        source=source,
    )

    (items, kw) = calls[-1]
    assert [it.kind for it in items] == ["narration", "clip", "narration"]
    assert all(it.placement == "sequential" for it in items)
    assert not any(it.spotlight for it in items)
    assert kw == {
        "clip_edge_overlap_s": PRE_CHANGE_DEFAULTS["clip_edge_overlap_s"],
        "narration_crossfade_s": PRE_CHANGE_DEFAULTS["crossfade_s"],
        "target_lufs": PRE_CHANGE_DEFAULTS["target_lufs"],
        "true_peak": PRE_CHANGE_DEFAULTS["true_peak_dbtp"],
        "sample_rate": PRE_CHANGE_DEFAULTS["sample_rate"],
        "bed": None,
    }


@needs_ffmpeg
def test_a_declared_structure_without_assets_is_inert(
    project, source, silent_narration, tmp_path
):
    """A format may declare stings + spotlight and still supply no music. That
    declaration must not move a single sample."""
    beats = (
        braidio.Narration(text="one"),
        braidio.SegmentBeat(reference="the hook", label="hook"),
        braidio.Narration(text="two"),
    )
    plain = braidio.weave_project(project, _script(*beats), source=source)
    declared = braidio.weave_project(
        braidio.Project.init(tmp_path / "declared", title="declared"),
        _script(*beats),
        source=source,
        structure=braidio.MusicStructure(scene_marker="sting", spotlight_clips=True),
    )
    assert decode(braidio.transforms._common.url_to_path(plain.body["url"])) == decode(
        braidio.transforms._common.url_to_path(declared.body["url"])
    )


@needs_ffmpeg
def test_scene_break_plays_the_sting_in_the_graph_path(
    project, source, silent_narration, tmp_path
):
    """A reelee-shaped project → a render with an audible sting at the break."""
    sting = braidio.Sting(
        str(tone(tmp_path / "hit.wav", 880, 1.0)),
        gain_db=0.0,
        max_len_s=1.0,
        fade_out_s=0.2,
        gap_after_s=0.0,
    )
    script = _script(
        braidio.Narration(text="one"),
        braidio.SceneBreak(label="act 2"),
        braidio.Narration(text="two"),
    )
    episode = braidio.weave_project(
        project,
        script,
        source=source,
        structure=braidio.MusicStructure(sting=sting),
    )
    pcm = decode(braidio.transforms._common.url_to_path(episode.body["url"]))
    assert seconds(pcm) == pytest.approx(3.0, abs=0.3)
    assert rms(pcm, 0.1, 0.8) < SILENT_RMS  # talk (silent here)
    assert rms(pcm, 1.1, 1.7) > AUDIBLE_RMS  # the sting
    assert rms(pcm, 2.2, 2.9) < SILENT_RMS  # talk again


@needs_ffmpeg
def test_scene_break_without_a_sting_is_a_pause_in_the_graph_path(
    project, source, tone_narration
):
    """No sting asset → the boundary is still heard, as silence. It is never
    silently dropped."""
    script = _script(
        braidio.Narration(text="one"),
        braidio.SceneBreak(),
        braidio.Narration(text="two"),
    )
    episode = braidio.weave_project(
        project, script, source=source, structure=braidio.MusicStructure(pause_s=0.5)
    )
    pcm = decode(braidio.transforms._common.url_to_path(episode.body["url"]))
    assert seconds(pcm) == pytest.approx(2.5, abs=0.3)
    assert rms(pcm, 0.1, 0.8) > AUDIBLE_RMS  # talk
    # The pause runs from the first crossfade to the second; sample its middle.
    assert rms(pcm, 1.05, 1.2) < SILENT_RMS
    assert rms(pcm, 1.7, 2.4) > AUDIBLE_RMS  # talk again


@needs_ffmpeg
def test_spotlight_drops_the_bed_under_the_clip_in_the_graph_path(
    project, source, silent_narration, tmp_path
):
    """Fade-to-spotlight: the graph path now renders a bed at all, and a spotlit
    exhibit opens a gap in it."""
    bed = braidio.MusicBed(
        str(tone(tmp_path / "bed.wav", 110, 12.0)),
        gain_db=0.0,
        lead_in_s=0.0,
        fade_in_s=0.05,
        fade_out_s=0.05,
        spotlight_fade_s=0.05,
    )
    script = _script(
        braidio.Narration(text="one"),
        braidio.SegmentBeat(reference="the hook", label="hook", spotlight=True),
        braidio.Narration(text="two"),
    )
    episode = braidio.weave_project(project, script, source=source, bed=bed)
    pcm = decode(braidio.transforms._common.url_to_path(episode.body["url"]))
    # The clip itself is a 440 Hz tone, so the spotlit window is not silent —
    # what the bed's absence must show is a level DROP against the talk, whose
    # narration is digital silence and therefore bed-only.
    assert rms(pcm, 0.1, 0.8) > AUDIBLE_RMS  # bed under the talk

    plain = braidio.Project.init(tmp_path / "plain", title="no spotlight")
    unspotlit = braidio.weave_project(
        plain,
        _script(
            braidio.Narration(text="one"),
            braidio.SegmentBeat(reference="the hook", label="hook", spotlight=False),
            braidio.Narration(text="two"),
        ),
        source=source,
        bed=bed,
    )
    without = decode(braidio.transforms._common.url_to_path(unspotlit.body["url"]))
    assert without != pcm  # the same production without the spotlight differs
    # ... and specifically: the bed keeps playing where the spotlight opened a gap.
    assert rms(without, 1.3, 1.7) > rms(pcm, 1.3, 1.7)


@needs_ffmpeg
def test_a_format_with_no_structure_declared_is_inert(
    project, source, tone_narration, tmp_path
):
    """``interview_host_removed`` declares ``scene_marker='none'``: a break in it
    is a pause even with a sting asset supplied. The format's decision wins, in
    the graph path as in the fast path."""
    fmt = braidio.FORMATS["interview_host_removed"]
    assert fmt.structure.scene_marker == "none"
    script = _script(
        braidio.Narration(text="one"),
        braidio.SceneBreak(),
        braidio.Narration(text="two"),
    )
    episode = braidio.weave_project(
        project,
        script,
        source=source,
        fmt=fmt,
        structure=replace(
            fmt.structure,
            sting=braidio.Sting(str(tone(tmp_path / "hit.wav", 880, 1.0))),
        ),
    )
    pcm = decode(braidio.transforms._common.url_to_path(episode.body["url"]))
    # A sting would ring here; the format asked for a pause, so this stays quiet.
    assert rms(pcm, 1.15, 1.45) < SILENT_RMS
    assert seconds(pcm) == pytest.approx(2.0 + fmt.structure.pause_s, abs=0.3)
