"""Offline tests for braidio's nw.Transform pipeline (thorwhalen/braidio#6).

Drives the whole ``commentary_weave`` chain (ingest → voice-assignment →
narration-render → segment-extraction → weave-to-episode) with the synthesis
boundaries (``narrate`` / ``extract_padded`` / ``weave_timeline``)
monkeypatched, so no ElevenLabs / ffmpeg runs. Asserts:

- every authoring + render node lands in the project graph;
- ``nw.stale_after`` (over ``project.graph``, not braidio's standalone store)
  returns exactly the right partial-re-render frontier for a config change, a
  narration-beat change, and a source change;
- the explicit ``cache_key`` compare-and-skip reuses an existing render;
- the ``commentary_weave`` genre is registered and ready.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

import braidio

pytestmark = pytest.mark.skipif(
    not braidio.HAS_NW, reason="nw (and lacing) not available"
)


class _FakeSource:
    """A SegmentSource that resolves any reference to a fixed window."""

    def __init__(self, asset_path: Path, *, start_s: float = 1.0, end_s: float = 4.0):
        self._asset_path = asset_path
        self._start_s = start_s
        self._end_s = end_s

    def resolve(self, reference: str):
        from braidio.sources import ResolvedSegment

        return ResolvedSegment(
            asset_path=self._asset_path,
            start_s=self._start_s,
            end_s=self._end_s,
            score=1.0,
            matched_text=reference,
        )


@pytest.fixture
def patched_synthesis(monkeypatch):
    """Replace the audio boundaries with file-writing stubs (no real synth)."""

    def _write(out_path, tag: bytes):
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(tag)
        return p

    def _narrate(text, out, *, return_cache_status=False, **kw):
        p = _write(out, b"TTS")  # default: live synth (was_cached=False)
        return (p, False) if return_cache_status else p

    monkeypatch.setattr(braidio, "narrate", _narrate)
    monkeypatch.setattr(
        braidio,
        "extract_padded",
        lambda asset, start, end, out, **kw: _write(out, b"CLIP"),
    )
    monkeypatch.setattr(
        braidio, "weave_timeline", lambda items, out, **kw: _write(out, b"EPISODE")
    )
    # scene breaks: stub the two ffmpeg-backed structure primitives too, so this
    # module stays ffmpeg-free (the real-audio graph path is covered in
    # tests/test_transforms_structure.py).
    monkeypatch.setattr(
        braidio.structure, "prepare_sting", lambda st, out, **kw: _write(out, b"STING")
    )
    monkeypatch.setattr(
        braidio.structure, "prepare_pause", lambda s, out, **kw: _write(out, b"PAUSE")
    )
    monkeypatch.setattr(braidio, "duration_s", lambda path: 2.0)


@pytest.fixture
def project(tmp_path):
    return braidio.Project.init(tmp_path / "proj", title="test weave")


@pytest.fixture
def script_and_source(tmp_path):
    """A 3-beat script (narration, segment, narration) + a fake source."""
    song = tmp_path / "song.mp3"
    song.write_bytes(b"SONG")
    script = braidio.Script(
        title="Demo",
        id_slug="demo",
        beats=[
            braidio.Narration(text="Opening line about the song."),
            braidio.SegmentBeat(reference="the famous hook", label="hook"),
            braidio.Narration(text="Closing thought on the hook."),
        ],
    )
    return script, _FakeSource(song)


def _tier_ids(project, tier):
    import nw

    return {a.id for a in nw.annotations_at_tier(project.root, tier)}


def _rewrite_in_place(project, ann, *, body: dict):
    """Replace ``ann``'s body keeping its id — what a real edit does.

    ``nw.stale_after`` grew a verifying-trace early cutoff (nw#24): freshly
    derived nodes whose traces match their parents' *current* digests are
    fresh, so querying it right after a weave — with nothing changed — is
    correctly empty. To assert a re-render frontier the fixture must actually
    change the parent. ``store.add`` is a plain INSERT, so in-place update is
    remove-then-add; going back through ``add_annotation`` is the point — it
    re-records the trace (idiom from nw's tests/test_freshness.py).
    """
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


def test_weave_project_populates_graph(project, script_and_source, patched_synthesis):
    import nw

    script, source = script_and_source
    episode = braidio.weave_project(project, script, source=source)

    # The episode is complete: it references the assembled audio.
    assert episode.tier == "episode-renders"
    assert episode.body["artifact_id"]
    assert episode.body["url"].startswith("file://")
    assert len(episode.body["ordered_member_ids"]) == 3  # 2 narration + 1 segment

    # Every authoring + render node landed in the project graph.
    counts = {
        t: len(nw.annotations_at_tier(project.root, t))
        for t in (
            "weave-configs",
            "narrative-beats",
            "source-media",
            "audio-clips",
            "voice-assignments",
            "narration-renders",
            "segment-extractions",
            "episode-renders",
        )
    }
    assert counts == {
        "weave-configs": 1,
        "narrative-beats": 2,
        "source-media": 1,
        "audio-clips": 1,
        "voice-assignments": 2,
        "narration-renders": 2,
        "segment-extractions": 1,
        "episode-renders": 1,
    }

    # The produced audio files exist on disk.
    for nr in nw.annotations_at_tier(project.root, "narration-renders"):
        assert braidio.transforms._common.url_to_path(nr.body["url"]).exists()


def test_stale_after_config_restales_all_renders(
    project, script_and_source, patched_synthesis
):
    import nw

    script, source = script_and_source
    braidio.weave_project(project, script, source=source)

    (cfg,) = nw.annotations_at_tier(project.root, "weave-configs")
    render_ids = (
        _tier_ids(project, "voice-assignments")
        | _tier_ids(project, "narration-renders")
        | _tier_ids(project, "segment-extractions")
        | _tier_ids(project, "episode-renders")
    )
    # Actually change the config (a fresh weave is correctly all-fresh).
    _rewrite_in_place(
        project,
        cfg,
        body={**cfg.body, "config": {**cfg.body["config"], "voice_seed": 8}},
    )
    stale = {a.id for a in nw.stale_after(project.root, cfg.id)}
    # A weave-config change re-stales every render node, and nothing authoring.
    assert stale == render_ids
    assert stale.isdisjoint(
        _tier_ids(project, "narrative-beats") | _tier_ids(project, "source-media")
    )


def test_stale_after_narration_beat_scope(
    project, script_and_source, patched_synthesis
):
    import nw

    script, source = script_and_source
    braidio.weave_project(project, script, source=source)

    beats = nw.annotations_at_tier(project.root, "narrative-beats")
    beat = beats[0]
    (episode_id,) = _tier_ids(project, "episode-renders")
    # Actually change the beat's text (a fresh weave is correctly all-fresh).
    _rewrite_in_place(
        project, beat, body={**beat.body, "text": beat.body["text"] + " (edited)"}
    )
    stale = {a.id for a in nw.stale_after(project.root, beat.id)}
    # Change one narration beat → its voice-assignment, its narration-render,
    # and the episode re-stale (3 nodes) — not the other beat's renders.
    assert len(stale) == 3
    assert episode_id in stale


def test_stale_after_source_scope(project, script_and_source, patched_synthesis):
    import nw

    script, source = script_and_source
    braidio.weave_project(project, script, source=source)

    (source_media,) = nw.annotations_at_tier(project.root, "source-media")
    (episode_id,) = _tier_ids(project, "episode-renders")
    # Actually repoint the source (a fresh weave is correctly all-fresh).
    _rewrite_in_place(
        project,
        source_media,
        body={**source_media.body, "asset_id": source_media.body["asset_id"] + ".v2"},
    )
    stale = {a.id for a in nw.stale_after(project.root, source_media.id)}
    # A source change re-stales its segment-extraction + the episode only.
    assert stale == (_tier_ids(project, "segment-extractions") | {episode_id})


def test_narration_render_cache_skip(project, script_and_source, patched_synthesis):
    import nw
    from nw import TransformInputs

    script, source = script_and_source
    braidio.transforms.ingest_script(project, script, source=source)
    beat = nw.annotations_at_tier(project.root, "narrative-beats")[0]
    voice = nw.get_transform("beat_to_voice_assignment.default")
    narration = nw.get_transform("narration_render.tts")

    voice.execute(project, *voice.plan(project, TransformInputs(primary=(beat,))))
    plan1, skel1 = narration.plan(project, TransformInputs(primary=(beat,)))
    first = narration.execute(project, plan1, skel1).annotations[0]

    # A second render of the same beat hits the cache_key: no new node.
    plan2, skel2 = narration.plan(project, TransformInputs(primary=(beat,)))
    second = narration.execute(project, plan2, skel2).annotations[0]
    assert second.id == first.id
    assert len(nw.annotations_at_tier(project.root, "narration-renders")) == 1


def test_narration_render_reports_real_cost(
    project, script_and_source, patched_synthesis, monkeypatch
):
    # The whole point of the cost model: an actual synthesis reports real dollars
    # on both the TransformResult and the produced Artifact (not a fake 0.0).
    import nw
    from nw import TransformInputs
    from braidio.cost import RATE_ENV_VAR, tts_cost_usd

    monkeypatch.delenv(RATE_ENV_VAR, raising=False)  # default per-char rate

    script, source = script_and_source
    braidio.transforms.ingest_script(project, script, source=source)
    beat = nw.annotations_at_tier(project.root, "narrative-beats")[0]
    voice = nw.get_transform("beat_to_voice_assignment.default")
    narration = nw.get_transform("narration_render.tts")

    voice.execute(project, *voice.plan(project, TransformInputs(primary=(beat,))))
    plan1, skel1 = narration.plan(project, TransformInputs(primary=(beat,)))
    result = narration.execute(project, plan1, skel1)

    expected = tts_cost_usd(beat.body["text"])
    assert expected > 0  # the beat carries real narration text
    assert result.cost_usd_actual == pytest.approx(expected)
    assert result.artifacts[0].cost_usd == pytest.approx(expected)
    assert result.has_unknown_costs is False  # priced: the number is the truth


def test_narration_render_cache_hit_reports_savings(
    project, script_and_source, patched_synthesis, monkeypatch
):
    # On a graph cache hit no synthesis happens ($0 spent), but the avoided cost
    # is reported as cache_hit_savings_usd — symmetric with the spend path.
    import nw
    from nw import TransformInputs
    from braidio.cost import RATE_ENV_VAR, tts_cost_usd

    monkeypatch.delenv(RATE_ENV_VAR, raising=False)
    script, source = script_and_source
    braidio.transforms.ingest_script(project, script, source=source)
    beat = nw.annotations_at_tier(project.root, "narrative-beats")[0]
    voice = nw.get_transform("beat_to_voice_assignment.default")
    narration = nw.get_transform("narration_render.tts")
    voice.execute(project, *voice.plan(project, TransformInputs(primary=(beat,))))

    narration.execute(
        project, *narration.plan(project, TransformInputs(primary=(beat,)))
    )
    hit = narration.execute(
        project, *narration.plan(project, TransformInputs(primary=(beat,)))
    )

    assert hit.artifacts == ()  # nothing produced on a hit
    assert hit.cost_usd_actual == 0.0  # nothing spent
    assert hit.cache_hit_savings_usd == pytest.approx(tts_cost_usd(beat.body["text"]))


def test_narration_render_mixing_cache_hit_reports_zero_actual(
    project, script_and_source, patched_synthesis, monkeypatch
):
    # braidio#8: the graph cache MISSES (fresh project) but mixing's on-disk cache
    # HITS — the synthesis branch runs yet real spend was $0. cost_usd_actual must
    # be 0 (the estimate would over-report), while the Artifact keeps the estimate.
    import nw
    from nw import TransformInputs
    from braidio.cost import RATE_ENV_VAR, tts_cost_usd

    monkeypatch.delenv(RATE_ENV_VAR, raising=False)

    def _cached_narrate(text, out, *, return_cache_status=False, **kw):
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"TTS")
        return (p, True) if return_cache_status else p  # was_cached=True

    monkeypatch.setattr(braidio, "narrate", _cached_narrate)

    script, source = script_and_source
    braidio.transforms.ingest_script(project, script, source=source)
    beat = nw.annotations_at_tier(project.root, "narrative-beats")[0]
    voice = nw.get_transform("beat_to_voice_assignment.default")
    narration = nw.get_transform("narration_render.tts")
    voice.execute(project, *voice.plan(project, TransformInputs(primary=(beat,))))

    result = narration.execute(
        project, *narration.plan(project, TransformInputs(primary=(beat,)))
    )
    expected = tts_cost_usd(beat.body["text"])
    assert expected > 0
    assert result.cost_usd_actual == 0.0  # real spend was $0 (mixing cache hit)
    assert result.cache_hit_savings_usd == pytest.approx(expected)
    assert result.artifacts[0].cost_usd == pytest.approx(expected)  # estimate kept


def test_narration_render_unpriced_cost(
    project, script_and_source, patched_synthesis, monkeypatch
):
    # Rate disabled: spend is honestly unpriced — Artifact.cost_usd is None (never a
    # fake 0.0). nw's cost_usd_actual is a plain float, so the unknown rides on
    # has_unknown_costs: a 0.0 with the flag clear would be the fake zero the
    # cost model forbids (#57 review).
    import nw
    from nw import TransformInputs
    from braidio.cost import RATE_ENV_VAR

    monkeypatch.setenv(RATE_ENV_VAR, "none")
    script, source = script_and_source
    braidio.transforms.ingest_script(project, script, source=source)
    beat = nw.annotations_at_tier(project.root, "narrative-beats")[0]
    voice = nw.get_transform("beat_to_voice_assignment.default")
    narration = nw.get_transform("narration_render.tts")
    voice.execute(project, *voice.plan(project, TransformInputs(primary=(beat,))))
    result = narration.execute(
        project, *narration.plan(project, TransformInputs(primary=(beat,)))
    )

    assert result.artifacts[0].cost_usd is None
    assert result.cost_usd_actual == 0.0
    assert result.has_unknown_costs is True
    # ...and the unknown survives a cache hit: the saving is unknown too
    hit = narration.execute(
        project, *narration.plan(project, TransformInputs(primary=(beat,)))
    )
    assert hit.cost_usd_actual == 0.0 and hit.cache_hit_savings_usd == 0.0
    assert hit.has_unknown_costs is True


def test_commentary_weave_genre_ready():
    import nw

    from braidio.formats import FORMATS

    genre = nw.get_genre("commentary_weave")
    assert genre.is_ready()
    assert genre.projection_entrypoint == "weave_to_episode.default"
    # braidio#6: the genre is self-describing — its 7 Formats are Templates,
    # plus intake_kinds + a cost_profile, so a host can expose it straight from nw.
    assert {t.slug for t in genre.templates} == set(FORMATS)
    assert all(t.params["format_id"] in FORMATS for t in genre.templates)
    assert genre.intake_kinds and genre.cost_profile == "tts"
    assert genre.defaults["format_id"] in FORMATS


# --- structural music in the graph path (braidio#39) ------------------------


def _script_with_break(script):
    """``script`` with a scene break between its first two beats."""
    from braidio import SceneBreak

    beats = list(script.beats)
    return braidio.Script(
        title=script.title,
        id_slug=script.id_slug,
        beats=[beats[0], SceneBreak(label="act 2"), *beats[1:]],
    )


def test_ingest_writes_scene_breaks_in_order(project, script_and_source):
    """The boundary is a node the episode can order between the beats — it used
    to be refused outright (NotImplementedError, no structural tier)."""
    script, source = script_and_source
    ingested = braidio.transforms.ingest_script(
        project, _script_with_break(script), source=source
    )
    assert [k for k, _ in ingested.ordered] == [
        "narration",
        "scene_break",
        "segment",
        "narration",
    ]
    (brk,) = ingested.scene_breaks
    assert brk.tier == "scene-breaks"
    assert brk.body["label"] == "act 2" and brk.body["marker"] is None
    # No structure was declared, so no production-structure node was written.
    assert ingested.structure is None
    assert _tier_ids(project, "production-structures") == set()


def test_ingest_records_the_structure_decision_with_asset_ids(
    project, script_and_source, tmp_path
):
    sting_asset = tmp_path / "hit.mp3"
    sting_asset.write_bytes(b"STING-ASSET")
    bed_asset = tmp_path / "bed.mp3"
    bed_asset.write_bytes(b"BED-ASSET")

    ingested = braidio.transforms.ingest_script(
        project,
        _script_with_break(script_and_source[0]),
        source=script_and_source[1],
        structure=braidio.MusicStructure(
            sting=braidio.Sting(str(sting_asset)), spotlight_clips=True
        ),
        bed=braidio.MusicBed(str(bed_asset)),
    )
    body = ingested.structure.body
    assert ingested.structure.tier == "production-structures"
    assert body["structure"]["spotlight_clips"] is True
    # The assets are recorded as content-addressed ids, not only as locations.
    assert body["sting_asset_id"] and body["bed_asset_id"]
    assert body["sting_asset_id"] != body["bed_asset_id"]
    assert body["sting_url"].endswith("hit.mp3")
    assert body["sting"]["gain_db"] == braidio.Sting(str(sting_asset)).gain_db
    assert "asset_path" not in body["sting"] and "asset_path" not in body["bed"]


def test_weave_project_places_the_break_between_the_renders(
    project, script_and_source, patched_synthesis, tmp_path
):
    import nw

    sting_asset = tmp_path / "hit.mp3"
    sting_asset.write_bytes(b"STING-ASSET")
    script, source = script_and_source
    episode = braidio.weave_project(
        project,
        _script_with_break(script),
        source=source,
        structure=braidio.MusicStructure(sting=braidio.Sting(str(sting_asset))),
    )
    members = [
        m
        for m in nw.iter_all_annotations(project.root)
        if str(m.id) in episode.body["ordered_member_ids"]
    ]
    by_id = {str(m.id): m.tier for m in members}
    assert [by_id[i] for i in episode.body["ordered_member_ids"]] == [
        "narration-renders",
        "scene-breaks",
        "segment-extractions",
        "narration-renders",
    ]
    # The break's audio was prepared into the project (not skipped).
    (brk,) = nw.annotations_at_tier(project.root, "scene-breaks")
    assert (project.root / "data" / "breaks" / f"{brk.id}.mp3").exists()


def test_structure_change_restales_only_the_episode(
    project, script_and_source, patched_synthesis, tmp_path
):
    """Swap the sting and only the episode is stale — the narration renders and
    the extraction keep their audio."""
    import nw

    sting_asset = tmp_path / "hit.mp3"
    sting_asset.write_bytes(b"STING-ASSET")
    script, source = script_and_source
    braidio.weave_project(
        project,
        _script_with_break(script),
        source=source,
        structure=braidio.MusicStructure(sting=braidio.Sting(str(sting_asset))),
    )
    (structure,) = nw.annotations_at_tier(project.root, "production-structures")
    (episode_id,) = _tier_ids(project, "episode-renders")
    _rewrite_in_place(
        project,
        structure,
        body={**structure.body, "sting_asset_id": "a" * 64},
    )
    stale = {a.id for a in nw.stale_after(project.root, structure.id)}
    assert stale == {episode_id}


def test_profile_change_restales_only_the_episode(
    project, script_and_source, patched_synthesis
):
    """The rights profile is a real graph input, so changing it re-stales the
    episode through ordinary freshness — no special case (braidio#47).

    Read this together with ``test_episode_refuses_members_its_profile_forbids``:
    the stale signal says *this episode no longer matches its inputs*, NOT
    "re-run the weave". Because the filter ran at ingest, only re-ingest can
    honour a changed profile, and re-weaving alone is refused rather than
    allowed to mislabel the old cut. Re-ingest is what ``weave_project`` does
    on a second run — see the re-ingest tests below (braidio#51).
    """
    import nw

    script, source = script_and_source
    braidio.weave_project(
        project, script, source=source, profile=braidio.Profile.PERSONAL
    )
    (profile_node,) = nw.annotations_at_tier(project.root, "render-profiles")
    (episode_id,) = _tier_ids(project, "episode-renders")
    _rewrite_in_place(
        project, profile_node, body={**profile_node.body, "profile": "published"}
    )
    stale = {a.id for a in nw.stale_after(project.root, profile_node.id)}
    assert stale == {episode_id}


def test_episode_refuses_members_its_profile_forbids(
    project, script_and_source, patched_synthesis
):
    """A profile flipped after ingest must not produce a mislabelled episode.

    The filtering happens once, at ingest. Re-staling the episode alone
    therefore *invites* the wrong repair: re-run only ``weave_to_episode`` and
    it would weave the members the OLD profile chose while stamping the NEW
    profile's name on them — a published-labelled episode with an owned-local
    clip in it. The transform verifies its members against the profile it is
    about to claim, and refuses (braidio#47 review).
    """
    import nw

    script, source = script_and_source
    braidio.weave_project(
        project, script, source=source, profile=braidio.Profile.PERSONAL
    )
    (profile_node,) = nw.annotations_at_tier(project.root, "render-profiles")
    episode = nw.annotations_at_tier(project.root, "episode-renders")[-1]
    index = {a.id: a for a in nw.iter_all_annotations(project.root)}
    members = tuple(index[UUID(m)] for m in episode.body["ordered_member_ids"])
    _rewrite_in_place(
        project, profile_node, body={**profile_node.body, "profile": "published"}
    )

    with pytest.raises(braidio.RightsViolation) as ei:
        nw.get_transform(braidio.transforms.EPISODE_TRANSFORM).plan(
            project, nw.TransformInputs(primary=members)
        )
    # names the beat and what to do about it, not just "invalid"
    assert "hook" in str(ei.value) and "re-ingest" in str(ei.value)


def test_no_declared_profile_writes_no_render_profile_node(
    project, script_and_source, patched_synthesis
):
    """The absence is load-bearing: an undeclared profile leaves the graph — and
    so the episode's provenance — exactly as it was before braidio#47, while the
    episode still reports the profile the fast path would have used."""
    import nw

    script, source = script_and_source
    episode = braidio.weave_project(project, script, source=source)

    assert nw.annotations_at_tier(project.root, "render-profiles") == []
    assert episode.body["profile"] == braidio.DEFAULT_PROFILE.value


def test_weave_project_applies_a_format_and_its_declared_structure(
    project, script_and_source, patched_synthesis
):
    """The reelee-shaped path: hand the genre's Format to the graph driver and
    its declared structure lands in the graph (braidio#39)."""
    import nw

    script, source = script_and_source
    braidio.weave_project(
        project, _script_with_break(script), source=source, fmt=braidio.SOLO_EXPLAINER
    )
    (structure,) = nw.annotations_at_tier(project.root, "production-structures")
    assert structure.body["structure"] == {
        "scene_marker": braidio.SOLO_EXPLAINER.structure.scene_marker,
        "spotlight_clips": braidio.SOLO_EXPLAINER.structure.spotlight_clips,
        "pause_s": braidio.SOLO_EXPLAINER.structure.pause_s,
    }
    assert structure.body["sting_asset_id"] is None  # no asset supplied
    (cfg,) = nw.annotations_at_tier(project.root, "weave-configs")
    # the format's narration voice rides in the config now (braidio#115)
    expected = braidio.SOLO_EXPLAINER.weave.with_(
        narration_voice=braidio.SOLO_EXPLAINER.narration_voice
    )
    assert cfg.body["config"] == expected.to_dict()


# --- re-ingest: identity, freshness, retry (braidio#49, braidio#51) ------------


def _authoring_identities(project):
    """``{(tier, identity): id}`` over every authoring node — the graph's shape.

    Fails on a doubled identity, which is the #49 corruption itself.
    """
    import nw
    from braidio.transforms._common import AUTHORING_TIERS, node_identity

    out = {}
    for tier in AUTHORING_TIERS:
        for a in nw.annotations_at_tier(project.root, tier):
            key = (tier, node_identity(a))
            assert key not in out, f"doubled {key} node"
            out[key] = a.id
    return out


#: The identities the 3-beat fixture script ingests to, under any profile that
#: keeps the segment (config + narration, segment (source + clip), narration).
_FIXTURE_IDENTITIES = {
    ("weave-configs", "weave-configs"),
    ("narrative-beats", "0000"),
    ("source-media", "0001"),
    ("audio-clips", "0001"),
    ("narrative-beats", "0002"),
}


def test_first_ingest_stamps_every_node_with_its_identity(project, script_and_source):
    """The identity a re-ingest will match by is on the node from day one:
    the singleton tiers by tier, every beat-derived node by ``beat_id`` —
    including the clip and its source-media, which carry it as of #51."""
    script, source = script_and_source
    ing = braidio.transforms.ingest_script(project, script, source=source)

    ids = _authoring_identities(project)
    assert set(ids) == _FIXTURE_IDENTITIES
    assert ids[("weave-configs", "weave-configs")] == ing.config.id
    assert ids[("narrative-beats", "0000")] == ing.narration_beats[0].id
    assert ids[("narrative-beats", "0002")] == ing.narration_beats[1].id
    (clip,) = ing.audio_clips
    assert ids[("audio-clips", "0001")] == clip.id
    assert clip.body["beat_id"] == "0001"
    assert ids[("source-media", "0001")] == UUID(clip.body["source_node_id"])


def test_reingest_of_the_same_script_writes_nothing(project, script_and_source):
    """Idempotence, observed: same ids, same bodies, same timestamps, still one
    of each singleton. A no-op re-ingest must not re-date a node, or
    ``generated_at_time`` stops meaning "a real change" (the property nw's
    entity upsert protects)."""
    import nw

    script, source = script_and_source
    first = braidio.transforms.ingest_script(project, script, source=source)
    before = {a.id: a for a in nw.iter_all_annotations(project.root)}

    second = braidio.transforms.ingest_script(project, script, source=source)

    after = {a.id: a for a in nw.iter_all_annotations(project.root)}
    assert second.config.id == first.config.id
    assert [a.id for _, a in second.ordered] == [a.id for _, a in first.ordered]
    assert set(after) == set(before)
    for aid, ann in before.items():
        assert after[aid].body == ann.body
        assert (
            after[aid].provenance.generated_at_time == ann.provenance.generated_at_time
        )
    assert len(nw.annotations_at_tier(project.root, "weave-configs")) == 1


def test_reingest_with_changed_profile_restales_through_provenance(
    project, script_and_source, patched_synthesis
):
    """The #51 scenario end to end: a second ``weave_project`` on the same
    project with a different profile is *possible*, and correct.

    The render-profile node is replaced under its own id, so the personal
    episode reads stale through its provenance edge; the clip the published
    profile refuses is removed, so its extraction reads stale too
    (``upstream-missing``); and the new episode carries the new profile with
    no extraction among its members — the label is true (braidio#47).
    """
    import nw

    script, source = script_and_source
    personal = braidio.weave_project(
        project, script, source=source, profile=braidio.Profile.PERSONAL
    )
    (profile_before,) = nw.annotations_at_tier(project.root, "render-profiles")
    (clip_before,) = nw.annotations_at_tier(project.root, "audio-clips")
    (extraction_before,) = nw.annotations_at_tier(project.root, "segment-extractions")

    published = braidio.weave_project(
        project, script, source=source, profile=braidio.Profile.PUBLISHED
    )

    # one profile node, same identity, new value — the change is on the edge
    (profile_after,) = nw.annotations_at_tier(project.root, "render-profiles")
    assert profile_after.id == profile_before.id
    assert profile_after.body["profile"] == "published"
    assert profile_after.body["dropped"] == ["hook"]
    stale = {a.id for a in nw.stale_after(project.root, profile_after.id)}
    assert personal.id in stale
    assert published.id not in stale

    # the refused clip is gone, and what derived from it knows
    assert nw.annotations_at_tier(project.root, "audio-clips") == []
    assert nw.annotations_at_tier(project.root, "source-media") == []
    verdicts = {v.annotation.id: v for v in nw.stale_verdicts_all(project.root)}
    assert verdicts[extraction_before.id].is_stale
    assert verdicts[extraction_before.id].reason == nw.freshness.REASON_UPSTREAM_MISSING
    assert verdicts[personal.id].is_stale
    assert not verdicts[published.id].is_stale

    # the new episode is honestly labelled: published, and no extraction in it
    assert published.body["profile"] == "published"
    index = {a.id: a for a in nw.iter_all_annotations(project.root)}
    member_tiers = {index[UUID(m)].tier for m in published.body["ordered_member_ids"]}
    assert member_tiers == {"narration-renders"}
    assert clip_before.id not in index


def test_reingest_with_changed_config_restales_every_render(
    project, script_and_source, patched_synthesis
):
    """The weave-config is replaced in place too, so every render that derived
    from it — voice, narration, extraction, episode — reads stale, while the
    unchanged beats keep their ids (and so their renders' provenance)."""
    import nw

    script, source = script_and_source
    first = braidio.weave_project(project, script, source=source)
    render_tiers = (
        "voice-assignments",
        "narration-renders",
        "segment-extractions",
        "episode-renders",
    )
    renders_before = {tier: _tier_ids(project, tier) for tier in render_tiers}
    beats_before = _tier_ids(project, "narrative-beats") | _tier_ids(
        project, "audio-clips"
    )

    second = braidio.weave_project(
        project, script, source=source, config=braidio.WeaveConfig(crossfade_s=0.5)
    )

    (cfg,) = nw.annotations_at_tier(project.root, "weave-configs")
    assert cfg.body["config"]["crossfade_s"] == 0.5
    stale = {a.id for a in nw.stale_after(project.root, cfg.id)}
    for tier, ids in renders_before.items():
        assert ids <= stale, tier
    assert first.id in stale
    # the beats did not change → same nodes, nothing rewritten
    beats_after = _tier_ids(project, "narrative-beats") | _tier_ids(
        project, "audio-clips"
    )
    assert beats_after == beats_before
    # ...and the RETURNED episode is current: its members were completed
    # against the new config (cache-keyed audio, fresh provenance), so
    # nothing the caller was just handed reads stale (#56 review, nit 2).
    stale_now = {a.id for a in nw.all_stale(project.root)}
    assert second.id not in stale_now
    assert second.id != first.id
    assert not any(UUID(m) in stale_now for m in second.body["ordered_member_ids"])


def test_failed_plan_leaves_the_graph_untouched(project, script_and_source):
    """Validate before mutating (braidio#49): an unresolvable segment fails
    before the first write, so nothing is left behind — not even the config
    node that used to make the project un-ingestable."""
    import nw

    script, _ = script_and_source

    class _Unresolvable:
        def resolve(self, reference):
            return None

    with pytest.raises(ValueError, match="could not resolve"):
        braidio.transforms.ingest_script(project, script, source=_Unresolvable())
    assert list(nw.iter_all_annotations(project.root)) == []


def test_missing_sting_asset_fails_before_the_first_write(project, script_and_source):
    """The door #45 opened (a sting path that does not exist) now fails in the
    plan, while hashing the asset, with the graph still empty."""
    import nw
    from braidio.structure import MusicStructure, Sting

    script, source = script_and_source
    with pytest.raises(FileNotFoundError):
        braidio.transforms.ingest_script(
            project,
            script,
            source=source,
            structure=MusicStructure(sting=Sting(str(project.root / "nope.mp3"))),
        )
    assert list(nw.iter_all_annotations(project.root)) == []


def test_retry_after_an_injected_mid_commit_failure_converges(
    project, script_and_source, monkeypatch
):
    """A crash *during* the commit — after the config node, before the beats —
    is the #49 shape. The write sequence is idempotent, so the retry does not
    double the config: it keeps the node already there and finishes the rest,
    ending in exactly the graph a clean first ingest would have produced."""
    import nw
    from braidio.transforms._common import singleton

    script, source = script_and_source
    real_add = project.graph.add_annotation
    calls = {"n": 0}

    def _flaky(ann):
        calls["n"] += 1
        if calls["n"] == 3:  # config + first beat written; then the crash
            raise RuntimeError("injected: disk full")
        real_add(ann)

    monkeypatch.setattr(project.graph, "add_annotation", _flaky)
    with pytest.raises(RuntimeError, match="injected"):
        braidio.transforms.ingest_script(project, script, source=source)
    half_written = _authoring_identities(project)
    assert ("weave-configs", "weave-configs") in half_written
    assert set(half_written) < _FIXTURE_IDENTITIES
    monkeypatch.setattr(project.graph, "add_annotation", real_add)

    ing = braidio.transforms.ingest_script(project, script, source=source)

    assert len(nw.annotations_at_tier(project.root, "weave-configs")) == 1
    whole = _authoring_identities(project)
    assert set(whole) == _FIXTURE_IDENTITIES
    # what survived the crash kept its id — the retry reconciled, not re-minted
    for key, aid in half_written.items():
        assert whole[key] == aid
    assert singleton(project, "weave-configs").id == ing.config.id


def test_a_doubled_singleton_tier_is_repaired_by_reingest(project, script_and_source):
    """A project already stuck in the pre-#49 state — two weave-config nodes —
    is reconciled by the next ingest: the earliest node (the one existing
    renders derive from) keeps its id, the stray is removed, and the
    ``singleton`` reads that used to raise ``found 2`` work again."""
    import nw
    from lacing.time import RationalTime
    from braidio.transforms._common import singleton

    script, source = script_and_source
    first = braidio.transforms.ingest_script(project, script, source=source)
    stray = first.config.model_copy(
        update={
            "id": UUID(int=1),
            "provenance": first.config.provenance.model_copy(
                update={"generated_at_time": RationalTime.now()}
            ),
        }
    )
    project.graph.add_annotation(stray)
    with pytest.raises(ValueError, match="found 2.*re-running"):
        singleton(project, "weave-configs")

    again = braidio.transforms.ingest_script(project, script, source=source)

    assert again.config.id == first.config.id
    assert singleton(project, "weave-configs").id == first.config.id
    assert len(nw.annotations_at_tier(project.root, "weave-configs")) == 1


def test_legacy_clip_without_identity_is_rewritten_once(
    project, script_and_source, patched_synthesis
):
    """A clip written before ``beat_id`` existed has no identity: a re-ingest
    cannot claim it, so it is removed and rewritten — the one-time cost of
    upgrading a pre-#51 project, and never a doubled tier. The weave after it
    must then succeed: the extraction's cache-keyed audio is adopted onto a
    node derived from the NEW clip, never the old node whose parents are gone
    (the crash the #56 review reproduced)."""
    import nw

    script, source = script_and_source
    ing = braidio.transforms.ingest_script(project, script, source=source)
    (clip,) = ing.audio_clips
    legacy_body = {k: v for k, v in clip.body.items() if k != "beat_id"}
    _rewrite_in_place(project, clip, body=legacy_body)

    again = braidio.transforms.ingest_script(project, script, source=source)

    (new_clip,) = nw.annotations_at_tier(project.root, "audio-clips")
    assert new_clip.id != clip.id
    assert new_clip.body["beat_id"] == "0001"
    assert again.audio_clips[0].id == new_clip.id

    episode = braidio.weave_project(project, script, source=source)
    _assert_episode_current(project, episode, expect_clip=new_clip.id)


def _assert_episode_current(project, episode, *, expect_clip):
    """``episode`` is fresh, and its one extraction member derives from
    ``expect_clip`` — the clip the CURRENT ingest wrote."""
    import nw

    index = {a.id: a for a in nw.iter_all_annotations(project.root)}
    stale = {a.id for a in nw.all_stale(project.root)}
    assert episode.id not in stale
    members = [index[UUID(m)] for m in episode.body["ordered_member_ids"]]
    assert not any(m.id in stale for m in members)
    (extraction,) = [m for m in members if m.tier == "segment-extractions"]
    assert expect_clip in extraction.provenance.was_derived_from


def _ingest_as_written_by_0_0_34(project, script, source):
    """Write the authoring layer the way braidio 0.0.34 (commit 06affa5) did.

    The shape that build persisted, reproduced literally rather than by
    calling that code: config node first, one fresh uuid per node, and
    ``source-media`` / ``audio-clip`` bodies WITHOUT ``beat_id`` (the field
    arrived in #56). Every guest project on the hosted connector written
    before #56 has exactly this shape, so this is the fixture the upgrade
    path is tested against. Returns the nodes in the order the old
    ``weave_project`` drove them.
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
        if isinstance(beat, braidio.Narration):
            ann = _write(
                "narrative-beats",
                {
                    "beat_id": f"{i:04d}",
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


def test_reweave_of_a_project_written_by_the_previous_build(
    project, script_and_source, patched_synthesis
):
    """The connector's real upgrade path: a project written and woven by
    0.0.34 is re-woven by this build. Ingest rewrites the identity-less clip,
    and the weave must complete — the old extraction's audio is adopted onto
    a node derived from the new clip instead of the old node (whose parents
    no longer exist) being handed to the episode."""
    import nw
    from braidio.transforms import _run

    script, source = script_and_source
    legacy = _ingest_as_written_by_0_0_34(project, script, source)
    # the 0.0.34 weave: the same transforms, driven the way that build did
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
    (old_clip,) = nw.annotations_at_tier(project.root, "audio-clips")
    assert "beat_id" not in old_clip.body  # the pre-#56 shape, as intended

    episode = braidio.weave_project(project, script, source=source)

    (new_clip,) = nw.annotations_at_tier(project.root, "audio-clips")
    assert new_clip.id != old_clip.id
    assert episode.id != old_episode.id
    _assert_episode_current(project, episode, expect_clip=new_clip.id)
    # the audio was not re-cut: the extraction member carries the old artifact
    index = {a.id: a for a in nw.iter_all_annotations(project.root)}
    (old_extraction,) = [
        m
        for m in (index[UUID(x)] for x in old_episode.body["ordered_member_ids"])
        if m.tier == "segment-extractions"
    ]
    (new_extraction,) = [
        m
        for m in (index[UUID(x)] for x in episode.body["ordered_member_ids"])
        if m.tier == "segment-extractions"
    ]
    assert new_extraction.body["artifact_id"] == old_extraction.body["artifact_id"]


def test_a_noop_reweave_adds_no_nodes_and_returns_the_same_episode(
    project, script_and_source, patched_synthesis
):
    """Ingest is idempotent, and so is the weave: a second ``weave_project``
    with nothing changed reuses every voice-assignment, render and the
    episode itself (same parents, same planned value, verified fresh) rather
    than appending a new set each run (#56 review, nit 3)."""
    import nw

    script, source = script_and_source
    first = braidio.weave_project(project, script, source=source)
    before = {a.id for a in nw.iter_all_annotations(project.root)}

    second = braidio.weave_project(project, script, source=source)

    assert second.id == first.id
    assert {a.id for a in nw.iter_all_annotations(project.root)} == before


# --- artifact-tier lineage, by declaration (nw#55) ------------------------------


def _weave_with_sting_and_bed(project, script_and_source, tmp_path):
    sting_asset = tmp_path / "hit.mp3"
    sting_asset.write_bytes(b"STING-ASSET")
    bed_asset = tmp_path / "bed.mp3"
    bed_asset.write_bytes(b"BED-ASSET")
    script, source = script_and_source
    return braidio.weave_project(
        project,
        _script_with_break(script),
        source=source,
        structure=braidio.MusicStructure(sting=braidio.Sting(str(sting_asset))),
        bed=braidio.MusicBed(str(bed_asset)),
    )


def test_the_episode_derives_from_the_sting_and_bed_bytes(
    project, script_and_source, patched_synthesis, tmp_path
):
    """The declared ids land in the episode's provenance and it still reads fresh."""
    import nw

    episode = _weave_with_sting_and_bed(project, script_and_source, tmp_path)
    (structure,) = nw.annotations_at_tier(project.root, "production-structures")
    parents = episode.provenance.was_derived_from

    assert structure.body["sting_asset_id"] in parents
    assert structure.body["bed_asset_id"] in parents
    assert structure.id in parents
    verdicts = {v.annotation.id: v for v in nw.stale_verdicts_all(project.root)}
    assert not verdicts[episode.id].is_stale, verdicts[episode.id]


def test_source_media_paths_are_not_declared_as_asset_ids(
    project, script_and_source, patched_synthesis, tmp_path
):
    """``source-media/v1.asset_id`` is a filesystem path: declaring it would make
    every segment extraction raise at plan time. No extraction names an asset."""
    import nw
    from nw.transforms import asset_refs_of

    from braidio.bodies import SOURCE_MEDIA_V1
    from braidio.transforms._asset_refs import ASSET_REF_FIELDS

    _weave_with_sting_and_bed(project, script_and_source, tmp_path)
    assert SOURCE_MEDIA_V1 not in ASSET_REF_FIELDS
    for media in (
        a
        for a in nw.iter_all_annotations(project.root)
        if a.body_schema_uri == SOURCE_MEDIA_V1
    ):
        assert asset_refs_of(media) == ()
    for extraction in nw.annotations_at_tier(project.root, "segment-extractions"):
        assert all(isinstance(p, UUID) for p in extraction.provenance.was_derived_from)


def test_an_episode_woven_before_the_declaration_is_reused_not_re_rendered(
    project, script_and_source, patched_synthesis, tmp_path
):
    """``fresh_equivalent`` compares annotation parents only. A whole-list compare
    never matches a node written before the declaration (it names no assets), so
    the first re-weave after upgrading would add a second episode-render."""
    import nw
    from nw.transforms import register_asset_refs

    from braidio.bodies import PRODUCTION_STRUCTURE_V1
    from braidio.transforms._asset_refs import declare_asset_refs

    register_asset_refs(PRODUCTION_STRUCTURE_V1, lambda body: ())  # "declares nothing"
    try:
        before = _weave_with_sting_and_bed(project, script_and_source, tmp_path)
    finally:
        declare_asset_refs()
    assert all(isinstance(p, UUID) for p in before.provenance.was_derived_from)

    after = _weave_with_sting_and_bed(project, script_and_source, tmp_path)

    assert after.id == before.id
    assert [a.id for a in nw.annotations_at_tier(project.root, "episode-renders")] == [
        before.id
    ]


# --- genre_status: the commentary status a host's project_status serves (braidio#125)


def test_genre_status_follows_the_project_from_empty_to_woven(
    project, script_and_source, patched_synthesis
):
    import nw

    from braidio.project import NEXT_STEP_DONE, NEXT_STEP_SAVE_SCRIPT, NEXT_STEP_WEAVE

    fresh = project.genre_status()
    assert fresh["episodes"] == [] and fresh["next_step"] == NEXT_STEP_SAVE_SCRIPT
    assert not any(v for k, v in fresh.items() if k not in ("episodes", "next_step"))

    script, source = script_and_source
    braidio.transforms.ingest_script(project, script, source=source)
    saved = project.genre_status()
    assert (saved["narration_beats"], saved["clips"]) == (2, 1)
    assert saved["narration_renders"] == 0 and saved["next_step"] == NEXT_STEP_WEAVE

    episode = braidio.weave_project(project, script, source=source)
    woven = project.genre_status()
    assert woven["narration_renders"] == 2 and woven["segment_extractions"] == 1
    assert woven["episodes"] == [
        {"episode_id": str(episode.id), "profile": "personal", "duration_s": 2.0}
    ]
    assert woven["next_step"] == NEXT_STEP_DONE
    # what a host serves: nw's guard passes it through unchanged (JSON, small,
    # and no string naming the project root — the episode's file:// url is left out)
    assert nw.genre_status_of(project) == woven
    assert str(project.root) not in repr(woven)
