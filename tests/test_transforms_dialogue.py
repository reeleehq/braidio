"""Dialogue beats through the **graph** path (thorwhalen/braidio#46).

``Dialogue`` was the last beat type ``ingest_script`` refused. These tests
drive the ``commentary_weave`` chain over a script that carries one, with the
synthesis boundaries monkeypatched (no ElevenLabs, no ffmpeg — ``render_dialogue``
is a file-writing stub), and pin:

- the authoring shape: one ``dialogue-beat/v1`` per beat, identified by its
  script position like every other beat, plus **one** ``dialogue-cast/v1``
  singleton — written only when there is a dialogue beat to cast;
- the render: a ``dialogue-render/v1`` derived from ``[beat, cast]`` and
  nothing else, woven into the episode as talk;
- the money: real ``cost_usd_actual`` on a live call, ``$0`` + savings on a
  graph cache hit and on a disk cache hit, ``None`` when unpriced;
- the #51 rule applied to the cast: a re-ingest with a different cast rewrites
  the node under its id, so the dialogue renders (and the episode) read stale
  through provenance while the narration renders stay fresh;
- the upgrade path: a project written by 0.0.35 (no dialogue, no cast node)
  re-weaves under this build as a no-op, whatever cast the caller passes.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

import braidio
from braidio.conversation import ConversationCast

pytestmark = pytest.mark.skipif(
    not braidio.HAS_NW, reason="nw (and lacing) not available"
)

TURNS = (("A", "The thing that gets me is the bass."), ("B", "Say more?"))
RECAST = ConversationCast(roles={"A": "voice-one", "B": "voice-two"})


class _FakeSource:
    """A SegmentSource that resolves any reference to a fixed window."""

    def __init__(self, asset_path: Path):
        self._asset_path = asset_path

    def resolve(self, reference: str):
        from braidio.sources import ResolvedSegment

        return ResolvedSegment(
            asset_path=self._asset_path,
            start_s=1.0,
            end_s=4.0,
            score=1.0,
            matched_text=reference,
        )


def _write(out_path, tag: bytes) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(tag)
    return p


@pytest.fixture
def synthesis(monkeypatch):
    """Stub every audio boundary; return a recorder of what was called.

    ``render_dialogue`` counts its calls and can be told to report a disk
    cache hit (``was_cached``), so the cost attribution is testable without a
    cache directory. ``weave_timeline`` keeps the timeline items it was given.
    """
    rec = {"dialogue_calls": [], "was_cached": False, "timelines": []}

    def _render_dialogue(turns, cast, *, out_path, return_cache_status=False, **kw):
        rec["dialogue_calls"].append((tuple(turns), cast))
        p = _write(out_path, b"DIALOGUE")
        return (p, rec["was_cached"]) if return_cache_status else p

    def _narrate(text, out, *, return_cache_status=False, **kw):
        p = _write(out, b"TTS")
        return (p, False) if return_cache_status else p

    def _weave(items, out, **kw):
        rec["timelines"].append(list(items))
        return _write(out, b"EPISODE")

    monkeypatch.setattr(braidio, "render_dialogue", _render_dialogue)
    monkeypatch.setattr(braidio, "narrate", _narrate)
    monkeypatch.setattr(
        braidio,
        "extract_padded",
        lambda asset, start, end, out, **kw: _write(out, b"CLIP"),
    )
    monkeypatch.setattr(braidio, "weave_timeline", _weave)
    monkeypatch.setattr(braidio, "duration_s", lambda path: 2.0)
    return rec


@pytest.fixture
def project(tmp_path):
    return braidio.Project.init(tmp_path / "proj", title="dialogue weave")


@pytest.fixture
def source(tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"SONG")
    return _FakeSource(song)


def _script(*beats):
    return braidio.Script(title="Dialogue", id_slug="dlg", beats=list(beats))


@pytest.fixture
def script():
    """narration, dialogue, segment — every spoken kind plus a clip."""
    return _script(
        braidio.Narration(text="Opening line."),
        braidio.Dialogue(TURNS, label="the bass"),
        braidio.SegmentBeat(reference="the famous hook", label="hook"),
    )


def _tier(project, tier):
    import nw

    return nw.annotations_at_tier(project.root, tier)


def _ids(project, tier):
    return {a.id for a in _tier(project, tier)}


def _index(project):
    import nw

    return {a.id: a for a in nw.iter_all_annotations(project.root)}


def _members(project, episode):
    index = _index(project)
    return [index[UUID(m)] for m in episode.body["ordered_member_ids"]]


# --- ingest ------------------------------------------------------------------


def test_ingest_writes_dialogue_beats_and_one_cast(project, script, source):
    from braidio.conversation import DEFAULT_CAST
    from braidio.transforms._common import node_identity

    ing = braidio.transforms.ingest_script(project, script, source=source)

    (beat,) = ing.dialogue_beats
    assert beat.tier == "dialogue-beats"
    assert beat.body["beat_id"] == "0001"  # script position, like every beat
    assert node_identity(beat) == "0001"
    assert beat.body["turns"] == [list(t) for t in TURNS]  # json-mode round trip
    assert beat.body["label"] == "the bass"
    assert [k for k, _ in ing.ordered] == ["narration", "dialogue", "segment"]

    # the cast: one node, the tier is its identity, the default cast recorded
    assert ing.cast is not None and ing.cast.tier == "dialogue-casts"
    assert node_identity(ing.cast) == "dialogue-casts"
    assert ing.cast.body["roles"] == DEFAULT_CAST.roles
    assert ing.cast.body["model_id"] == DEFAULT_CAST.model_id
    assert ing.cast.body["settings"] == DEFAULT_CAST.settings
    assert len(_tier(project, "dialogue-casts")) == 1


def test_format_cast_is_recorded_and_an_explicit_cast_wins(project, synthesis):
    from braidio.formats import DEEP_DIVE

    script = _script(
        braidio.Dialogue((("host_a", "hi"), ("host_b", "hey"))),
    )
    braidio.weave_project(project, script, fmt=DEEP_DIVE)
    (cast,) = _tier(project, "dialogue-casts")
    assert cast.body["roles"] == DEEP_DIVE.cast.roles

    mine = ConversationCast(roles={"host_a": "x", "host_b": "y"})
    braidio.weave_project(project, script, fmt=DEEP_DIVE, cast=mine)
    (cast_after,) = _tier(project, "dialogue-casts")
    assert cast_after.id == cast.id  # same identity, rewritten in place
    assert cast_after.body["roles"] == mine.roles


def test_an_unknown_role_fails_before_the_first_write(project, source):
    """Validate before mutating (braidio#49): the role lookup that
    ``render_dialogue`` would do inside a paid call happens in the plan."""
    import nw

    script = _script(braidio.Dialogue((("host_a", "hi"),)))
    with pytest.raises(ValueError, match=r"\['host_a'\].*cast roles: \['A', 'B'\]"):
        braidio.transforms.ingest_script(project, script, source=source)
    assert list(nw.iter_all_annotations(project.root)) == []


def test_a_script_without_dialogue_writes_no_cast_node(project, source, synthesis):
    """The absence rule (structure, profile — and now cast): a production that
    has nothing to cast writes nothing at the tier, whatever the format or the
    caller declared. This is what keeps a 0.0.35 project's graph identical."""
    from braidio.formats import DEEP_DIVE
    from braidio.transforms._common import AUTHORING_TIERS, node_identity

    script = _script(
        braidio.Narration(text="one"),
        braidio.SegmentBeat(reference="hook", label="hook"),
    )
    braidio.transforms.ingest_script(
        project, script, source=source, cast=DEEP_DIVE.cast
    )
    assert _tier(project, "dialogue-casts") == []
    assert _tier(project, "dialogue-beats") == []
    identities = {
        (tier, node_identity(a))
        for tier in AUTHORING_TIERS
        for a in _tier(project, tier)
    }
    assert identities == {
        ("weave-configs", "weave-configs"),
        ("narrative-beats", "0000"),
        ("source-media", "0001"),
        ("audio-clips", "0001"),
    }


# --- render ------------------------------------------------------------------


def test_weave_project_renders_dialogue_through_the_graph(
    project, script, source, synthesis
):
    from braidio.transforms._common import url_to_path

    episode = braidio.weave_project(project, script, source=source)

    counts = {
        t: len(_tier(project, t))
        for t in (
            "narrative-beats",
            "dialogue-beats",
            "dialogue-casts",
            "audio-clips",
            "voice-assignments",
            "narration-renders",
            "dialogue-renders",
            "segment-extractions",
            "episode-renders",
        )
    }
    assert counts == {
        "narrative-beats": 1,
        "dialogue-beats": 1,
        "dialogue-casts": 1,
        "audio-clips": 1,
        "voice-assignments": 1,
        "narration-renders": 1,
        "dialogue-renders": 1,
        "segment-extractions": 1,
        "episode-renders": 1,
    }
    # one Text-to-Dialogue pass, over the beat's turns, under the graph's cast
    ((turns, cast),) = synthesis["dialogue_calls"]
    assert turns == TURNS
    assert isinstance(cast, ConversationCast)
    (cast_node,) = _tier(project, "dialogue-casts")
    assert cast.roles == cast_node.body["roles"]

    # the render derives from the beat and the cast — and NOT the weave-config
    (render,) = _tier(project, "dialogue-renders")
    (beat,) = _tier(project, "dialogue-beats")
    assert set(render.provenance.was_derived_from) == {beat.id, cast_node.id}
    assert render.body["artifact_id"] and url_to_path(render.body["url"]).exists()
    assert render.body["cache_key"]

    # ...and reaches the episode in script order, woven as talk, not a clip
    members = _members(project, episode)
    assert [m.tier for m in members] == [
        "narration-renders",
        "dialogue-renders",
        "segment-extractions",
    ]
    (items,) = synthesis["timelines"]
    assert [i.kind for i in items] == ["narration", "narration", "clip"]


def test_dialogue_render_cache_skip_reports_savings(
    project, script, source, synthesis, monkeypatch
):
    """The explicit ``cache_key`` compare-and-skip: a second render of the same
    exchange under the same cast is the node already there — no call, $0
    actual, the estimate reported as the saving."""
    from nw import TransformInputs
    from braidio.cost import RATE_ENV_VAR, tts_cost_usd

    monkeypatch.delenv(RATE_ENV_VAR, raising=False)
    braidio.transforms.ingest_script(project, script, source=source)
    (beat,) = _tier(project, "dialogue-beats")
    import nw

    dialogue = nw.get_transform(braidio.transforms.DIALOGUE_RENDER_TRANSFORM)

    first = dialogue.execute(
        project, *dialogue.plan(project, TransformInputs(primary=(beat,)))
    )
    expected = tts_cost_usd("".join(t for _, t in TURNS), model_id="eleven_v3")
    assert expected > 0
    assert first.cost_usd_actual == pytest.approx(expected)
    assert first.artifacts[0].cost_usd == pytest.approx(expected)
    assert first.has_unknown_costs is False  # priced: the number is the truth

    hit = dialogue.execute(
        project, *dialogue.plan(project, TransformInputs(primary=(beat,)))
    )
    assert hit.annotations[0].id == first.annotations[0].id
    assert len(_tier(project, "dialogue-renders")) == 1
    assert len(synthesis["dialogue_calls"]) == 1  # no second call
    assert hit.artifacts == ()
    assert hit.cost_usd_actual == 0.0
    assert hit.cache_hit_savings_usd == pytest.approx(expected)


def test_dialogue_disk_cache_hit_reports_zero_actual(
    project, script, source, synthesis, monkeypatch
):
    """braidio#8 for dialogue: the graph cache misses (fresh project) but the
    on-disk dialogue cache serves the take — $0 actual, estimate as saving."""
    import nw
    from nw import TransformInputs
    from braidio.cost import RATE_ENV_VAR, tts_cost_usd

    monkeypatch.delenv(RATE_ENV_VAR, raising=False)
    synthesis["was_cached"] = True
    braidio.transforms.ingest_script(project, script, source=source)
    (beat,) = _tier(project, "dialogue-beats")
    dialogue = nw.get_transform(braidio.transforms.DIALOGUE_RENDER_TRANSFORM)

    result = dialogue.execute(
        project, *dialogue.plan(project, TransformInputs(primary=(beat,)))
    )
    expected = tts_cost_usd("".join(t for _, t in TURNS), model_id="eleven_v3")
    assert result.cost_usd_actual == 0.0
    assert result.cache_hit_savings_usd == pytest.approx(expected)
    assert result.artifacts[0].cost_usd == pytest.approx(expected)  # the estimate


def test_dialogue_render_unpriced_cost(project, script, source, synthesis, monkeypatch):
    """Unpriced is honest, not free: ``None`` on the artifact, and because nw's
    ``cost_usd_actual`` is a plain float the unknown rides on
    ``has_unknown_costs`` — a bare ``0.0`` would be the fake zero the cost model
    forbids (#57 review). The unknown survives a cache hit, whose saving is
    unknown too."""
    import nw
    from nw import TransformInputs
    from braidio.cost import RATE_ENV_VAR

    monkeypatch.setenv(RATE_ENV_VAR, "none")
    braidio.transforms.ingest_script(project, script, source=source)
    (beat,) = _tier(project, "dialogue-beats")
    dialogue = nw.get_transform(braidio.transforms.DIALOGUE_RENDER_TRANSFORM)

    result = dialogue.execute(
        project, *dialogue.plan(project, TransformInputs(primary=(beat,)))
    )
    assert result.artifacts[0].cost_usd is None
    assert result.cost_usd_actual == 0.0
    assert result.cache_hit_savings_usd == 0.0
    assert result.has_unknown_costs is True

    hit = dialogue.execute(
        project, *dialogue.plan(project, TransformInputs(primary=(beat,)))
    )
    assert hit.artifacts == ()
    assert hit.cost_usd_actual == 0.0 and hit.cache_hit_savings_usd == 0.0
    assert hit.has_unknown_costs is True


def test_dialogue_render_stamps_its_identity_on_provenance_and_key(
    project, script, source, synthesis, monkeypatch
):
    """The transform's identity reaches both places it must (nw#27, nw#54):
    provenance names ``transform:<name>@<impl_version>`` and is attributed to
    braidio, and an ``impl_version`` bump salts the cache key — so "same
    interface, changed behaviour" cannot serve a stale take forever."""
    import nw
    from nw import TransformInputs

    braidio.transforms.ingest_script(project, script, source=source)
    (beat,) = _tier(project, "dialogue-beats")
    dialogue = nw.get_transform(braidio.transforms.DIALOGUE_RENDER_TRANSFORM)

    _, (skel,) = dialogue.plan(project, TransformInputs(primary=(beat,)))
    assert (
        skel.provenance.was_generated_by
        == f"transform:{dialogue.name}@{dialogue.impl_version}"
    )
    assert skel.provenance.was_attributed_to == "agent:braidio"
    assert skel.provenance.activity == "derive"

    key_v1 = skel.body["cache_key"]
    monkeypatch.setattr(dialogue, "impl_version", "2")
    _, (skel_v2,) = dialogue.plan(project, TransformInputs(primary=(beat,)))
    assert skel_v2.body["cache_key"] != key_v1
    assert skel_v2.provenance.was_generated_by == f"transform:{dialogue.name}@2"
    monkeypatch.undo()
    _, (skel_again,) = dialogue.plan(project, TransformInputs(primary=(beat,)))
    assert skel_again.body["cache_key"] == key_v1  # stable at the same version


# --- the cast is a singleton under the #51 rule --------------------------------


def test_reingest_with_a_changed_cast_restales_only_the_dialogue_renders(
    project, script, source, synthesis
):
    """Recast → the cast node is rewritten under its id → every dialogue render
    (and the episode) reads ``upstream-changed``; the narration render, its
    voice assignment and the extraction do not derive from the cast and stay
    fresh, and the re-run reuses them by value."""
    import nw

    first = braidio.weave_project(project, script, source=source)
    (cast_before,) = _tier(project, "dialogue-casts")
    (dialogue_before,) = _tier(project, "dialogue-renders")
    untouched = {
        tier: _ids(project, tier)
        for tier in ("voice-assignments", "narration-renders", "segment-extractions")
    }
    beats_before = {
        tier: _ids(project, tier)
        for tier in ("narrative-beats", "dialogue-beats", "audio-clips")
    }

    second = braidio.weave_project(project, script, source=source, cast=RECAST)

    (cast_after,) = _tier(project, "dialogue-casts")
    assert cast_after.id == cast_before.id
    assert cast_after.body["roles"] == RECAST.roles
    stale = {a.id for a in nw.stale_after(project.root, cast_after.id)}
    assert dialogue_before.id in stale
    assert first.id in stale
    for tier, ids in untouched.items():
        assert not (ids & stale), tier
    # the beats did not change → same nodes
    for tier, ids in beats_before.items():
        assert _ids(project, tier) == ids, tier

    # the returned episode is current, with a NEW dialogue take under the new
    # cast and the SAME narration / extraction members as before
    assert second.id != first.id
    stale_now = {a.id for a in nw.all_stale(project.root)}
    assert second.id not in stale_now
    new_members = _members(project, second)
    assert not any(m.id in stale_now for m in new_members)
    (dialogue_after,) = [m for m in new_members if m.tier == "dialogue-renders"]
    assert dialogue_after.id != dialogue_before.id
    assert dialogue_after.body["cache_key"] != dialogue_before.body["cache_key"]
    assert len(synthesis["dialogue_calls"]) == 2  # one take per cast
    assert {m.id for m in new_members if m.tier != "dialogue-renders"} == (
        untouched["narration-renders"] | untouched["segment-extractions"]
    )


def test_reingest_of_the_same_dialogue_script_is_a_noop(
    project, script, source, synthesis
):
    import nw

    first = braidio.weave_project(project, script, source=source)
    before = {a.id for a in nw.iter_all_annotations(project.root)}

    second = braidio.weave_project(project, script, source=source)

    assert second.id == first.id
    assert {a.id for a in nw.iter_all_annotations(project.root)} == before
    assert len(synthesis["dialogue_calls"]) == 1


def test_removing_the_dialogue_beat_removes_the_cast(
    project, script, source, synthesis
):
    """Identity gone from the plan → removed: no dialogue beat, no cast, and
    the old render knows (``upstream-missing``)."""
    import nw

    braidio.weave_project(project, script, source=source)
    (render,) = _tier(project, "dialogue-renders")

    without = _script(*[b for b in script.beats if not isinstance(b, braidio.Dialogue)])
    braidio.weave_project(project, without, source=source)

    assert _tier(project, "dialogue-casts") == []
    assert _tier(project, "dialogue-beats") == []
    verdicts = {v.annotation.id: v for v in nw.stale_verdicts_all(project.root)}
    assert verdicts[render.id].is_stale
    assert verdicts[render.id].reason == nw.freshness.REASON_UPSTREAM_MISSING


# --- the upgrade path ----------------------------------------------------------


def _ingest_as_written_by_0_0_35(project, script, source):
    """Write the authoring layer the way braidio 0.0.35 (commit 29d0764) did.

    The shape that build persisted, reproduced literally: config node first,
    one fresh uuid per node, ``beat_id`` on every beat-derived node (it
    arrived in #56) — and no ``dialogue-casts`` node, because the tier did
    not exist. Every guest project on the hosted connector written between
    #56 and this change has exactly this shape.
    """
    import uuid

    from lacing import Annotation, MediaRef, TimeInterval
    from braidio.bodies import (
        AUDIO_CLIP_V1,
        NARRATIVE_BEAT_V1,
        SOURCE_MEDIA_V1,
        WEAVE_CONFIG_V1,
    )
    from braidio.transforms._common import node_ref
    from braidio.transforms._ingest import _authored_provenance

    def _write(tier, body, uri, reference=None):
        ann = Annotation(
            id=uuid.uuid4(),
            tier=tier,
            reference=reference if reference is not None else node_ref(tier),
            body=body,
            body_schema_uri=uri,
            provenance=_authored_provenance(),
        )
        project.graph.add_annotation(ann)
        return ann

    _write(
        "weave-configs", {"config": braidio.WeaveConfig().to_dict()}, WEAVE_CONFIG_V1
    )
    ordered = []
    for i, beat in enumerate(script.beats):
        beat_id = f"{i:04d}"
        if isinstance(beat, braidio.Narration):
            ann = _write(
                "narrative-beats",
                {
                    "beat_id": beat_id,
                    "text": beat.text,
                    "style": beat.style,
                    "draws_on": [],
                    "plays_clip": None,
                },
                NARRATIVE_BEAT_V1,
            )
            ordered.append(("narration", ann))
        else:
            resolved = source.resolve(beat.reference)
            label = beat.label or beat.reference
            src = _write(
                "source-media",
                {
                    "label": label,
                    "asset_id": str(resolved.asset_path),
                    "rights": beat.rights,
                    "beat_id": beat_id,
                },
                SOURCE_MEDIA_V1,
            )
            clip = _write(
                "audio-clips",
                {
                    "source_node_id": str(src.id),
                    "label": label,
                    "rights": beat.rights,
                    "gain_db": None,
                    "fade": None,
                    "spotlight": beat.spotlight,
                    "beat_id": beat_id,
                },
                AUDIO_CLIP_V1,
                reference=MediaRef(
                    asset_id=str(resolved.asset_path),
                    interval=TimeInterval.from_seconds(
                        resolved.start_s, resolved.end_s, rate=1000
                    ),
                ),
            )
            ordered.append(("segment", clip))
    return ordered


@pytest.mark.parametrize("cast", [None, RECAST], ids=["no-cast", "explicit-cast"])
def test_reweave_of_a_project_written_by_0_0_35_is_a_noop(
    project, source, synthesis, cast
):
    """The connector's live guest projects: written and woven by 0.0.35, with
    no dialogue. Re-weaving under this build — cast argument or not — must
    add no node, rewrite no node, and return the episode already there."""
    import nw
    from braidio.transforms import _run

    script = _script(
        braidio.Narration(text="Opening line about the song."),
        braidio.SegmentBeat(reference="the famous hook", label="hook"),
        braidio.Narration(text="Closing thought on the hook."),
    )
    legacy = _ingest_as_written_by_0_0_35(project, script, source)
    voice = nw.get_transform(braidio.transforms.VOICE_ASSIGNMENT_TRANSFORM)
    narration = nw.get_transform(braidio.transforms.NARRATION_RENDER_TRANSFORM)
    segment = nw.get_transform(braidio.transforms.SEGMENT_EXTRACTION_TRANSFORM)
    episode_t = nw.get_transform(braidio.transforms.EPISODE_TRANSFORM)
    members = []
    for kind, auth in legacy:
        if kind == "narration":
            _run(voice, project, auth)
            members.append(_run(narration, project, auth))
        else:
            members.append(_run(segment, project, auth))
    old_episode = _run(episode_t, project, *members)
    before = _index(project)

    episode = braidio.weave_project(project, script, source=source, cast=cast)

    assert episode.id == old_episode.id
    after = _index(project)
    assert set(after) == set(before)
    for aid, ann in before.items():
        assert after[aid].body == ann.body
        assert (
            after[aid].provenance.generated_at_time == ann.provenance.generated_at_time
        )
    assert _tier(project, "dialogue-casts") == []
    assert not any(
        a.id
        in {v.annotation.id for v in nw.stale_verdicts_all(project.root) if v.is_stale}
        for a in after.values()
    )


# --- the genre -----------------------------------------------------------------


def test_commentary_weave_genre_carries_dialogue():
    import nw
    from braidio.bodies import DIALOGUE_BEAT_V1, DIALOGUE_CAST_V1, DIALOGUE_RENDER_V1

    genre = nw.get_genre("commentary_weave")
    assert genre.is_ready()
    assert {DIALOGUE_BEAT_V1, DIALOGUE_CAST_V1, DIALOGUE_RENDER_V1} <= set(
        genre.body_schema_uris
    )
    assert braidio.transforms.DIALOGUE_RENDER_TRANSFORM in genre.transform_names


def test_the_weave_receipt_counts_dialogue_and_a_cache_hit_as_savings(
    project, script, source, synthesis
):
    """braidio#147: a dialogue beat bills at the dialogue model's rate; a
    Text-to-Dialogue take mixing served from disk is savings, not spend."""
    from braidio.cost import tts_cost_usd
    from braidio.tts import DEFAULT_MODEL_ID, DIALOGUE_MODEL_ID

    talk = tts_cost_usd("".join(t for _r, t in TURNS), model_id=DIALOGUE_MODEL_ID)
    line = tts_cost_usd("Opening line.", model_id=DEFAULT_MODEL_ID)

    live = braidio.weave_project(project, script, source=source, return_receipt=True)
    assert live.cost_usd_actual == pytest.approx(line + talk)

    synthesis["was_cached"] = True  # a fresh project: synthesized, but from disk
    other = braidio.Project.init(project.root.parent / "other", title="cached")
    cached = braidio.weave_project(other, script, source=source, return_receipt=True)
    assert cached.cost_usd_actual == pytest.approx(line)  # the narration was live
    assert cached.cache_hit_savings_usd == pytest.approx(talk)
