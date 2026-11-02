"""Tests for the nw-app layer: body schemas + render provenance + partial
re-render. Skipped entirely if lacing isn't installed (the core stays usable)."""

from __future__ import annotations

from uuid import uuid4

import pytest

import braidio

pytestmark = pytest.mark.skipif(not braidio.HAS_GRAPH, reason="lacing not available")


#: The 17 URIs braidio puts on the wire — 7 domain + 10 render. Pinned as a
#: set, not a count (braidio#48, braidio#51): a count is green when one schema
#: is swapped for another, which is the change that actually breaks a stored
#: graph. The serialized *shape* of each body is pinned in
#: test_body_schema_stability.py; this is the registration smoke check.
EXPECTED_SCHEMA_URIS = frozenset(
    {
        "annot://schema/commentary/v1",
        "annot://schema/source/v1",
        "annot://schema/audio-clip/v1",
        "annot://schema/narrative-beat/v1",
        "annot://schema/dialogue-beat/v1",
        "annot://schema/scene-break/v1",
        "annot://schema/episode/v1",
        "annot://schema/weave-config/v1",
        "annot://schema/production-structure/v1",
        "annot://schema/render-profile/v1",
        "annot://schema/dialogue-cast/v1",
        "annot://schema/source-media/v1",
        "annot://schema/voice-assignment/v1",
        "annot://schema/narration-render/v1",
        "annot://schema/dialogue-render/v1",
        "annot://schema/segment-extraction/v1",
        "annot://schema/episode-render/v1",
        # the picture track (commentary-studio plan §3)
        "annot://schema/still/v1",
        "annot://schema/video-panel/v1",
        "annot://schema/video-cut/v1",
        "annot://schema/label-track/v1",
        # a person's recording standing in for a narration take (braidio#86)
        "annot://schema/take-override/v1",
    }
)


def test_domain_and_render_schemas_registered():
    from lacing.schema import is_registered
    from braidio.bodies import SCHEMA_URIS

    assert set(SCHEMA_URIS) == EXPECTED_SCHEMA_URIS
    assert len(SCHEMA_URIS) == len(EXPECTED_SCHEMA_URIS)  # no duplicates
    for uri in SCHEMA_URIS:
        assert is_registered(uri), uri


def test_domain_bodies_validate():
    from lacing.schema import validate
    from braidio.bodies import COMMENTARY_V1, AUDIO_CLIP_V1, EPISODE_RENDER_V1

    validate(
        {
            "text": "hi",
            "facet": "historical",
            "source_ids": (),
            "generated_by": "human:t",
        },
        COMMENTARY_V1,
    )
    validate(
        {"source_node_id": "x", "label": "l", "rights": "owned-local"}, AUDIO_CLIP_V1
    )
    validate({"profile": "personal", "ordered_member_ids": ("a",)}, EPISODE_RENDER_V1)


def test_record_render_writes_provenance_graph():
    from lacing import MemoryStore

    store = MemoryStore()
    b1, b2 = uuid4(), uuid4()
    ids = braidio.record_render(
        store,
        weave_config={"voices": ["x"], "min_turn": 2},
        beats=[
            {
                "kind": "narration",
                "source_id": b1,
                "cache_key": "k1",
                "duration_s": 3.0,
            },
            {
                "kind": "segment",
                "source_id": b2,
                "cache_key": "k2",
                "start_s": 1.0,
                "end_s": 4.0,
            },
        ],
        profile="personal",
    )
    # config + voice-assignment + narration-render + segment-extraction + episode-render
    assert sum(1 for _ in store.all()) == 5
    assert len(ids["members"]) == 2


def test_partial_rerender_scope():
    from lacing import MemoryStore

    store = MemoryStore()
    b1, b2 = uuid4(), uuid4()
    ids = braidio.record_render(
        store,
        weave_config={"seed": 7},
        beats=[
            {"kind": "narration", "source_id": b1, "cache_key": "k1"},
            {"kind": "segment", "source_id": b2, "cache_key": "k2"},
        ],
    )
    # changing narration beat b1 → its voice-assignment + narration-render + episode
    stale_b1 = braidio.stale_after(store, b1)
    assert ids["episode"] in stale_b1
    assert ids["members"][1] not in stale_b1  # the unrelated segment stays fresh
    # changing the segment source b2 → its extraction + episode, NOT the narration
    stale_b2 = braidio.stale_after(store, b2)
    assert ids["members"][1] in stale_b2 and ids["members"][0] not in stale_b2
    # changing the config → everything derived from it (narration, segment, voice, episode)
    stale_cfg = braidio.stale_after(store, ids["config"])
    assert ids["members"][0] in stale_cfg and ids["members"][1] in stale_cfg
    assert ids["episode"] in stale_cfg


def test_import_braidio_core_does_not_require_graph():
    # HAS_GRAPH may be True here, but the core API must be importable regardless.
    for name in ("Script", "WeaveConfig", "render_production", "weave_timeline"):
        assert hasattr(braidio, name)


def test_every_graph_tier_and_reader_is_public():
    """braidio#146: a host reads the graph through ``braidio.transforms``, never
    a private module. Derived from ``_common``, so a new tier cannot be missed."""
    pytest.importorskip("nw")
    import braidio.transforms as public
    from braidio.transforms import _common, _video_cut

    tiers = {n for n in vars(_common) if n.startswith("TIER_")}
    readers = {"graph_index", "node_ref"}
    picture = {"content_keyed_prepare", "frames_dir", "panel_source", "panel_canvas"}
    for name in tiers | readers:
        assert getattr(public, name) is getattr(_common, name), name
    for name in picture:
        assert getattr(public, name) is getattr(_video_cut, name), name
    assert (tiers | readers | picture) <= set(public.__all__)
    # reelee's adapter (reelee/_braidio.py) imports these private names until
    # reeleehq/braidio#156: they must stay the very public objects, not copies.
    for private in ("_content_keyed_prepare", "_frames_dir", "_panel_source"):
        assert getattr(_video_cut, private) is getattr(public, private[1:]), private
