"""The picture track on the graph path (commentary-studio plan §3 + §5).

Offline and free: the audio boundaries are stubbed exactly as in
``tests/test_transforms.py``, the frame renderer (``braidio.video.render_video``)
and tituli's ffmpeg overlay are stubbed to write marker files, so no ElevenLabs,
no ffmpeg, no minutes of frames. Proves:

- the episode persists its timeline, and the reconstruction for a row written
  before that field existed equals the renderer's own;
- ``video_panels.plan`` cuts on the persisted timeline, pins each panel to a
  ``MediaRef`` span on the episode audio, mints seeds that survive reordering,
  honours a pick map, refuses an unknown key, and is idempotent per episode;
- ``video_cut.render``'s cache key moves when a panel's move changes and does
  NOT move when only a still's label changes — the two-transform split
  earning itself — and a label edit is served from the cache, not re-rendered;
- ``video_cut.finish`` composites labels through tituli, writes the SRT from
  the beats' own text, and its key DOES move on a label edit.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

import braidio

pytestmark = pytest.mark.skipif(
    not braidio.HAS_NW, reason="nw (and lacing) not available"
)


# --- fixtures (mirroring tests/test_transforms.py) ---------------------------


class _FakeSource:
    def __init__(self, asset_path: Path, *, start_s: float = 1.0, end_s: float = 4.0):
        self._asset_path, self._start_s, self._end_s = asset_path, start_s, end_s

    def resolve(self, reference: str):
        from braidio.sources import ResolvedSegment

        return ResolvedSegment(
            asset_path=self._asset_path,
            start_s=self._start_s,
            end_s=self._end_s,
            score=1.0,
            matched_text=reference,
        )


def _write(out_path, tag: bytes) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(tag)
    return p


@pytest.fixture
def patched_synthesis(monkeypatch):
    def _narrate(text, out, *, return_cache_status=False, **kw):
        p = _write(out, b"TTS")
        return (p, False) if return_cache_status else p

    monkeypatch.setattr(braidio, "narrate", _narrate)
    monkeypatch.setattr(
        braidio, "extract_padded", lambda a, s, e, out, **kw: _write(out, b"CLIP")
    )
    monkeypatch.setattr(
        braidio, "weave_timeline", lambda items, out, **kw: _write(out, b"EPISODE")
    )
    monkeypatch.setattr(
        braidio.structure, "prepare_sting", lambda st, out, **kw: _write(out, b"STING")
    )
    monkeypatch.setattr(
        braidio.structure, "prepare_pause", lambda s, out, **kw: _write(out, b"PAUSE")
    )
    # every rendered part probes as 8 s: long enough that plan_spans splits and
    # merges something, short enough to read
    monkeypatch.setattr(braidio, "duration_s", lambda path: 8.0)


@pytest.fixture
def project(tmp_path):
    return braidio.Project.init(tmp_path / "proj", title="test video")


@pytest.fixture
def episode(project, tmp_path, patched_synthesis):
    """A woven 4-member episode: narration, clip, scene break, narration."""
    song = tmp_path / "song.mp3"
    song.write_bytes(b"SONG")
    script = braidio.Script(
        title="Demo",
        id_slug="demo",
        beats=[
            braidio.Narration(
                text="Opening line about the song. Then a second sentence."
            ),
            braidio.SegmentBeat(reference="the famous hook", label="hook"),
            braidio.SceneBreak(label="act 2"),
            braidio.Narration(text="Closing thought on the hook."),
        ],
    )
    return braidio.weave_project(project, script, source=_FakeSource(song))


def _png(path: Path, size=(64, 48), color=(200, 40, 40)) -> Path:
    PIL = pytest.importorskip("PIL.Image")
    path.parent.mkdir(parents=True, exist_ok=True)
    PIL.new("RGB", size, color).save(path)
    return path


def add_still(project, path: Path, *, key: str, labelled: bool, subject=None, **rights):
    """Write a ``still/v1`` node for ``path`` (the Wave-2 importer's job, inlined)."""
    from lacing import Annotation, Artifact

    from braidio.bodies import STILL_V1, StillBodyV1
    from braidio.transforms._common import TIER_STILL, file_url, node_ref
    from braidio.transforms._ingest import _authored_provenance

    artifact = Artifact.from_path(
        path, kind="image", was_generated_by="test", was_attributed_to="test"
    )
    ann = Annotation(
        id=uuid.uuid4(),
        tier=TIER_STILL,
        reference=node_ref(TIER_STILL),
        body=StillBodyV1(
            key=key,
            artifact_id=artifact.asset_id,
            url=file_url(path),
            labelled=labelled,
            subject=subject,
            license=rights.pop("license", "cc-by-4.0"),
            author=rights.pop("author", "Someone"),
            **rights,
        ).model_dump(mode="json"),
        body_schema_uri=STILL_V1,
        provenance=_authored_provenance(),
    )
    project.graph.add_annotation(ann)
    return ann


@pytest.fixture
def stills(project, tmp_path):
    return [
        add_still(
            project,
            _png(tmp_path / "a.png"),
            key="a",
            labelled=True,
            subject="Alice, 1971",
        ),
        add_still(
            project,
            _png(tmp_path / "b.png", color=(40, 200, 40)),
            key="b",
            labelled=False,
        ),
        add_still(
            project,
            _png(tmp_path / "c.png", color=(40, 40, 200)),
            key="c",
            labelled=True,
            subject="Carol",
        ),
    ]


def _rewrite_in_place(project, ann, *, body: dict):
    """Replace ``ann``'s body under its id — what a real edit does (see
    tests/test_transforms.py for why this goes back through add_annotation)."""
    import nw
    from lacing.time import RationalTime

    updated = ann.model_copy(
        update={
            "body": body,
            "provenance": ann.provenance.model_copy(
                update={"generated_at_time": RationalTime.now()}
            ),
        }
    )
    with nw.open_project_stores(project.root) as stores:
        for store in stores:
            if store.remove(ann.id) is not None:
                break
    project.graph.add_annotation(updated)
    return updated


def _run(name, project, *primary, params=None, **kw):
    import nw
    from nw import TransformInputs

    t = nw.get_transform(name)
    plan, skel = t.plan(project, TransformInputs(primary=tuple(primary)), params=params)
    return t.execute(project, plan, skel, **kw)


def _plan(name, project, *primary, params=None):
    import nw
    from nw import TransformInputs

    return nw.get_transform(name).plan(
        project, TransformInputs(primary=tuple(primary)), params=params
    )


# --- the timeline ---------------------------------------------------------------


def test_episode_persists_its_timeline(project, episode):
    from braidio.timeline import TimelineBreakdown
    from braidio.transforms import episode_timeline

    persisted = episode.body["timeline"]
    assert persisted and len(persisted["beats"]) == 4
    tl = episode_timeline(project, episode)
    assert tl == TimelineBreakdown.from_dict(persisted)
    assert [b.kind for b in tl.beats] == [
        "narration",
        "clip",
        "scene-break",
        "narration",
    ]
    assert tl.beats[1].source_start == 1.0 and tl.beats[1].source_end == 4.0
    assert tl.beats[0].label.startswith("Opening line")
    assert tl.settings["profile"] == "personal"


def test_timeline_reconstruction_equals_the_renderers_own(project, episode):
    """A row written before ``timeline`` existed: reconstructed from
    ``ordered_member_ids`` + member durations through the SAME function the
    render used, so the cut points are identical."""
    from braidio.transforms import episode_timeline

    legacy = episode.model_copy(
        update={"body": {k: v for k, v in episode.body.items() if k != "timeline"}}
    )
    assert "timeline" not in legacy.body
    assert episode_timeline(project, legacy).to_dict() == episode.body["timeline"]


def test_timeline_reconstruction_reproduces_the_renderer_arithmetic_when_files_are_gone(
    project, episode, monkeypatch
):
    """The two traps: a segment's body holds the UNPADDED window (the rendered
    clip is longer, by the pads + floor), and a scene break carries no
    duration at all (its pause comes from the production structure). With the
    rendered files gone the reconstruction reproduces the render arithmetic."""
    from braidio.transforms import episode_timeline
    from braidio.transforms._common import url_to_path

    index = {str(a.id): a for a in __import__("nw").iter_all_annotations(project.root)}
    members = [index[m] for m in episode.body["ordered_member_ids"]]
    url_to_path(members[1].body["url"]).unlink()  # the clip
    (project.root / "data" / "breaks" / f"{members[2].id}.mp3").unlink()
    legacy = episode.model_copy(
        update={"body": {k: v for k, v in episode.body.items() if k != "timeline"}}
    )
    tl = episode_timeline(project, legacy)
    # clip: [1.0, 4.0] padded by the default 0.4 / 0.3 → 3.7 s (over the 2.2 floor)
    assert tl.beats[1].duration == pytest.approx(3.7)
    # scene break with no sting: the structure's default pause
    assert tl.beats[2].duration == pytest.approx(braidio.DEFAULT_STRUCTURE.pause_s)
    # spoken takes: the body's own duration_s
    assert tl.beats[0].duration == pytest.approx(members[0].body["duration_s"])


# --- video_panels.plan --------------------------------------------------------------


def test_panels_plan_cuts_on_the_persisted_timeline(project, episode, stills):
    from braidio.transforms import VIDEO_PANELS_TRANSFORM, episode_timeline
    from braidio.video import plan_spans

    result = _run(VIDEO_PANELS_TRANSFORM, project, episode)
    panels = list(result.annotations)
    spans = plan_spans(episode_timeline(project, episode))
    assert len(panels) == len(spans) > 1
    audio_id = episode.body["artifact_id"]
    for panel, span in zip(panels, spans):
        assert panel.reference.kind == "media" and panel.reference.asset_id == audio_id
        iv = panel.reference.interval
        assert iv.start.to_seconds() == pytest.approx(span.start, abs=1e-3)
        assert iv.end.to_seconds() == pytest.approx(span.end, abs=1e-3)
        assert uuid.UUID(panel.body["still_id"]) in {s.id for s in stills}
        assert episode.id in panel.provenance.was_derived_from
    assert [p.body["order"] for p in panels] == list(range(len(panels)))
    # gapless
    for a, b in zip(panels, panels[1:]):
        assert a.reference.interval.end == b.reference.interval.start


def test_panel_seeds_are_minted_from_the_beat_not_the_ordinal(project, episode, stills):
    from braidio.transforms import VIDEO_PANELS_TRANSFORM, mint_seed

    _plan1 = _plan(VIDEO_PANELS_TRANSFORM, project, episode)[1]
    _plan2 = _plan(VIDEO_PANELS_TRANSFORM, project, episode)[1]
    assert [p.body["seed"] for p in _plan1] == [p.body["seed"] for p in _plan2]
    # the seed is a function of (beat_id, index within beat) — never of `order`
    within: dict = {}
    for p in _plan1:
        k = within.get(p.body["beat_id"], 0)
        within[p.body["beat_id"]] = k + 1
        assert p.body["seed"] == mint_seed(p.body["beat_id"], k)
    assert len({p.body["seed"] for p in _plan1}) == len(_plan1)


def test_panels_plan_honours_picks_and_refuses_an_unknown_key(project, episode, stills):
    from braidio.transforms import VIDEO_PANELS_TRANSFORM, picks_from_panels
    from braidio.transforms._common import graph_index

    first_beat = _plan(VIDEO_PANELS_TRANSFORM, project, episode)[1][0].body["beat_id"]
    skels = _plan(
        VIDEO_PANELS_TRANSFORM, project, episode, params={"picks": {first_beat: ["c"]}}
    )[1]
    by_id = {s.id: s for s in stills}
    for p in skels:
        if p.body["beat_id"] == first_beat:
            assert by_id[uuid.UUID(p.body["still_id"])].body["key"] == "c"
    # and the pick map round-trips out of a track, for carrying choices forward
    assert picks_from_panels(skels, graph_index(project))[first_beat][0] == "c"
    with pytest.raises(ValueError, match="does not hold"):
        _plan(
            VIDEO_PANELS_TRANSFORM,
            project,
            episode,
            params={"picks": {first_beat: ["nope"]}},
        )


def test_panels_plan_is_idempotent_by_value_and_a_track_is_an_identity(
    project, episode, stills
):
    import nw

    from braidio.transforms import (
        VIDEO_PANELS_TRANSFORM,
        panels_for_episode,
        picks_from_panels,
        tracks_for_episode,
    )
    from braidio.transforms._common import graph_index

    first = _run(VIDEO_PANELS_TRANSFORM, project, episode).annotations
    second = _run(VIDEO_PANELS_TRANSFORM, project, episode).annotations
    assert [a.id for a in first] == [a.id for a in second]
    assert len({a.body["track_id"] for a in first}) == 1
    assert len(nw.annotations_at_tier(project.root, "video-panels")) == len(first)

    # a plan that says something else (different picks) is NOT the same track:
    # it is written beside the first, never over it, and becomes the latest
    beat = first[0].body["beat_id"]
    other = (
        "c" if picks_from_panels(first, graph_index(project))[beat][0] != "c" else "b"
    )
    third = _run(
        VIDEO_PANELS_TRANSFORM, project, episode, params={"picks": {beat: [other]}}
    ).annotations
    assert {a.id for a in third}.isdisjoint({a.id for a in first})
    assert len(tracks_for_episode(project, episode.id)) == 2
    latest = panels_for_episode(project, episode.id)
    assert [a.id for a in latest] == [a.id for a in third]
    assert [a.body["order"] for a in latest] == list(range(len(third)))
    # a named track is retrievable whole, and picks read from ONE track
    assert [
        a.id
        for a in panels_for_episode(
            project, episode.id, track_id=first[0].body["track_id"]
        )
    ] == [a.id for a in first]
    assert all(
        len(v) <= 2 for v in picks_from_panels(latest, graph_index(project)).values()
    )

    # force writes a new track even for the same value
    forced = _run(VIDEO_PANELS_TRANSFORM, project, episode, force=True).annotations
    assert {a.id for a in forced}.isdisjoint(
        {a.id for a in first} | {a.id for a in third}
    )
    assert len(tracks_for_episode(project, episode.id)) == 3


# --- video_cut.render ----------------------------------------------------------------


@pytest.fixture
def patched_render(monkeypatch):
    calls: list[dict] = []

    def _render_video(panels, *, audio_path, out_path, size, fps, workdir=None, **kw):
        calls.append({"panels": panels, "size": size, "fps": fps})
        return _write(out_path, b"MOTION" + str(len(calls)).encode())

    monkeypatch.setattr(braidio.video, "render_video", _render_video)
    return calls


@pytest.fixture
def panels(project, episode, stills):
    from braidio.transforms import VIDEO_PANELS_TRANSFORM

    return list(_run(VIDEO_PANELS_TRANSFORM, project, episode).annotations)


def test_render_produces_a_motion_cut(project, episode, stills, panels, patched_render):
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM

    result = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels)
    cut = result.annotations[0]
    assert cut.tier == "video-cuts" and cut.body["stage"] == "motion"
    assert cut.body["audio_artifact_id"] == episode.body["artifact_id"]
    assert cut.body["panel_ids"] == [str(p.id) for p in panels]
    assert cut.body["artifact_id"] and cut.body["url"].endswith("_motion.mp4")
    assert result.artifacts[0].kind == "video"
    assert len(patched_render) == 1
    assert len(patched_render[0]["panels"]) == len(panels)
    # provenance reaches the panels, their stills and the episode
    parents = set(cut.provenance.was_derived_from)
    assert {p.id for p in panels} <= parents and episode.id in parents
    assert {uuid.UUID(p.body["still_id"]) for p in panels} <= parents


def test_render_cache_key_moves_on_a_move_and_not_on_a_label(
    project, episode, stills, panels, patched_render
):
    """The two-transform split earning itself: what reaches a pixel is in the
    motion key; the still's label is not."""
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM
    from braidio.transforms._common import graph_index

    key0 = _plan(VIDEO_CUT_RENDER_TRANSFORM, project, *panels)[1][0].body["cache_key"]

    # edit a still's label (subject) only
    still = graph_index(project)[uuid.UUID(panels[0].body["still_id"])]
    _rewrite_in_place(
        project, still, body={**still.body, "subject": "Someone else entirely"}
    )
    panels = [graph_index(project)[p.id] for p in panels]
    key_after_label = _plan(VIDEO_CUT_RENDER_TRANSFORM, project, *panels)[1][0].body[
        "cache_key"
    ]
    assert key_after_label == key0

    # now change a panel's move
    new_move = "drift_left" if panels[0].body["move"] != "drift_left" else "push_in"
    edited = _rewrite_in_place(
        project, panels[0], body={**panels[0].body, "move": new_move}
    )
    key_after_move = _plan(VIDEO_CUT_RENDER_TRANSFORM, project, edited, *panels[1:])[1][
        0
    ].body["cache_key"]
    assert key_after_move != key0


def test_a_label_edit_is_served_from_the_motion_cache_not_re_rendered(
    project, episode, stills, panels, patched_render
):
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM
    from braidio.transforms._common import graph_index

    first = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    assert len(patched_render) == 1
    still = graph_index(project)[uuid.UUID(panels[0].body["still_id"])]
    _rewrite_in_place(project, still, body={**still.body, "subject": "Renamed"})
    panels = [graph_index(project)[p.id] for p in panels]
    import nw

    stale_before = {
        v.annotation.id for v in nw.stale_verdicts_all(project.root) if v.is_stale
    }
    assert first.id in stale_before  # nw's transitive verdict: the still's digest moved
    second = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    # the plan re-derives to the same key, so the existing cut is RE-VERIFIED
    # under its own id — no render, no twin node, and the verdict clears
    assert len(patched_render) == 1
    assert second.id == first.id
    assert second.body["artifact_id"] == first.body["artifact_id"]
    assert len(nw.annotations_at_tier(project.root, "video-cuts")) == 1
    stale_after = {
        v.annotation.id for v in nw.stale_verdicts_all(project.root) if v.is_stale
    }
    assert first.id not in stale_after
    # and an unchanged re-run is the same node (idempotent)
    third = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    assert third.id == second.id and len(patched_render) == 1


def test_a_move_edit_re_renders(project, episode, stills, panels, patched_render):
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM

    _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels)
    edited = _rewrite_in_place(project, panels[0], body={**panels[0].body, "zoom": 1.4})
    _run(VIDEO_CUT_RENDER_TRANSFORM, project, edited, *panels[1:])
    assert len(patched_render) == 2


def test_a_footage_panel_renders_its_footage_and_keys_the_cache_on_it(
    project, episode, stills, panels, patched_render, tmp_path
):
    """A panel carrying footage hands the render a straight cut from its
    in-point; the footage and the in-point reach a pixel, so they are in the
    motion key — and a still panel's key is what it was before footage existed."""
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM

    key0 = _plan(VIDEO_CUT_RENDER_TRANSFORM, project, *panels)[1][0].body["cache_key"]
    recording = _write(tmp_path / "screen.mp4", b"SCREENCAST")
    footage = {"artifact_id": "not-a-digest", "url": recording.as_uri(), "in_s": 2.5}
    edited = _rewrite_in_place(
        project, panels[0], body={**panels[0].body, "footage": footage}
    )
    key1 = _plan(VIDEO_CUT_RENDER_TRANSFORM, project, edited, *panels[1:])[1][0].body[
        "cache_key"
    ]
    assert key1 != key0
    moved = _rewrite_in_place(
        project, edited, body={**edited.body, "footage": {**footage, "in_s": 3.0}}
    )
    key2 = _plan(VIDEO_CUT_RENDER_TRANSFORM, project, moved, *panels[1:])[1][0].body[
        "cache_key"
    ]
    assert key2 not in (key0, key1)

    # a move edit on a footage panel renders nothing, so it re-keys nothing
    new_move = "drift_left" if moved.body["move"] != "drift_left" else "push_in"
    removed = _rewrite_in_place(
        project, moved, body={**moved.body, "move": new_move, "zoom": 1.9}
    )
    key3 = _plan(VIDEO_CUT_RENDER_TRANSFORM, project, removed, *panels[1:])[1][0].body[
        "cache_key"
    ]
    assert key3 == key2

    _run(VIDEO_CUT_RENDER_TRANSFORM, project, removed, *panels[1:])
    rendered = patched_render[-1]["panels"]
    assert rendered[0].footage == braidio.video.Footage(str(recording), 3.0)
    assert all(p.footage is None for p in rendered[1:])

    from braidio.transforms import panel_footage

    assert panel_footage(project, removed) == braidio.video.Footage(str(recording), 3.0)
    assert panel_footage(project, panels[1]) is None


def test_a_footage_panel_whose_video_is_gone_names_it(
    project, episode, stills, panels, patched_render, tmp_path
):
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM

    gone = (tmp_path / "gone.mp4").as_uri()
    edited = _rewrite_in_place(
        project,
        panels[0],
        body={
            **panels[0].body,
            "footage": {"artifact_id": "x", "url": gone, "in_s": 0},
        },
    )
    with pytest.raises(Exception, match="footage"):
        _run(VIDEO_CUT_RENDER_TRANSFORM, project, edited, *panels[1:])


# --- video_cut.finish -------------------------------------------------------------------


@pytest.fixture
def patched_overlay(monkeypatch):
    pytest.importorskip("tituli")
    import tituli.video

    calls: list[list] = []

    def _overlay(video, overlays, dst, **kw):
        calls.append(list(overlays))
        return _write(dst, Path(video).read_bytes() + b"+TEXT")

    monkeypatch.setattr(tituli.video, "overlay", _overlay)
    return calls


def _add_label_track(project, episode, *, start, end, **body):
    from lacing import Annotation, MediaRef, TimeInterval

    from braidio.bodies import LABEL_TRACK_V1, LabelTrackBodyV1
    from braidio.transforms._common import TIER_LABEL_TRACK
    from braidio.transforms._ingest import _authored_provenance

    ann = Annotation(
        id=uuid.uuid4(),
        tier=TIER_LABEL_TRACK,
        reference=MediaRef(
            asset_id=episode.body["artifact_id"],
            interval=TimeInterval.from_seconds(start, end, rate=1000),
        ),
        body=LabelTrackBodyV1(**body).model_dump(mode="json"),
        body_schema_uri=LABEL_TRACK_V1,
        provenance=_authored_provenance(),
    )
    project.graph.add_annotation(ann)
    return ann


def test_finish_composites_labels_and_writes_captions(
    project, episode, stills, panels, patched_render, patched_overlay
):
    from tituli import Label, UNLABELLED

    from braidio.transforms import (
        VIDEO_CUT_FINISH_TRANSFORM,
        VIDEO_CUT_RENDER_TRANSFORM,
    )

    motion = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    card = _add_label_track(
        project,
        episode,
        start=0.0,
        end=4.0,
        kind="title",
        headline="Demo",
        lines=["a film"],
    )
    result = _run(VIDEO_CUT_FINISH_TRANSFORM, project, motion, params={"label": "v1"})
    cut = result.annotations[0]
    assert cut.body["stage"] == "delivered" and cut.body["label"] == "v1"
    assert cut.body["panel_ids"] == motion.body["panel_ids"]
    assert cut.body["audio_artifact_id"] == motion.body["audio_artifact_id"]
    # the captions sidecar is from the beats' own text, never ASR
    from braidio.transforms._common import url_to_path

    # url_to_path, not a "file://" prefix strip: on Windows the latter leaves
    # "/C:/..." (this test never ran on Windows until CI installed `video`)
    srt = url_to_path(cut.body["url"]).with_suffix(".srt")
    assert cut.body["captions_artifact_id"] and srt.exists()
    assert "Opening line about the song." in srt.read_text()
    # tituli got the card and one label per LABELLED still; the unlabelled
    # still contributed nothing rather than a blank
    (overlays,) = patched_overlay
    payloads = [o.payload for o in overlays]
    assert any(isinstance(p, dict) and p.get("kind") == "title" for p in payloads)
    labels = [p for p in payloads if isinstance(p, Label)]
    assert labels and all(l.text in {"Alice, 1971", "Carol"} for l in labels)
    assert all(p is not UNLABELLED for p in payloads)
    # provenance: the motion cut, the card, the panels and the stills
    parents = set(cut.provenance.was_derived_from)
    assert motion.id in parents and card.id in parents
    assert {p.id for p in panels} <= parents


def test_finish_key_moves_on_a_label_edit(
    project, episode, stills, panels, patched_render, patched_overlay
):
    from braidio.transforms import (
        VIDEO_CUT_FINISH_TRANSFORM,
        VIDEO_CUT_RENDER_TRANSFORM,
    )
    from braidio.transforms._common import graph_index

    motion = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    key0 = _plan(VIDEO_CUT_FINISH_TRANSFORM, project, motion)[1][0].body["cache_key"]
    still = graph_index(project)[uuid.UUID(panels[0].body["still_id"])]
    _rewrite_in_place(project, still, body={**still.body, "subject": "Renamed"})
    key1 = _plan(VIDEO_CUT_FINISH_TRANSFORM, project, motion)[1][0].body["cache_key"]
    assert key1 != key0


def test_finish_refuses_a_credits_roll_over_an_unlicensed_still(
    project, episode, tmp_path, patched_render, patched_overlay
):
    """Credits are a licence obligation: a still with no licence cannot be
    credited, and the plan says so before any frame is touched."""
    from braidio.transforms import (
        VIDEO_CUT_FINISH_TRANSFORM,
        VIDEO_CUT_RENDER_TRANSFORM,
        VIDEO_PANELS_TRANSFORM,
    )

    add_still(project, _png(tmp_path / "u.png"), key="u", labelled=False, license=None)
    panels = list(_run(VIDEO_PANELS_TRANSFORM, project, episode).annotations)
    motion = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    with pytest.raises(ValueError, match="no licence recorded"):
        _plan(VIDEO_CUT_FINISH_TRANSFORM, project, motion, params={"credits_s": 8.0})


# --- the move -------------------------------------------------------------------------


def test_every_move_resolves_to_a_burns_path(tmp_path):
    pytest.importorskip("burns")
    from burns import BurnsPath

    from braidio.bodies import MOVES
    from braidio.transforms import path_for_panel, resolve_move

    image = _png(tmp_path / "m.png", size=(160, 90))
    for i, move in enumerate(MOVES):
        path = resolve_move(move, image=str(image), aspect=16 / 9, zoom=1.2, seed=i)
        assert isinstance(path, BurnsPath) and path.output_aspect == pytest.approx(
            16 / 9
        )
    # the stored path is the other front door, and wins
    stored = resolve_move("push_in", image=str(image), aspect=16 / 9, seed=3).to_dict()
    body = {"move": "hold", "zoom": 1.0, "seed": 0, "path": stored}
    assert path_for_panel(body, image=str(image), aspect=16 / 9) == BurnsPath.from_dict(
        stored
    )
    # a focus box (a RectV1 dump on the panel) is honoured on the pushes — the
    # panel door converts it to the (x, y, w, h) tuple burns takes
    focused = {
        "move": "push_in",
        "zoom": 1.2,
        "seed": 0,
        "focus": {"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3},
    }
    assert isinstance(
        path_for_panel(focused, image=str(image), aspect=16 / 9), BurnsPath
    )
    assert isinstance(
        resolve_move(
            "push_in", image=str(image), aspect=16 / 9, focus=(0.1, 0.1, 0.3, 0.3)
        ),
        BurnsPath,
    )


def test_resolve_move_keeps_burns_raise_default_for_a_cross_aspect_path(tmp_path):
    """Post-hoc review of thorwhalen/braidio#81: burns' canvas-relative refit
    frames a different part of the still once braidio's canvas letterboxes it
    at another aspect, so braidio must NOT refit silently by default. The
    explicit ``"refit"`` is still passed through for a caller whose canvas is
    the still itself."""
    burns = pytest.importorskip("burns")
    from braidio.transforms import resolve_move

    image = _png(tmp_path / "m.png", size=(160, 90))
    landscape = resolve_move("push_in", image=str(image), aspect=16 / 9, seed=3)
    with pytest.raises(burns.MoveError):
        resolve_move(landscape.to_dict(), image=str(image), aspect=9 / 16, seed=3)
    refit = resolve_move(
        landscape.to_dict(),
        image=str(image),
        aspect=9 / 16,
        seed=3,
        on_aspect_mismatch="refit",
    )
    assert refit.output_aspect == pytest.approx(9 / 16)


def test_resolver_identity_prefers_burns_own_version_constant(monkeypatch):
    """thorwhalen/braidio#74 item 1: the motion cache key must key on burns'
    own ``RESOLVER_IMPL_VERSION`` — the identity burns itself commits to
    bumping on any framing-affecting change — rather than a source digest,
    which only catches a change inside the one file it reads."""
    import braidio.transforms._video_cut as vc

    burns = pytest.importorskip("burns")
    monkeypatch.setattr(burns, "RESOLVER_IMPL_VERSION", "sentinel-7", raising=False)
    # braidio's own share of the framing (content_box, burns#21) rides along
    assert vc.resolver_identity() == "burns.moves@sentinel-7/content_box@1"


def test_a_near_miss_aspect_fails_the_plan_not_the_render(
    project, episode, stills, panels
):
    """Second review of the #81 follow-up: the plan-time check must be as strict
    as burns' own, or a path authored at 1366x768 (0.00087 off 16:9) passes
    planning and raises MoveError from execute after the canvases."""
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM

    near = {
        "version": 1,
        "keyframes": [
            {"t": 0.0, "rect": {"x": 0, "y": 0, "w": 1, "h": 1}},
            {"t": 1.0, "rect": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}},
        ],
        "interp": "linear",
        "easing": "ease-in-out",
        "output_aspect": 1366 / 768,
    }
    edited = _rewrite_in_place(
        project, panels[0], body={**panels[0].body, "path": near}
    )
    with pytest.raises(ValueError, match="aspect"):
        _plan(VIDEO_CUT_RENDER_TRANSFORM, project, edited, *panels[1:])


def test_resolver_identity_refuses_a_burns_without_the_resolver(monkeypatch):
    """Post-hoc review of #81: ``mixing`` pulls burns with no floor, so an old
    burns is reachable without the ``video`` extra. It must fail the PLAN with
    a clear message, not raise AttributeError from execute after canvases."""
    import braidio.transforms._video_cut as vc

    burns = pytest.importorskip("burns")
    monkeypatch.delattr(burns, "RESOLVER_IMPL_VERSION", raising=False)
    with pytest.raises(RuntimeError, match="burns>=0.0.16"):
        vc.resolver_identity()


def test_resolver_identity_refuses_a_resolver_without_content_box(monkeypatch):
    """burns#21: RESOLVER_IMPL_VERSION 1 has no ``content_box`` and caps every
    zoom at ~1.07 on a photograph; planning against it must refuse."""
    import braidio.transforms._video_cut as vc

    burns = pytest.importorskip("burns")
    monkeypatch.setattr(burns, "RESOLVER_IMPL_VERSION", 1)
    with pytest.raises(RuntimeError, match="RESOLVER_IMPL_VERSION>=2"):
        vc.resolver_identity()


def test_the_four_studio_zooms_are_distinct_on_a_letterboxed_canvas(tmp_path):
    """burns#21 end to end through braidio: a detailed 4:3 still prepared onto
    the 16:9 canvas the render uses. Before, every zoom ended at ~1.07."""
    np = pytest.importorskip("numpy")
    PIL = pytest.importorskip("PIL.Image")
    burns = pytest.importorskip("burns")
    if getattr(burns, "RESOLVER_IMPL_VERSION", 0) < 2:
        pytest.skip("needs burns with content_box")
    from burns.content import axis_maxima

    from braidio.transforms._video_cut import path_for_panel
    from braidio.video import prepare_still

    rng = np.random.default_rng(0)
    tex = rng.integers(0, 255, size=(120, 160, 3), dtype=np.uint8)
    photo = np.kron(tex, np.ones((8, 8, 1), dtype=np.uint8))
    src = tmp_path / "photo.png"
    PIL.fromarray(photo).save(src)
    canvas = prepare_still(src, tmp_path / "canvas.jpg", size=(1920, 1080))
    wmax, _ = axis_maxima(1920, 1080, 16 / 9)
    ends = []
    for zoom in (1.0, 1.08, 1.18, 1.3):
        body = {"move": "push_in", "zoom": zoom, "seed": 0}
        path = path_for_panel(body, image=canvas, aspect=16 / 9, image_size=(1280, 960))
        ends.append(wmax / path.evaluate(1.0).w)
    assert all(b > a + 0.005 for a, b in zip(ends, ends[1:])), ends
    assert ends[-1] == pytest.approx(1.3, abs=0.01), ends


def test_path_for_panel_converts_focus_from_still_to_canvas_coordinates(
    tmp_path, monkeypatch
):
    """thorwhalen/braidio#74 item 4, pinned end to end. ``VideoPanelBodyV1.focus``'s
    own docstring says it is authored "on the picture AS SHOWN — the still
    after its crop". ``prepare_still`` then letterboxes that still onto a
    blurred fill to reach the delivery aspect, so the same normalized rect is
    a different place on the canvas whenever the aspects differ — measured
    upstream as the subject landing fully out of frame at zoom >= 3.5. This
    pins that ``path_for_panel`` actually converts via ``focus_on_canvas``
    when given ``image_size``, by capturing what reaches ``resolve_move``,
    rather than trusting the plumbing (which existed with zero test coverage
    before this)."""
    import braidio.transforms._video_cut as vc

    canvas = _png(tmp_path / "canvas.png", size=(200, 100))  # 2:1 delivery
    still_size = (100, 100)  # the 1:1 still the focus was authored on
    body = {
        "move": "push_in",
        "zoom": 1.2,
        "seed": 0,
        "focus": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
    }
    seen = {}

    def _fake_resolve_move(move, *, image, aspect, zoom, focus, seed, **kw):
        seen["focus"] = focus
        return "sentinel"

    monkeypatch.setattr(vc, "resolve_move", _fake_resolve_move)
    result = vc.path_for_panel(
        body, image=str(canvas), aspect=2.0, image_size=still_size
    )
    assert result == "sentinel"
    expected = vc.focus_on_canvas(
        (0.0, 0.0, 1.0, 1.0), image_size=still_size, canvas_size=(200, 100)
    )
    assert seen["focus"] == pytest.approx(expected)
    # the raw, un-normalized rect the panel authored — the bug this pins
    # against is passing this straight through to a canvas of another aspect
    assert seen["focus"] != (0.0, 0.0, 1.0, 1.0)


def test_finish_refuses_a_motion_cut_whose_frames_would_differ(
    project, episode, stills, panels, patched_render, patched_overlay
):
    """A still swap after the motion was rendered: finish must not composite
    the new still's label over the old still's frames."""
    from braidio.transforms import (
        VIDEO_CUT_FINISH_TRANSFORM,
        VIDEO_CUT_RENDER_TRANSFORM,
    )

    motion = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    other = next(s for s in stills if s.id != uuid.UUID(panels[0].body["still_id"]))
    _rewrite_in_place(
        project, panels[0], body={**panels[0].body, "still_id": str(other.id)}
    )
    with pytest.raises(ValueError, match="out of date"):
        _plan(VIDEO_CUT_FINISH_TRANSFORM, project, motion)


def test_finish_fails_an_overlay_collision_at_plan_time(
    project, episode, stills, panels, patched_render, patched_overlay
):
    from braidio.transforms import (
        VIDEO_CUT_FINISH_TRANSFORM,
        VIDEO_CUT_RENDER_TRANSFORM,
    )

    motion = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    _add_label_track(
        project, episode, start=0.0, end=6.0, kind="context", headline="A", lines=["x"]
    )
    _add_label_track(
        project, episode, start=3.0, end=9.0, kind="context", headline="B", lines=["y"]
    )
    with pytest.raises(ValueError, match="collide"):
        _plan(VIDEO_CUT_FINISH_TRANSFORM, project, motion)
    assert patched_overlay == []  # nothing was rendered


def test_a_fresh_cut_whose_file_is_gone_is_re_rendered(
    project, episode, stills, panels, patched_render
):
    """The fresh-equivalent door, with no edit at all: a node is not a cut."""
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM
    from braidio.transforms._common import url_to_path

    first = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    url_to_path(first.body["url"]).unlink()
    second = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    assert len(patched_render) == 2
    assert url_to_path(second.body["url"]).exists()


def test_a_cached_cut_whose_file_is_gone_is_not_adopted(
    project, episode, stills, panels, patched_render
):
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM
    from braidio.transforms._common import graph_index, url_to_path

    first = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    url_to_path(first.body["url"]).unlink()
    still = graph_index(project)[uuid.UUID(panels[0].body["still_id"])]
    _rewrite_in_place(project, still, body={**still.body, "subject": "Renamed"})
    panels = [graph_index(project)[p.id] for p in panels]
    second = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    assert len(patched_render) == 2  # re-rendered: the product is the file
    assert url_to_path(second.body["url"]).exists()


def test_published_profile_refuses_an_unlicensed_still(
    project, episode, tmp_path, stills, patched_render
):
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM, VIDEO_PANELS_TRANSFORM

    add_still(project, _png(tmp_path / "u.png"), key="u", labelled=False, license=None)
    panels = list(
        _run(VIDEO_PANELS_TRANSFORM, project, episode, force=True).annotations
    )
    _rewrite_in_place(project, episode, body={**episode.body, "profile": "published"})
    with pytest.raises(ValueError, match="published"):
        _plan(VIDEO_CUT_RENDER_TRANSFORM, project, *panels)


def test_an_edited_track_is_still_the_track_a_re_plan_returns(project, episode, stills):
    """Editing a panel's move must not make the next re-plan write a new
    track and hide the edit behind 'the latest'."""
    from braidio.transforms import VIDEO_PANELS_TRANSFORM, panels_for_episode

    first = list(_run(VIDEO_PANELS_TRANSFORM, project, episode).annotations)
    _rewrite_in_place(project, first[0], body={**first[0].body, "move": "drift_left"})
    again = _run(VIDEO_PANELS_TRANSFORM, project, episode).annotations
    assert [a.id for a in again] == [a.id for a in first]
    assert panels_for_episode(project, episode.id)[0].body["move"] == "drift_left"


def test_finish_stamps_and_gates_the_episodes_current_profile(
    project, episode, stills, panels, patched_render, patched_overlay
):
    from braidio.transforms import (
        VIDEO_CUT_FINISH_TRANSFORM,
        VIDEO_CUT_RENDER_TRANSFORM,
    )
    from braidio.transforms._common import graph_index

    motion = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    _rewrite_in_place(project, episode, body={**episode.body, "profile": "published"})
    cut = _run(VIDEO_CUT_FINISH_TRANSFORM, project, motion).annotations[0]
    assert cut.body["profile"] == "published"  # as the episode stands NOW
    still = graph_index(project)[uuid.UUID(panels[0].body["still_id"])]
    _rewrite_in_place(project, still, body={**still.body, "license": None})
    with pytest.raises(ValueError, match="published"):
        _plan(VIDEO_CUT_FINISH_TRANSFORM, project, motion)


def test_a_stored_path_for_another_aspect_fails_the_render_plan(
    project, episode, stills, panels
):
    """A stored path's rectangles are relative to the prepared canvas of the
    delivery it was authored for; converting them to another aspect needs the
    still's size, and these stills record none. Without the conversion burns'
    refit would frame blurred fill (post-hoc review of thorwhalen/braidio#81),
    so refuse at plan time, before any canvas is prepared."""
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM

    four_three = {
        "version": 1,
        "keyframes": [
            {"t": 0.0, "rect": {"x": 0, "y": 0, "w": 1, "h": 1}},
            {"t": 1.0, "rect": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}},
        ],
        "interp": "linear",
        "easing": "ease-in-out",
        "output_aspect": 4 / 3,
    }
    edited = _rewrite_in_place(
        project, panels[0], body={**panels[0].body, "path": four_three}
    )
    with pytest.raises(ValueError, match="aspect"):
        _plan(VIDEO_CUT_RENDER_TRANSFORM, project, edited, *panels[1:])


# --- a stored path re-cut at another delivery aspect (braidio#74 item 3) ----

#: The subject of the refit stills: a red box on white, in still pixels.
_SUBJECT = (50, 40, 90, 80)
_RED = (220, 30, 30)


def _subject_still(path: Path, size=(200, 200), *, subject=_SUBJECT) -> Path:
    PIL = pytest.importorskip("PIL.Image")
    img = PIL.new("RGB", size, (255, 255, 255))
    img.paste(_RED, subject)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def _frame_census(canvas: Path, rect) -> tuple[int, float]:
    """``(red pixels, share of blurred fill)`` in the window ``rect`` frames on
    ``canvas`` — the crop the render scales to the frame. The still is pure
    white + pure red; ``prepare_still``'s fill is that blurred and darkened to
    45%, so nothing on the picture is as dark as the fill."""
    from PIL import Image

    with Image.open(canvas) as img:
        crop = img.convert("RGB").crop(rect.to_pixels(*img.size))
    pixels = list(crop.getdata())
    red = sum(1 for r, g, b in pixels if r > 150 and g < 90 and b < 90)
    fill = sum(1 for px in pixels if max(px) < 140)
    return red, fill / len(pixels)


def _push_on_subject(still_size, canvas_size, *, zoom: float) -> dict:
    """A hand-authored path: whole canvas -> a ``zoom`` push centred on the
    subject, in fractions of the canvas ``prepare_still`` made at
    ``canvas_size`` (the path's ``output_aspect``)."""
    from braidio.transforms import path_on_canvas  # noqa: F401  (exported)
    from braidio.transforms._video_cut import focus_on_canvas

    sw, sh = still_size
    x0, y0, x1, y1 = _SUBJECT
    bx, by, bw, bh = focus_on_canvas(
        (x0 / sw, y0 / sh, (x1 - x0) / sw, (y1 - y0) / sh),
        image_size=still_size,
        canvas_size=canvas_size,
    )
    side = 1.0 / zoom
    cx, cy = bx + bw / 2, by + bh / 2
    return {
        "version": 1,
        "keyframes": [
            {"t": 0.0, "rect": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}},
            {
                "t": 1.0,
                "rect": {"x": cx - side / 2, "y": cy - side / 2, "w": side, "h": side},
            },
        ],
        "interp": "linear",
        "easing": "ease-in-out",
        "output_aspect": canvas_size[0] / canvas_size[1],
    }


def test_a_landscape_push_re_cut_vertical_keeps_the_subject_not_the_fill(tmp_path):
    """braidio#74 item 3, on pixels: a 3x push authored on a landscape cut of a
    square still, re-cut vertical, ends on the subject — whole, and on the
    picture rather than the blurred fill. burns' canvas-relative refit alone
    (what #81 shipped) is measured alongside, so this test fails if the
    conversion is dropped or wrong rather than greening on "it resolves"."""
    pytest.importorskip("burns")
    from braidio.transforms import path_for_panel, resolve_move
    from braidio.video import prepare_still

    still_size, landscape, vertical = (200, 200), (320, 180), (180, 320)
    still = _subject_still(tmp_path / "subject.png", still_size)
    wide = prepare_still(still, tmp_path / "wide.jpg", size=landscape)
    tall = prepare_still(still, tmp_path / "tall.jpg", size=vertical)
    stored = _push_on_subject(still_size, landscape, zoom=3.0)
    body = {"move": "hold", "zoom": 1.0, "seed": 0, "path": stored}

    def end_rect(path):
        return path.keyframes[-1][1]

    def whole_subject(canvas):
        from burns import Rect

        return _frame_census(canvas, Rect(0, 0, 1, 1))[0]

    # the author's view: the whole subject, on the cut it was authored for
    authored = path_for_panel(
        body, image=str(wide), aspect=16 / 9, image_size=still_size
    )
    red, _ = _frame_census(wide, end_rect(authored))
    assert red >= 0.95 * whole_subject(wide)

    # re-cut vertical through the panel door: converted, then refit
    recut = path_for_panel(body, image=str(tall), aspect=9 / 16, image_size=still_size)
    assert recut.output_aspect == pytest.approx(9 / 16)
    # the start framed the whole picture, so it still does (a whole square
    # still at 9:16 is letterboxed, as any zoom-1 frame of it is)
    red, _ = _frame_census(tall, recut.keyframes[0][1])
    assert red >= 0.95 * whole_subject(tall)
    # the push lands on the subject, on the picture
    red, fill = _frame_census(tall, end_rect(recut))
    assert red >= 0.95 * whole_subject(tall)
    assert fill <= 0.02, f"the push ends on {fill:.0%} blurred fill"
    # and it is a push, not a retreat to the whole canvas
    assert end_rect(recut).zoom > 1.5

    # the guard has teeth: burns' refit without the conversion lands elsewhere
    naive = resolve_move(
        stored, image=str(tall), aspect=9 / 16, on_aspect_mismatch="refit"
    )
    red, fill = _frame_census(tall, end_rect(naive))
    assert fill > 0.25 or red < 0.5 * whole_subject(tall)


def _window(canvas_size, still_size, *, center_on_still=None, side: float):
    """A square (canvas-shaped) window of ``side`` on the canvas, centred on a
    point of the still (default: the canvas centre), slid inside the canvas."""
    from braidio.transforms._video_cut import focus_on_canvas

    cx, cy = 0.5, 0.5
    if center_on_still is not None:
        x, y, _, _ = focus_on_canvas(
            (*center_on_still, 0, 0), image_size=still_size, canvas_size=canvas_size
        )
        cx, cy = x, y
    clamp = lambda c: min(max(c - side / 2, 0.0), 1.0 - side)  # noqa: E731
    return {"x": clamp(cx), "y": clamp(cy), "w": side, "h": side}


@pytest.mark.parametrize(
    "still_size, subject, authored, recut, starts",
    [
        # a square still, landscape -> vertical: a whole-canvas start and a 3x push
        ((200, 200), (50, 40, 90, 80), (320, 180), (180, 320), (1.0, 1 / 3)),
        # subject at the still's left edge, vertical -> landscape: the window
        # must slide onto the picture, not keep the author's canvas offset
        ((200, 200), (10, 80, 50, 120), (180, 320), (320, 180), (0.5,)),
        # landscape -> 4:3, a start that shows some fill and a 2x push
        ((200, 200), (50, 40, 90, 80), (320, 180), (240, 180), (0.8, 0.5)),
        # a landscape still, vertical -> landscape
        ((320, 180), (50, 40, 90, 80), (180, 320), (320, 180), (1.0, 0.5)),
        # stills a hair either side of 16:9 (review of #96: a hard
        # "showed fill" threshold flipped the start to ~68% fill here)
        ((356, 200), (150, 80, 190, 120), (320, 180), (180, 320), (1.0, 0.4)),
        ((354, 200), (150, 80, 190, 120), (320, 180), (180, 320), (1.0, 0.4)),
        ((357, 200), (150, 80, 190, 120), (320, 180), (180, 320), (1.0, 0.4)),
    ],
)
def test_a_re_cut_never_shows_less_subject_or_more_fill(
    tmp_path, still_size, subject, authored, recut, starts
):
    """braidio#74 item 3, on pixels, keyframe by keyframe: re-aimed at another
    delivery aspect, a stored path shows at least as much of the subject, and
    no more blurred fill, than the author's own frame did ("fill only where
    the author showed fill"). Where the re-cut shows the whole still across,
    it shows it centred (equal fill either side)."""
    pytest.importorskip("burns")
    from burns import Rect

    from braidio.transforms import path_for_panel
    from braidio.video import prepare_still

    still = _subject_still(tmp_path / "s.png", still_size, subject=subject)
    old = prepare_still(still, tmp_path / "old.jpg", size=authored)
    new = prepare_still(still, tmp_path / "new.jpg", size=recut)
    centre = (  # the subject's centre, in still fractions
        (subject[0] + subject[2]) / 2 / still_size[0],
        (subject[1] + subject[3]) / 2 / still_size[1],
    )
    keyframes = [
        {
            "t": i / max(len(starts) - 1, 1),
            "rect": _window(
                authored,
                still_size,
                center_on_still=None if side >= 0.8 else centre,
                side=side,
            ),
        }
        for i, side in enumerate(starts)
    ]
    stored = {
        "version": 1,
        "keyframes": keyframes,
        "interp": "linear",
        "easing": "ease-in-out",
        "output_aspect": authored[0] / authored[1],
    }
    body = {"move": "hold", "zoom": 1.0, "seed": 0, "path": stored}
    aspect = recut[0] / recut[1]
    path = path_for_panel(body, image=str(new), aspect=aspect, image_size=still_size)
    assert path.output_aspect == pytest.approx(aspect)
    whole_old = _frame_census(old, Rect(0, 0, 1, 1))[0]
    whole_new = _frame_census(new, Rect(0, 0, 1, 1))[0]
    for kf, (t, rect) in zip(keyframes, path.keyframes):
        r = kf["rect"]
        was_red, was_fill = _frame_census(old, Rect(r["x"], r["y"], r["w"], r["h"]))
        red, fill = _frame_census(new, rect)
        assert red / whole_new >= was_red / whole_old - 0.05, f"t={t}: lost subject"
        assert fill <= was_fill + 0.02, f"t={t}: {fill:.0%} fill, author {was_fill:.0%}"
        left, right = _side_fill(new, rect)
        if left or right:
            assert abs(left - right) <= 1, f"t={t}: off-centre ({left} vs {right})"


def test_a_re_cut_start_shows_the_rows_of_the_still_the_author_showed(tmp_path):
    """A start that showed fill beside the still (a 0.8 window on a landscape
    cut of a square still) re-cut 4:3: only the part of it that was picture
    is carried over, so the re-cut shows the same rows of the still — not a
    wider zoom-out that the fill's width would otherwise ask for."""
    pytest.importorskip("burns")
    from burns import Rect

    from braidio.transforms import path_for_panel
    from braidio.video import prepare_still

    still_size, authored, recut = (200, 200), (320, 180), (240, 180)
    still = _subject_still(tmp_path / "s.png", still_size)
    old = prepare_still(still, tmp_path / "old.jpg", size=authored)
    new = prepare_still(still, tmp_path / "new.jpg", size=recut)
    start = _window(authored, still_size, side=0.8)
    stored = {
        "version": 1,
        "keyframes": [{"t": 0.0, "rect": start}, {"t": 1.0, "rect": start}],
        "interp": "linear",
        "easing": "ease-in-out",
        "output_aspect": 16 / 9,
    }
    body = {"move": "hold", "zoom": 1.0, "seed": 0, "path": stored}
    path = path_for_panel(body, image=str(new), aspect=4 / 3, image_size=still_size)

    def picture_rows(canvas, rect) -> float:
        """Share of the still's height the window shows, down its middle."""
        from PIL import Image

        with Image.open(canvas) as img:
            img = img.convert("RGB")
            whole = 200 * min(img.width / 200, img.height / 200)  # still px
            crop = img.crop(rect.to_pixels(*img.size))
        col = [crop.getpixel((crop.width // 2, y)) for y in range(crop.height)]
        return sum(1 for px in col if max(px) >= 140) / whole

    authored_rows = picture_rows(old, Rect(**start))
    assert authored_rows == pytest.approx(0.8, abs=0.02)
    assert picture_rows(new, path.keyframes[0][1]) == pytest.approx(
        authored_rows, abs=0.03
    )


def test_a_push_re_cut_vertical_still_pushes(tmp_path):
    """Review of #96: a wide still re-cut vertical cannot show either keyframe
    of a 2.5x push without trimming it, and capping each to the picture made
    both the same window — a push turned into a hold. The subject must grow
    in the frame by the author's ratio, on pixels."""
    pytest.importorskip("burns")
    from burns import Rect

    from braidio.transforms import path_for_panel
    from braidio.video import prepare_still

    still_size, subject = (356, 200), (158, 80, 198, 120)
    still = _subject_still(tmp_path / "s.png", still_size, subject=subject)
    old = prepare_still(still, tmp_path / "old.jpg", size=(320, 180))
    new = prepare_still(still, tmp_path / "new.jpg", size=(180, 320))
    keyframes = [
        {"t": 0.0, "rect": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}},
        {"t": 1.0, "rect": {"x": 0.3, "y": 0.3, "w": 0.4, "h": 0.4}},
    ]
    stored = {"version": 1, "keyframes": keyframes, "output_aspect": 16 / 9}
    body = {"move": "hold", "zoom": 1.0, "seed": 0, "path": stored}
    path = path_for_panel(body, image=str(new), aspect=9 / 16, image_size=still_size)

    def subject_share(canvas, rect):
        red, _ = _frame_census(canvas, rect)
        x0, y0, x1, y1 = rect.to_pixels(*_image_size(canvas))
        return red / ((x1 - x0) * (y1 - y0))

    authored = [Rect(**kf["rect"]) for kf in keyframes]
    growth_was = subject_share(old, authored[1]) / subject_share(old, authored[0])
    (_, start), (_, end) = path.keyframes
    growth = subject_share(new, end) / subject_share(new, start)
    # both ends outgrow the picture, so the push keeps at least half its
    # range in log zoom (_MIN_MOVE_KEPT) rather than freezing; area grows as
    # zoom squared, so that is growth >= growth_was ** 0.5
    assert growth >= growth_was**0.5 * 0.9
    for rect in (start, end):
        assert _frame_census(new, rect)[1] <= 0.02


def test_a_wide_start_does_not_crop_a_landing_that_fits(tmp_path):
    """Review round 2 of #96: one common scale for every keyframe let a wide
    start (trimmed to the picture) shrink a tight landing that fit on its own
    to a third of its width. The landing must keep the whole detail."""
    pytest.importorskip("burns")
    from braidio.transforms import path_for_panel
    from braidio.video import prepare_still

    still_size = (640, 360)
    detail = (307, 173, 333, 187)  # inside the author's (0.48, 0.48, 0.04) end
    still = _subject_still(tmp_path / "s.png", still_size, subject=detail)
    new = prepare_still(still, tmp_path / "new.jpg", size=(360, 640))
    keyframes = [
        {"t": 0.0, "rect": {"x": 0.0, "y": 0.05, "w": 0.9, "h": 0.9}},
        {"t": 1.0, "rect": {"x": 0.47, "y": 0.47, "w": 0.06, "h": 0.06}},
    ]
    stored = {"version": 1, "keyframes": keyframes, "output_aspect": 16 / 9}
    body = {"move": "hold", "zoom": 1.0, "seed": 0, "path": stored}
    path = path_for_panel(body, image=str(new), aspect=9 / 16, image_size=still_size)
    whole = _frame_census(new, path.keyframes[0][1].__class__(0, 0, 1, 1))[0]
    red, fill = _frame_census(new, path.keyframes[-1][1])
    assert red >= 0.95 * whole and fill <= 0.02
    assert path.keyframes[-1][1].zoom > 2 * path.keyframes[0][1].zoom  # a push


def _image_size(path):
    from PIL import Image

    with Image.open(path) as img:
        return img.size


def test_a_window_barely_on_the_still_is_not_blown_up_into_a_zoom():
    """Review of #96: intersecting a window with the still turned a window that
    overlapped it by a sliver into an 8x zoom on that sliver. Moved onto the
    still instead, it keeps its size: never a tighter zoom than the author's."""
    from braidio.transforms import path_on_canvas

    for x in (0.0, 0.0001, 0.05):  # all fill, a sliver, a little picture
        stored = {
            "keyframes": [
                {"t": 0.0, "rect": {"x": x, "y": 0.3, "w": 0.219, "h": 0.219}}
            ],
            "output_aspect": 16 / 9,
        }
        (kf,) = path_on_canvas(stored, image_size=(200, 200), canvas_size=(180, 320))[
            "keyframes"
        ]
        assert kf["rect"]["w"] >= 0.219 / 0.5625 - 1e-9  # the same still width


def test_a_refit_keeps_every_move_in_its_direction_and_the_fill_bound():
    """Review round 3 of #96: fitting each keyframe alone turned some pushes
    into pull-outs (a big all-picture keyframe capped below a smaller one that
    showed fill). Over a seeded sweep of 2-3 keyframe paths, still shapes and
    delivery aspects: every segment keeps at least half its log-zoom change in
    the author's direction, a held zoom stays held, and no keyframe shows more
    of the blurred fill than the author's did. The move is measured on the
    picture: the author's window capped to the still (there is no picture to
    zoom out past) and shaped for the new canvas."""
    import math
    import random

    from braidio.transforms import path_on_canvas
    from braidio.transforms._video_cut import (
        _HOLD_LOG_TOL,
        _MIN_MOVE_KEPT,
        _onto_picture,
        _picture_share,
        focus_on_canvas,
        focus_on_still,
    )

    rng = random.Random(3)
    shapes = [(100, 100), (400, 100), (100, 400), (178, 100)]
    canvases = [(1080, 1920), (1920, 1080), (1080, 1080)]
    checked = 0
    for _ in range(600):
        still, canvas = rng.choice(shapes), rng.choice(canvases)
        authored = rng.choice([16 / 9, 9 / 16, 1.0])
        if math.isclose(authored, canvas[0] / canvas[1]):
            continue
        rects = []
        for _ in range(rng.choice([2, 3])):
            w = rng.uniform(0.05, 1.0)
            rects.append(
                {"x": rng.uniform(0, 1 - w), "y": rng.uniform(0, 1 - w), "w": w, "h": w}
            )
        stored = {
            "keyframes": [{"t": i, "rect": r} for i, r in enumerate(rects)],
            "output_aspect": authored,
        }
        result = path_on_canvas(stored, image_size=still, canvas_size=canvas)
        out = [kf["rect"] for kf in result["keyframes"]]

        def on_still(r, shape):
            box = (r["x"], r["y"], r["w"], r["h"])
            return focus_on_still(box, image_size=still, canvas_size=shape)

        def side(r):
            moved = _onto_picture(on_still(r, (authored, 1)))
            x, y, w, h = focus_on_canvas(moved, image_size=still, canvas_size=canvas)
            return min(max(w, h), 1.0)

        authored_sides = [side(r) for r in rects]
        held_was = held_now = 0.0  # drift since the held run began
        for (a, b), (c, d) in zip(
            zip(authored_sides, authored_sides[1:]), zip(out, out[1:])
        ):
            was, now = math.log(a / b), math.log(c["w"] / d["w"])
            if abs(was) <= _HOLD_LOG_TOL:
                held_was, held_now = held_was + was, held_now + now
                assert abs(held_now - held_was) <= _HOLD_LOG_TOL + 1e-6, (rects, out)
            else:
                assert now / was >= _MIN_MOVE_KEPT - 1e-6, (still, canvas, rects, out)
                held_was = held_now = 0.0
            checked += 1
        for r, o in zip(rects, out):
            share_was = _picture_share(on_still(r, (authored, 1)))
            share_now = _picture_share(on_still(o, canvas))
            assert share_now >= share_was - 0.01
    assert checked > 300


def test_a_held_zoom_does_not_drift_across_a_long_pan():
    """Review round 4 of #96: a hold checked segment by segment let a 5-keyframe
    pan at one zoom drift 16% in zoom, 5% at a time. A held run is measured
    from its first keyframe."""
    import math

    from braidio.transforms._video_cut import _HOLD_LOG_TOL, _move_preserving_sides

    sides = [0.6] * 5
    widest = [0.65, 0.63, 0.61, 0.59, 0.56]  # each a little tighter
    out = _move_preserving_sides(sides, widest)
    assert math.log(max(out) / min(out)) <= _HOLD_LOG_TOL + 1e-9
    # and no one held segment swings across the band (+9% then -8%)
    sides = [0.5, 0.52, 0.5]
    out = _move_preserving_sides(sides, [0.3, 2.0, 0.3])
    for c, d in zip(out, out[1:]):
        assert abs(math.log(d / c)) <= _HOLD_LOG_TOL + 1e-9


def test_path_on_canvas_refuses_what_it_cannot_aim():
    """Review round 2 of #96: called directly (it is exported), a nan aspect,
    no keyframes or a zero-area rect is a ValueError naming the problem, not
    a TypeError from deep inside."""
    from braidio.transforms import path_on_canvas

    rect = {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}
    for stored in (
        {"keyframes": [{"t": 0, "rect": rect}], "output_aspect": "nan"},
        {"keyframes": [], "output_aspect": 16 / 9},
        {"keyframes": [{"t": 0, "rect": {**rect, "w": 0}}], "output_aspect": 16 / 9},
    ):
        with pytest.raises(ValueError):
            path_on_canvas(stored, image_size=(200, 200), canvas_size=(180, 320))


@pytest.mark.parametrize("bad", [0, -1.0, float("inf"), float("nan"), "nan", "16/9"])
def test_a_malformed_output_aspect_fails_the_plan(
    project, episode, stills, panels, bad
):
    """Review of #96: once a cross-aspect path could plan, an ``output_aspect``
    of 0, a negative, inf or nan passed the plan and raised (or framed NaNs)
    in execute. It must fail the plan, naming the value."""
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM
    from braidio.transforms._common import graph_index

    still = graph_index(project)[uuid.UUID(panels[0].body["still_id"])]
    _sized(project, still, (64, 48))
    path = _push_on_subject((64, 48), (64, 48), zoom=2.0)
    path["output_aspect"] = bad
    edited = _rewrite_in_place(
        project, panels[0], body={**panels[0].body, "path": path}
    )
    with pytest.raises(ValueError, match="not a positive aspect"):
        _plan(VIDEO_CUT_RENDER_TRANSFORM, project, edited, *panels[1:])


def _side_fill(canvas: Path, rect) -> tuple[int, int]:
    """Columns of pure fill at the window's left and right edges, along its
    middle row (where a letterboxed still shows whole across)."""
    from PIL import Image

    with Image.open(canvas) as img:
        crop = img.convert("RGB").crop(rect.to_pixels(*img.size))
    row = [crop.getpixel((x, crop.height // 2)) for x in range(crop.width)]
    is_fill = [max(px) < 140 for px in row]
    left = next((i for i, f in enumerate(is_fill) if not f), len(row))
    right = next((i for i, f in enumerate(reversed(is_fill)) if not f), len(row))
    return left, right


def _sized(project, still, size):
    return _rewrite_in_place(
        project, still, body={**still.body, "width": size[0], "height": size[1]}
    )


def test_a_cross_aspect_path_over_a_sized_still_plans_and_converts(
    project, episode, stills, panels
):
    """The plan-time refusal is lifted exactly when the still records its size
    (the conversion's input); ``panel_path`` — the studio's preview, same code
    as the render — then answers with the converted path at the new aspect,
    and the cut's key moves (a #81-era naive refit is not served)."""
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM, panel_path
    from braidio.transforms._common import graph_index

    four_three = _push_on_subject((64, 48), (64, 48), zoom=2.0)
    four_three["output_aspect"] = 4 / 3
    still = graph_index(project)[uuid.UUID(panels[0].body["still_id"])]
    _sized(project, still, (64, 48))
    edited = _rewrite_in_place(
        project, panels[0], body={**panels[0].body, "path": four_three}
    )
    plan, (skel,) = _plan(VIDEO_CUT_RENDER_TRANSFORM, project, edited, *panels[1:])
    path = panel_path(project, edited, size=(90, 160))
    assert path.output_aspect == pytest.approx(90 / 160)

    same = dict(
        four_three,
        output_aspect=skel.body["settings"]["size"][0]
        / skel.body["settings"]["size"][1],
    )
    at_cut = _rewrite_in_place(project, edited, body={**edited.body, "path": same})
    _, (skel_same,) = _plan(VIDEO_CUT_RENDER_TRANSFORM, project, at_cut, *panels[1:])
    assert skel.body["cache_key"] != skel_same.body["cache_key"]


def test_the_refit_version_keys_only_cuts_that_convert_a_path(
    project, episode, stills, panels, monkeypatch
):
    """A change to how a cross-aspect path is converted re-stales exactly the
    cuts that convert one (and keeps a cut rendered under #81's naive refit
    from being served); every other cut's key is untouched."""
    import braidio.transforms._video_cut as vc
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM
    from braidio.transforms._common import graph_index

    still = graph_index(project)[uuid.UUID(panels[0].body["still_id"])]
    _sized(project, still, (64, 48))
    cross = _push_on_subject((64, 48), (64, 48), zoom=2.0)
    cross["output_aspect"] = 4 / 3
    edited = _rewrite_in_place(
        project, panels[0], body={**panels[0].body, "path": cross}
    )

    def keys():
        _, (a,) = _plan(VIDEO_CUT_RENDER_TRANSFORM, project, edited, *panels[1:])
        _, (b,) = _plan(VIDEO_CUT_RENDER_TRANSFORM, project, *panels[1:])
        return a.body["cache_key"], b.body["cache_key"]

    before = keys()
    monkeypatch.setattr(vc, "_PATH_REFIT_VERSION", "next")
    after = keys()
    assert after[0] != before[0] and after[1] == before[1]


def test_finish_leaves_no_staging_file_where_the_lister_looks(
    project, episode, stills, panels, patched_render, patched_overlay, monkeypatch
):
    """A crash mid-chain must not leave an mp4 directly in data/cuts."""
    import tituli.video

    from braidio.transforms import (
        VIDEO_CUT_FINISH_TRANSFORM,
        VIDEO_CUT_RENDER_TRANSFORM,
    )

    motion = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    _add_label_track(project, episode, start=0.0, end=4.0, kind="title", headline="T")

    def _overlay_then_die(video, overlays, dst, **kw):
        _write(dst, b"PARTIAL")
        raise RuntimeError("ffmpeg died")

    monkeypatch.setattr(tituli.video, "overlay", _overlay_then_die)
    with pytest.raises(RuntimeError):
        _run(VIDEO_CUT_FINISH_TRANSFORM, project, motion)
    cuts = project.root / "data" / "cuts"
    assert [p.name for p in cuts.iterdir() if p.suffix == ".mp4"] == [
        Path(motion.body["url"]).name
    ]


def test_a_caption_overflow_names_the_still_and_the_delivery(
    project, episode, stills, panels, patched_render, monkeypatch
):
    """thorwhalen/braidio#77 item 4: whatever the eventual fix for the fit
    itself, the error at least has to say which still and which delivery —
    tituli's own message names only the truncated text. Fires regardless of
    (1)-(3) because it wraps the SAME ``TextDoesNotFit`` tituli always
    raises."""
    pytest.importorskip("tituli")
    import tituli.video
    from tituli.compose import TextDoesNotFit

    from braidio.transforms import (
        VIDEO_CUT_FINISH_TRANSFORM,
        VIDEO_CUT_RENDER_TRANSFORM,
    )

    motion = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]

    def _overlay_overflows(video, overlays, dst, **kw):
        # still "a" is labelled with subject "Alice, 1971" (see the `stills`
        # fixture) — this is exactly the text tituli could not set.
        raise TextDoesNotFit("Alice, 1971", 0.022, 4, 3)

    monkeypatch.setattr(tituli.video, "overlay", _overlay_overflows)
    with pytest.raises(TextDoesNotFit) as exc_info:
        _run(VIDEO_CUT_FINISH_TRANSFORM, project, motion)
    message = str(exc_info.value)
    assert "'a'" in message  # the still's key, not just the truncated text
    assert "1920x1080" in message  # the delivery's size
    # tituli's own structured attributes survive, for a programmatic catcher
    assert exc_info.value.text == "Alice, 1971"
    assert exc_info.value.max_lines == 3


def test_an_attribution_overflow_also_names_the_still(
    project, episode, stills, panels, patched_render, monkeypatch
):
    """Post-hoc review of #82: tituli fits a label's attribution separately
    from its subject, so on a narrow frame the overflowing text can be the
    credit line. It must still name the still, not "an overlay tituli could
    not identify"."""
    pytest.importorskip("tituli")
    import tituli.video
    from tituli.compose import TextDoesNotFit

    from braidio.transforms import (
        VIDEO_CUT_FINISH_TRANSFORM,
        VIDEO_CUT_RENDER_TRANSFORM,
    )

    motion = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]

    def _overlay_overflows(video, overlays, dst, **kw):
        label = next(
            o.payload for o in overlays if getattr(o.payload, "key", None) == "a"
        )
        assert label.attribution  # the fixture credits every still
        raise TextDoesNotFit(label.attribution, 0.022, 3, 2)

    monkeypatch.setattr(tituli.video, "overlay", _overlay_overflows)
    with pytest.raises(TextDoesNotFit) as exc_info:
        _run(VIDEO_CUT_FINISH_TRANSFORM, project, motion)
    # every fixture still shares one credit, so any labelled still is a
    # correct answer; what must not happen is the unidentified fallback
    assert "still '" in str(exc_info.value)
    assert "could not identify" not in str(exc_info.value)


_LONG_SUBJECT = (
    "A Central Park concert audience, Schaefer Bandstand — not the 1971 "
    "Beach Boys concert, a later crowd"
)


def _label_overlay(subject, key="a"):
    from tituli import Label, TimedOverlay

    return TimedOverlay(
        None, 0.0, 4.0, slot="top-left", payload=Label(subject, "Jane Doe, CC BY-SA 4.0", key=key)
    )


def test_a_tall_frame_wraps_a_caption_a_wide_frame_would_refuse():
    """thorwhalen/braidio#77: the same still that sets in 3 lines at 16:9 needs
    5 at 9:16. The tall frame has the height for them, so it gets them rather
    than refusing the cut — and nothing is truncated."""
    pytest.importorskip("tituli")
    from tituli.compose import TextDoesNotFit, caption
    from tituli.frame import Frame

    from braidio.transforms._video_cut import _wrap_labels_for_frame

    overlay = _label_overlay(_LONG_SUBJECT)
    vertical = Frame.blank((1080, 1920)).with_delivery("youtube")
    with pytest.raises(TextDoesNotFit):  # the premise: the default budget fails
        caption(overlay.payload.text, overlay.payload.attribution, frame=vertical, anchor="top-left")
    (wrapped,) = _wrap_labels_for_frame(
        [overlay], size=(1080, 1920), delivery="youtube"
    )
    assert wrapped.layout is not None
    assert wrapped.payload is overlay.payload  # still names its still for errors
    # the whole subject is on the layout: wrapped, not ellipsised
    assert "…" not in str(wrapped.layout) and "..." not in str(wrapped.layout)


def test_a_caption_that_already_fits_is_left_exactly_as_it_was():
    """Existing vertical cuts must render byte-identically: only an overlay
    that would have been refused is given a layout."""
    pytest.importorskip("tituli")
    from braidio.transforms._video_cut import _wrap_labels_for_frame

    short = _label_overlay("Central Park")
    (out,) = _wrap_labels_for_frame([short], size=(1080, 1920), delivery="youtube")
    assert out is short
    long_ = _label_overlay(_LONG_SUBJECT)
    (out,) = _wrap_labels_for_frame([long_], size=(1920, 1080), delivery="youtube")
    assert out is long_  # a 16:9 frame has no taller budget to offer


def test_a_caption_too_long_even_for_the_taller_budget_still_raises():
    """Raise, never truncate: the wrap is a bigger budget, not a bypass."""
    pytest.importorskip("tituli")
    from tituli.compose import TextDoesNotFit

    from braidio.transforms._video_cut import _wrap_labels_for_frame

    with pytest.raises(TextDoesNotFit):
        _wrap_labels_for_frame(
            [_label_overlay(_LONG_SUBJECT * 6)], size=(1080, 1920), delivery="youtube"
        )


def test_a_vertical_finish_hands_tituli_the_wrapped_caption(
    project, episode, stills, panels, patched_render, monkeypatch
):
    """thorwhalen/braidio#77, through the transform: ``finish`` on a 1080x1920
    cut must give tituli a laid-out caption for a subject that overflows the
    default budget. Guards the wiring, not just ``_wrap_labels_for_frame``."""
    pytest.importorskip("tituli")
    import tituli.video

    from braidio.transforms import (
        VIDEO_CUT_FINISH_TRANSFORM,
        VIDEO_CUT_RENDER_TRANSFORM,
    )

    still_a = stills[0]
    _rewrite_in_place(
        project, still_a, body={**still_a.body, "subject": _LONG_SUBJECT}
    )
    motion = _run(
        VIDEO_CUT_RENDER_TRANSFORM,
        project,
        *panels,
        params={"size": (1080, 1920), "fps": 6},
    ).annotations[0]
    seen = []

    def _record(video, overlays, dst, **kw):
        seen.extend(overlays)
        raise RuntimeError("stop after the overlay hand-off")

    monkeypatch.setattr(tituli.video, "overlay", _record)
    with pytest.raises(RuntimeError, match="hand-off"):
        _run(VIDEO_CUT_FINISH_TRANSFORM, project, motion)
    long_label = [
        o for o in seen if getattr(o.payload, "text", None) == _LONG_SUBJECT
    ]
    assert long_label and all(o.layout is not None for o in long_label)
    short_label = [o for o in seen if getattr(o.payload, "text", None) == "Carol"]
    assert short_label and all(o.layout is None for o in short_label)


def test_motion_key_names_the_move_resolver(
    project, episode, stills, panels, monkeypatch
):
    """burns' resolve_move and the shim frame six of eight moves differently,
    so which one ran is part of the key: the day burns ships the name, cuts
    re-render rather than being served under a framing the panel no longer
    describes."""
    import braidio.transforms._video_cut as vc
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM

    key0 = _plan(VIDEO_CUT_RENDER_TRANSFORM, project, *panels)[1][0].body["cache_key"]
    monkeypatch.setattr(vc, "resolver_identity", lambda: "somebody.else")
    key1 = _plan(VIDEO_CUT_RENDER_TRANSFORM, project, *panels)[1][0].body["cache_key"]
    assert key1 != key0


def test_canvases_are_keyed_on_bytes_and_size_never_ordinal_or_stem(
    project, episode, tmp_path, monkeypatch
):
    """Two stills with one filename stem at the same ordinal, and one project
    cut at two sizes: the prepared canvas must follow the bytes and the
    frame, or a swapped still ships the old picture and a vertical cut reuses
    a landscape canvas (both reproduced by the adversarial review)."""
    pytest.importorskip("burns")
    import burns
    from PIL import Image

    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM, VIDEO_PANELS_TRANSFORM

    captured: list[list] = []

    def _film(triples, *, saveas, fps, audio_path=None, **kw):
        captured.append(list(triples))
        return _write(saveas, b"FILM")

    monkeypatch.setattr(burns, "ken_burns_film", _film)
    red = add_still(
        project,
        _png(tmp_path / "a" / "portrait.png", size=(64, 96), color=(220, 30, 30)),
        key="red",
        labelled=False,
    )
    blue = add_still(
        project,
        _png(tmp_path / "b" / "portrait.png", size=(64, 96), color=(30, 30, 220)),
        key="blue",
        labelled=False,
    )
    panels = list(
        _run(
            VIDEO_PANELS_TRANSFORM,
            project,
            episode,
            params={"picks": {p: ["red"] for p in {"0000", "0001", "0003"}}},
        ).annotations
    )
    small = {"size": (96, 54), "fps": 6}
    _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels, params=small)
    # swap the first panel to the same-stem blue still and render again
    panels[0] = _rewrite_in_place(
        project, panels[0], body={**panels[0].body, "still_id": str(blue.id)}
    )
    _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels, params=small)
    canvas_red, canvas_blue = captured[0][0][0], captured[1][0][0]
    assert canvas_red != canvas_blue
    with Image.open(canvas_blue) as im:
        assert im.getpixel((48, 27))[2] > 150  # the blue still, not a stale red canvas
    # a vertical cut of the same panels does not reuse the landscape canvas
    _run(
        VIDEO_CUT_RENDER_TRANSFORM,
        project,
        *panels,
        params={"size": (54, 96), "fps": 6},
    )
    with Image.open(captured[2][0][0]) as im:
        assert im.size == (54, 96)


def test_clips_emit_no_caption(project, episode):
    """A clip's text is not known on the graph (only its label is stored),
    so it contributes no cue — a caption is a claim about what is heard."""
    from braidio.transforms._video_cut import _captions_srt

    srt = _captions_srt(project, episode, max_chars=42)
    assert "♪" not in srt and "♪ hook ♪" not in srt
    assert "Opening line" in srt and "Closing thought" in srt


# --- the genre -----------------------------------------------------------------------


def test_commentary_weave_genre_carries_the_picture_track():
    import nw

    from braidio.formats import FORMATS
    from braidio.transforms import (
        VIDEO_CUT_FINISH_TRANSFORM,
        VIDEO_CUT_RENDER_TRANSFORM,
        VIDEO_PANELS_TRANSFORM,
    )

    genre = nw.get_genre("commentary_weave")
    assert genre.is_ready()
    for uri in (
        "annot://schema/still/v1",
        "annot://schema/video-panel/v1",
        "annot://schema/video-cut/v1",
        "annot://schema/label-track/v1",
    ):
        assert uri in genre.body_schema_uris
    for name in (
        VIDEO_PANELS_TRANSFORM,
        VIDEO_CUT_RENDER_TRANSFORM,
        VIDEO_CUT_FINISH_TRANSFORM,
    ):
        assert name in genre.transform_names
    assert "commentary-video" in genre.intake_kinds
    # a video is a delivery, orthogonal to format: the templates are untouched
    assert {t.slug for t in genre.templates} == set(FORMATS)
    assert genre.projection_entrypoint == "weave_to_episode.default"


# --- placement reasons, relevance and the knobs (thorwhalen/braidio#85) -------


def _keys_by_order(skels, stills):
    by_id = {s.id: s.body["key"] for s in stills}
    return [by_id[uuid.UUID(p.body["still_id"])] for p in skels]


def test_every_panel_records_its_words_its_score_and_its_scorer(
    project, episode, stills
):
    from braidio.transforms import VIDEO_PANELS_TRANSFORM, placement_report

    skels = _plan(VIDEO_PANELS_TRANSFORM, project, episode)[1]
    assert skels[0].body["anchor_text"].startswith("Opening line")
    for p in skels:
        assert 0.0 <= p.body["relevance"] <= 1.0
        assert p.body["scorer"] == "lexical"
    # a pool-cycled still is nobody's reasoned choice: unexplained, and — since
    # the narration never names Alice or Carol — scored decorative
    assert {p.body["role"] for p in skels} == {"decorative"}
    assert all("decorative" in r["issues"] for r in placement_report(skels))


def test_the_defaults_place_the_same_stills_as_before(project, episode, stills):
    """A re-plan of an existing (e.g. imported) track must not silently move a
    picture: the new knobs describe the placement, they do not change it."""
    from braidio.transforms import VIDEO_PANELS_TRANSFORM
    from braidio.transforms._video_panels import _track_value

    new = _run(VIDEO_PANELS_TRANSFORM, project, episode).annotations
    legacy_body = [
        {
            k: v
            for k, v in p.body.items()
            if k
            not in {
                "anchor_text",
                "rationale",
                "relevance",
                "scorer",
                "role",
                "disclaimed",
            }
        }
        for p in new
    ]
    legacy = [p.model_copy(update={"body": b}) for p, b in zip(new, legacy_body)]
    # a track written before the fields existed compares equal to today's plan,
    # so execute() returns it instead of writing a new "latest" beside it
    assert _track_value(legacy) == _track_value(new)


def test_a_reasoned_pick_carries_its_reason_and_role(project, episode, stills):
    from braidio.transforms import VIDEO_PANELS_TRANSFORM, picks_from_panels
    from braidio.transforms._common import graph_index

    beat = _plan(VIDEO_PANELS_TRANSFORM, project, episode)[1][0].body["beat_id"]
    pick = {"still_key": "c", "reason": "the song's opening", "role": "contextual"}
    skels = _plan(
        VIDEO_PANELS_TRANSFORM,
        project,
        episode,
        params={"picks": {beat: [pick]}, "min_relevance": 0.0},
    )[1]
    mine = [p for p in skels if p.body["beat_id"] == beat]
    assert all(p.body["rationale"] == "the song's opening" for p in mine)
    assert all(p.body["role"] == "contextual" for p in mine)
    carried = picks_from_panels(skels, graph_index(project), with_reasons=True)[beat]
    assert carried[0] == {**pick, "disclaimed": False}
    # and a carried reason is RE-SCORED where it lands: under the default
    # threshold the same pick is decorative, because nothing names Carol
    rescored = _plan(
        VIDEO_PANELS_TRANSFORM, project, episode, params={"picks": {beat: carried}}
    )[1]
    assert {p.body["role"] for p in rescored if p.body["beat_id"] == beat} == {
        "decorative"
    }


def test_a_still_the_words_name_is_not_decorative(project, episode, stills, tmp_path):
    from braidio.transforms import VIDEO_PANELS_TRANSFORM

    add_still(
        project,
        _png(tmp_path / "song.png"),
        key="song",
        labelled=True,
        subject="The opening song",
    )
    beat = _plan(VIDEO_PANELS_TRANSFORM, project, episode)[1][0].body["beat_id"]
    skels = _plan(
        VIDEO_PANELS_TRANSFORM,
        project,
        episode,
        params={"picks": {beat: [{"still_key": "song", "role": "literal"}]}},
    )[1]
    first = skels[0]
    assert first.body["relevance"] > 0.5 and first.body["role"] == "literal"


def test_allow_decorative_refuses_or_caps(project, episode, stills):
    from braidio.transforms import VIDEO_PANELS_TRANSFORM

    with pytest.raises(ValueError, match="allow_decorative"):
        _plan(
            VIDEO_PANELS_TRANSFORM, project, episode, params={"allow_decorative": False}
        )
    with pytest.raises(ValueError, match="allow_decorative=0.1"):
        _plan(
            VIDEO_PANELS_TRANSFORM, project, episode, params={"allow_decorative": 0.1}
        )
    # nothing below threshold → nothing to refuse
    _plan(
        VIDEO_PANELS_TRANSFORM,
        project,
        episode,
        params={"allow_decorative": False, "min_relevance": 0.0},
    )


def test_a_disclaimed_pick_is_off_by_default_and_needs_a_label(
    project, episode, stills
):
    from braidio.transforms import VIDEO_PANELS_TRANSFORM

    beat = _plan(VIDEO_PANELS_TRANSFORM, project, episode)[1][0].body["beat_id"]

    def picks(key):
        return {"picks": {beat: [{"still_key": key, "disclaimed": True}]}}

    with pytest.raises(ValueError, match="allow_disclaimed=True"):
        _plan(VIDEO_PANELS_TRANSFORM, project, episode, params=picks("a"))
    with pytest.raises(ValueError, match="unlabelled"):
        _plan(
            VIDEO_PANELS_TRANSFORM,
            project,
            episode,
            params={**picks("b"), "allow_disclaimed": True},
        )
    skels = _plan(
        VIDEO_PANELS_TRANSFORM,
        project,
        episode,
        params={**picks("a"), "allow_disclaimed": True},
    )[1]
    assert skels[0].body["disclaimed"] is True


def test_pick_scope_span_chooses_by_the_words_under_each_span(project, episode, stills):
    from braidio.transforms import VIDEO_PANELS_TRANSFORM

    def scorer(text, bodies):  # a stand-in for a model scorer: likes "b" on "hook"
        return [1.0 if ("hook" in text and b["key"] == "b") else 0.0 for b in bodies]

    skels = _plan(
        VIDEO_PANELS_TRANSFORM,
        project,
        episode,
        params={"pick_scope": "span", "relevance": scorer},
    )[1]
    keys = _keys_by_order(skels, stills)
    hooked = [i for i, p in enumerate(skels) if "hook" in (p.body["anchor_text"] or "")]
    assert hooked, [p.body["anchor_text"] for p in skels]
    # the first span about the hook gets the still the scorer likes; the next
    # one may not (never the same still twice in a row)
    assert keys[hooked[0]] == "b" and skels[hooked[0]].body["relevance"] == 1.0
    assert all(a != b for a, b in zip(keys, keys[1:]))
    assert skels[0].body["scorer"].endswith("scorer")
    with pytest.raises(ValueError, match="pick_scope"):
        _plan(VIDEO_PANELS_TRANSFORM, project, episode, params={"pick_scope": "x"})
    with pytest.raises(ValueError, match="unknown relevance scorer"):
        _plan(VIDEO_PANELS_TRANSFORM, project, episode, params={"relevance": "nope"})


def test_a_disclaimer_label_is_not_hidden_by_a_heavier_card():
    """The Two Silences defect: the recording tag (weight 2) owned the label's
    slot at first appearance, so the Beach Boys still showed captioned only
    '1981 · Central Park, live'. A disclaimed panel's label now wins."""
    from types import SimpleNamespace as NS

    from lacing import MediaRef, TimeInterval, RationalTime

    from braidio.transforms._video_cut import _overlays

    def ref(a, b):
        return MediaRef(
            asset_id="x",
            interval=TimeInterval(RationalTime(a, 1), RationalTime(b, 1)),
        )

    still = NS(
        body={
            "key": "beach",
            "labelled": True,
            "subject": "The Beach Boys in Central Park, 1971 — not this concert",
            "author": "ABC",
            "license": "pdm",
        }
    )
    card = NS(
        reference=ref(0, 20),
        body={"kind": "context", "lines": ["1981 · Central Park, live"], "weight": 2},
    )

    def overlays(disclaimed):
        panel = NS(
            reference=ref(0, 10),
            body={"still_id": "s", "disclaimed": disclaimed},
        )
        return _overlays([panel], {"s": still}, [card])

    def label_times(ovs):
        return [
            (o.start, o.end)
            for o in ovs
            if not isinstance(o.payload, dict) and o.payload.key == "beach"
        ]

    assert label_times(overlays(False)) == []  # the old behaviour: hidden
    assert label_times(overlays(True)) == [(0.0, 10.0)]  # the whole panel
    card_times = [
        (o.start, o.end) for o in overlays(True) if isinstance(o.payload, dict)
    ]
    assert card_times == [(10.0, 20.0)]  # the card yields the panel's stretch


def test_a_threshold_verdict_is_not_carried_forward_as_an_authored_role(
    project, episode, stills
):
    """Review finding: a pick demoted to decorative by the threshold must not
    come back as the author's claim, or it stays decorative where the new
    narration names the still."""
    from braidio.transforms import VIDEO_PANELS_TRANSFORM, picks_from_panels
    from braidio.transforms._common import graph_index

    beat = _plan(VIDEO_PANELS_TRANSFORM, project, episode)[1][0].body["beat_id"]
    skels = _plan(
        VIDEO_PANELS_TRANSFORM,
        project,
        episode,
        params={"picks": {beat: [{"still_key": "c", "role": "literal"}]}},
    )[1]
    assert skels[0].body["role"] == "decorative"  # nothing names Carol
    carried = picks_from_panels(skels, graph_index(project), with_reasons=True)
    assert carried[beat][0]["role"] is None


def test_string_flags_are_read_strictly(project, episode, stills):
    from braidio.transforms import VIDEO_PANELS_TRANSFORM

    beat = _plan(VIDEO_PANELS_TRANSFORM, project, episode)[1][0].body["beat_id"]
    skels = _plan(
        VIDEO_PANELS_TRANSFORM,
        project,
        episode,
        params={"picks": {beat: [{"still_key": "a", "disclaimed": "false"}]}},
    )[1]
    assert skels[0].body["disclaimed"] is False
    with pytest.raises(ValueError, match="allow_disclaimed=True"):
        _plan(
            VIDEO_PANELS_TRANSFORM,
            project,
            episode,
            params={
                "picks": {beat: [{"still_key": "a", "disclaimed": True}]},
                "allow_disclaimed": "false",
            },
        )
    with pytest.raises(ValueError, match="min_relevance"):
        _plan(VIDEO_PANELS_TRANSFORM, project, episode, params={"min_relevance": 2})


def test_panel_path_is_the_path_the_render_uses(
    project, episode, stills, panels, monkeypatch, tmp_path
):
    """One source of truth for "what will the camera do": a preview through
    :func:`panel_path` must equal what ``video_cut.render`` hands the frame
    renderer, focus and crop included, or the studio shows a move the film
    does not make."""
    burns = pytest.importorskip("burns")
    if getattr(burns, "RESOLVER_IMPL_VERSION", 0) < 2:
        pytest.skip("needs burns with content_box")
    import braidio.video
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM, panel_path

    edited = _rewrite_in_place(
        project,
        panels[0],
        body={
            **panels[0].body,
            "move": "push_in",
            "zoom": 1.3,
            "focus": {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.5},
        },
    )
    # and a textured portrait still WITH a crop, so saliency, the crop and the
    # content_box all have something to act on (a flat colour exercises none)
    np = pytest.importorskip("numpy")
    PIL = pytest.importorskip("PIL.Image")
    tex = np.random.default_rng(3).integers(0, 255, (150, 75, 3), dtype=np.uint8)
    portrait = tmp_path / "portrait.png"
    PIL.fromarray(np.kron(tex, np.ones((8, 8, 1), dtype=np.uint8))).save(portrait)
    cropped = add_still(
        project,
        portrait,
        key="portrait",
        labelled=False,
        crop={"x": 0.1, "y": 0.05, "w": 0.8, "h": 0.7},
    )
    swapped = _rewrite_in_place(
        project,
        panels[1],
        body={**panels[1].body, "still_id": str(cropped.id), "zoom": 1.3},
    )
    panels = [edited, swapped, *panels[2:]]
    seen = []

    def _render_video(vpanels, *, out_path, size, workdir, prepare, path_for, **kw):
        for i, vp in enumerate(vpanels):
            canvas = prepare(vp.still, Path(workdir) / f"{i}.jpg", size=size)
            seen.append(path_for(canvas, i, vp))
        return _write(out_path, b"MOTION")

    monkeypatch.setattr(braidio.video, "render_video", _render_video)
    cut = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    size = tuple(cut.body["settings"]["size"])
    assert seen and [panel_path(project, p, size=size) for p in panels] == seen
    # JSON callers send the size as a list
    assert panel_path(project, panels[1], size=list(size)) == seen[1]


# --- media by content, not by recorded path (thorwhalen/braidio#89) --------------


def _register(project, ann, path):
    """Register a still the way the importer does: from a copy inside the project.

    The catalog hardlinks, so it refuses a file that lives outside the project
    (nw.media_catalog); the importer copies media in before registering.
    """
    import shutil

    from braidio.importing._catalog import CatalogReport, register_artifact

    inside = project.root / "media" / Path(path).name
    inside.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, inside)
    register_artifact(
        project.root,
        inside,
        artifact_id=ann.body["artifact_id"],
        kind="image",
        generated_at="2026-09-25T00:00:00Z",
        report=CatalogReport(),
    )


def test_stills_resolve_through_the_catalog_when_their_urls_point_nowhere(
    project, episode, stills, panels, tmp_path
):
    """The server case: an imported project whose recorded still urls are the
    importing machine's paths. The catalog blob, keyed by the content hash the
    graph already records, is found wherever the project now lives."""
    burns = pytest.importorskip("burns")
    if getattr(burns, "RESOLVER_IMPL_VERSION", 0) < 2:
        pytest.skip("needs burns with content_box")
    from braidio.transforms import panel_path
    from braidio.transforms._common import url_to_path

    before = [panel_path(project, p, size=(640, 360)) for p in panels]
    for still in stills:
        _register(project, still, url_to_path(still.body["url"]))
        _rewrite_in_place(
            project,
            still,
            body={**still.body, "url": "file:///Users/someone-else/gone/x.png"},
        )
    after = [panel_path(project, p, size=(640, 360)) for p in panels]
    assert after == before


def test_the_catalog_blob_wins_over_a_recorded_path(project, stills):
    """Content first: a file at the recorded path with other bytes (a still
    re-fetched under the same name) must not be read as this still."""
    from braidio.transforms._common import media_path, url_to_path

    still = stills[0]
    original = url_to_path(still.body["url"])
    _register(project, still, original)
    # replaced, not written through: the importer hardlinks the blob to the
    # project copy, so an in-place write would change both (same inode)
    original.unlink()
    original.write_bytes(b"not the still any more")
    found = media_path(project.root, still.body, what="still")
    assert found != original and found.read_bytes() != b"not the still any more"


def test_a_still_found_nowhere_names_every_place_looked(project, stills):
    from braidio.transforms._common import media_path

    body = {**stills[0].body, "url": "file:///nowhere/at/all.png"}
    with pytest.raises(
        FileNotFoundError, match=r"catalog blob .*; .*nowhere.at.all\.png"
    ):
        media_path(project.root, body, what="still")


def test_a_deleted_cut_re_renders_even_when_its_blob_survives(
    project, episode, stills, panels, patched_render
):
    """Review of braidio#90: a cut is a deliverable at its own path; finding
    its bytes in the catalog must not make a deleted file count as present."""
    from braidio.importing._catalog import CatalogReport, register_artifact
    from braidio.transforms import VIDEO_CUT_RENDER_TRANSFORM
    from braidio.transforms._common import url_to_path

    cut = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    path = url_to_path(cut.body["url"])
    register_artifact(
        project.root,
        path,
        artifact_id=cut.body["artifact_id"],
        kind="video",
        generated_at="2026-09-25T00:00:00Z",
        report=CatalogReport(),
    )
    path.unlink()
    again = _run(VIDEO_CUT_RENDER_TRANSFORM, project, *panels).annotations[0]
    assert len(patched_render) == 2  # rendered again, not served from the blob
    assert url_to_path(again.body["url"]).exists()
