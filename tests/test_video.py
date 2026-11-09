"""The video layer: panel planning, caption building, and the optional-dep contract.

The planning and caption functions are pure, so they are tested directly. The
render path needs ``braidio[video]`` and is exercised only far enough to prove the
dependency gate reports honestly — rendering a real film is minutes of frame
generation, which belongs in a manual check, not the suite.
"""

from __future__ import annotations

import builtins
import importlib

import pytest

from braidio import captions_for, cues_for
from braidio.script import Narration, Script, SegmentBeat
from braidio.timeline import build_timeline
from braidio.video import (
    MAX_PANEL_S,
    MIN_PANEL_S,
    Panel,
    Span,
    _split_count,
    assign_stills,
    missing_dependencies,
    plan_spans,
    with_credits,
)


# --- panel planning ---------------------------------------------------------


def test_plan_spans_is_gapless_and_ordered():
    tl = build_timeline(
        kinds=["narration", "clip", "narration"], durations=[24.0, 8.0, 16.0]
    )
    spans = plan_spans(tl)
    assert spans, "a non-empty timeline must yield spans"
    for a, b in zip(spans, spans[1:]):
        assert a.end == pytest.approx(b.start), "spans must not gap or overlap"
        assert a.start < a.end


def test_plan_spans_respects_the_ceiling():
    """No panel outstays MAX_PANEL_S — the bug `round()` introduced (20s -> 2x10s)."""
    tl = build_timeline(kinds=["narration"], durations=[20.0])
    spans = plan_spans(tl)
    assert [round(s.duration, 2) for s in spans] == [6.67, 6.67, 6.67]
    assert all(s.duration <= MAX_PANEL_S + 1e-9 for s in spans)


def test_ceiling_yields_to_the_floor():
    """One slightly-long panel beats a row of flickers."""
    assert _split_count(9.5, MIN_PANEL_S, MAX_PANEL_S) == 1
    assert _split_count(20.0, MIN_PANEL_S, MAX_PANEL_S) == 3
    assert _split_count(4.0, MIN_PANEL_S, MAX_PANEL_S) == 1


def test_short_beat_merges_forward_instead_of_flickering():
    tl = build_timeline(kinds=["narration", "narration"], durations=[2.0, 8.0])
    spans = plan_spans(tl)
    assert len(spans) == 1
    assert spans[0].duration >= MIN_PANEL_S


def test_plan_spans_empty_timeline():
    assert plan_spans(build_timeline(kinds=[], durations=[])) == []


def test_spans_carry_their_beat_identity():
    """A caller choosing pictures needs to know what is being said over each span."""
    tl = build_timeline(
        kinds=["narration", "clip"], durations=[7.0, 7.0], labels=["intro", "the hook"]
    )
    spans = plan_spans(tl)
    assert {s.kind for s in spans} <= {"narration", "clip"}
    assert any(s.label == "the hook" for s in spans)


# --- still assignment -------------------------------------------------------


def test_assign_stills_never_repeats_back_to_back():
    spans = [Span(i * 5.0, (i + 1) * 5.0, i) for i in range(6)]
    panels = assign_stills(spans, ["a.jpg", "b.jpg", "c.jpg"])
    assert [p.still for p in panels] == ["a.jpg", "b.jpg", "c.jpg"] * 2
    for x, y in zip(panels, panels[1:]):
        assert x.still != y.still


def test_assign_stills_alternates_camera_move():
    spans = [Span(0, 5, 0), Span(5, 10, 1)]
    assert [p.style for p in assign_stills(spans, ["a.jpg"])] == ["push", "drift"]


def test_assign_stills_refuses_an_empty_pool():
    with pytest.raises(ValueError, match="at least one still"):
        assign_stills([Span(0, 5, 0)], [])


def test_with_credits_appends_after_the_last_panel():
    panels = [Panel(0.0, 6.0, "a.jpg")]
    out = with_credits(panels, "card.jpg", duration_s=10.0)
    assert len(out) == 2
    assert out[-1].start == 6.0 and out[-1].end == 16.0
    assert out[-1].still == "card.jpg"


# --- captions ---------------------------------------------------------------


def test_captions_split_sentences_within_their_beat():
    script = Script(title="t", id_slug="01", beats=[Narration("One. Two.")])
    tl = build_timeline(kinds=["narration"], durations=[4.0])
    cues = cues_for(script, tl)
    assert [c.text for c in cues] == ["One.", "Two."]
    assert cues[0].start_s == 0.0 and cues[-1].end_s == pytest.approx(4.0)


def test_captions_never_overlap_despite_beat_crossfades():
    """The weave crossfades beats, so raw spans overlap; cues must not."""
    script = Script(
        title="t",
        id_slug="01",
        beats=[Narration("A sentence here."), Narration("Another one.")],
    )
    tl = build_timeline(kinds=["narration", "narration"], durations=[5.0, 5.0])
    assert tl.beats[1].start < tl.beats[0].start + tl.beats[0].duration, (
        "precondition: the weave really does overlap consecutive beats"
    )
    cues = cues_for(script, tl)
    for a, b in zip(cues, cues[1:]):
        assert b.start_s >= a.end_s - 1e-9


def test_segment_beats_are_marked_as_quotes():
    script = Script(
        title="t", id_slug="01", beats=[SegmentBeat("a lyric line", label="hook")]
    )
    tl = build_timeline(kinds=["clip"], durations=[6.0])
    (cue,) = cues_for(script, tl)
    assert "a lyric line" in cue.text
    assert cue.text.startswith("♪")


def test_srt_format_is_well_formed():
    script = Script(title="t", id_slug="01", beats=[Narration("Hello there.")])
    tl = build_timeline(kinds=["narration"], durations=[2.5])
    srt = captions_for(script, tl)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,500\n")


def test_max_chars_wraps_long_sentences():
    long = "word " * 40
    script = Script(title="t", id_slug="01", beats=[Narration(long.strip() + ".")])
    tl = build_timeline(kinds=["narration"], durations=[10.0])
    assert all(len(c.text) <= 42 for c in cues_for(script, tl, max_chars=40))


def test_beats_with_no_span_contribute_no_cue():
    """A beat dropped by a rights profile simply has no timeline entry."""
    script = Script(
        title="t", id_slug="01", beats=[Narration("Kept."), SegmentBeat("dropped")]
    )
    tl = build_timeline(kinds=["narration"], durations=[3.0])  # only beat 0
    assert [c.text for c in cues_for(script, tl)] == ["Kept."]


# --- the optional-dependency contract ---------------------------------------


def test_video_module_imports_without_its_optional_deps(monkeypatch):
    """`import braidio.video` must work with burns/pillow absent — only the render
    path needs them, so the pure planners stay usable on a bare install."""
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.split(".")[0] in {"burns", "PIL"}:
            raise ImportError(f"blocked: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    module = importlib.reload(importlib.import_module("braidio.video"))
    try:
        assert module.missing_dependencies() == ["burns", "pillow"]
        assert module.HAS_VIDEO is False
        # The pure planner still works with nothing installed.
        tl = build_timeline(kinds=["narration"], durations=[12.0])
        assert module.plan_spans(tl)
        with pytest.raises(ImportError, match=r"braidio\[video\]"):
            module.prepare_still("a.jpg", "b.jpg")
    finally:
        monkeypatch.undo()
        importlib.reload(module)


def test_missing_dependencies_reports_distribution_names():
    """Names must be installable: 'pillow', not the 'PIL' import name."""
    assert set(missing_dependencies()) <= {"burns", "pillow"}


def test_render_video_refuses_an_empty_panel_list():
    pytest.importorskip("burns")
    from braidio.video import render_video

    with pytest.raises(ValueError, match="at least one panel"):
        render_video([], audio_path="a.mp3", out_path="o.mp4")


def test_prepare_still_rebuilds_when_the_source_changes(tmp_path):
    """The cache key is the input, not the output path (braidio#213)."""
    pytest.importorskip("PIL")
    from PIL import Image

    from braidio.video import prepare_still

    Image.new("RGB", (40, 20), "red").save(tmp_path / "a.png")
    Image.new("RGB", (40, 20), "blue").save(tmp_path / "b.png")
    dst = tmp_path / "out.jpg"
    red = prepare_still(tmp_path / "a.png", dst, size=(64, 36)).read_bytes()
    blue = prepare_still(tmp_path / "b.png", dst, size=(64, 36)).read_bytes()
    assert blue != red
    # same input again: reused (mtime unchanged), a new size: rebuilt
    mtime = dst.stat().st_mtime_ns
    prepare_still(tmp_path / "b.png", dst, size=(64, 36))
    assert dst.stat().st_mtime_ns == mtime
    assert Image.open(prepare_still(tmp_path / "b.png", dst, size=(32, 18))).size == (
        32,
        18,
    )
    # no sidecar -> unknown origin -> rebuilt; refresh forces it too
    (tmp_path / "out.jpg.src").unlink()
    prepare_still(tmp_path / "b.png", dst, size=(32, 18))
    assert (tmp_path / "out.jpg.src").exists()
    before = dst.stat().st_mtime_ns
    prepare_still(tmp_path / "b.png", dst, size=(32, 18), refresh=True)
    assert dst.stat().st_mtime_ns != before


def test_prepare_still_interrupted_rebuild_leaves_no_stale_claim(tmp_path, monkeypatch):
    """A rebuild that dies after writing dst must not leave the old stamp vouching."""
    pytest.importorskip("PIL")
    import os

    from PIL import Image

    from braidio import video

    Image.new("RGB", (40, 20), "red").save(tmp_path / "a.png")
    Image.new("RGB", (40, 20), "blue").save(tmp_path / "b.png")
    dst = tmp_path / "out.jpg"
    video.prepare_still(tmp_path / "a.png", dst, size=(64, 36))

    real_replace = os.replace

    def replace(a, b):
        if str(b).endswith(".src"):
            raise KeyboardInterrupt
        return real_replace(a, b)

    with monkeypatch.context() as m:
        m.setattr(os, "replace", replace)
        with pytest.raises(KeyboardInterrupt):
            video.prepare_still(tmp_path / "b.png", dst, size=(64, 36))
    blue_dst = dst.read_bytes()
    # red is asked for again: must be rebuilt, not served blue's canvas
    assert (
        video.prepare_still(tmp_path / "a.png", dst, size=(64, 36)).read_bytes()
        != blue_dst
    )
