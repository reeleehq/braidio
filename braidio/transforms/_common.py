"""Shared helpers for braidio's ``nw.Transform`` pipeline.

The transforms in this package turn braidio's authoring graph (narrative
beats, dialogue beats, audio clips, scene breaks, a weave-config +
production-structure + dialogue-cast snapshot) into render-provenance nodes
(voice-assignment, narration-render, dialogue-render, segment-extraction,
episode-render),
writing each **through** ``project.graph`` so ``nw.stale_after`` traverses
them. This is the whole point of riding nw: one freshness engine over the
project graph — *not* braidio's parallel standalone ``record_render`` store
(which stays as the no-nw fast path in :mod:`braidio.provenance`).

Tier-name constants mirror :mod:`braidio.bodies._tiers`; body schemas live in
:mod:`braidio.bodies`.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from lacing import Annotation, Artifact, NodeRef, TimeInterval

# Tier names (must match braidio.bodies._tiers).
TIER_WEAVE_CONFIG = "weave-configs"
TIER_PRODUCTION_STRUCTURE = "production-structures"
TIER_RENDER_PROFILE = "render-profiles"
TIER_DIALOGUE_CAST = "dialogue-casts"
TIER_SOURCE_MEDIA = "source-media"
TIER_NARRATIVE_BEAT = "narrative-beats"
TIER_DIALOGUE_BEAT = "dialogue-beats"
TIER_SCENE_BREAK = "scene-breaks"
TIER_AUDIO_CLIP = "audio-clips"
TIER_VOICE_ASSIGNMENT = "voice-assignments"
TIER_NARRATION_RENDER = "narration-renders"
TIER_DIALOGUE_RENDER = "dialogue-renders"
TIER_SEGMENT_EXTRACTION = "segment-extractions"
TIER_EPISODE_RENDER = "episode-renders"
TIER_STILL = "stills"
TIER_VIDEO_PANEL = "video-panels"
TIER_VIDEO_CUT = "video-cuts"
TIER_LABEL_TRACK = "label-tracks"

#: Rate for the (incidental) NodeRef intervals on non-media nodes.
_RATE = 1000

#: The provider name braidio's two paid Transforms read from
#: ``execute(secrets=)`` (nw's per-caller credential seam, thorwhalen/braidio#58).
#: braidio owns this name the way nw owns ``nw.FAL_SECRET``.
ELEVENLABS_SECRET = "elevenlabs"


def elevenlabs_key(secrets) -> str | None:
    """The caller's ElevenLabs key from an ``execute(secrets=)`` mapping, or ``None``.

    ``None`` is what :func:`braidio.tts.narrate` / :func:`braidio.render_dialogue`
    read as "resolve from the process environment" — the behaviour every graph
    render had before the seam existed. The key is read here, handed to the
    synthesis boundary as ``api_key``, and reaches nothing that persists: it is
    not an audio-affecting input, so it is deliberately absent from every
    ``cache_key`` (the same text under two keys is the same render).
    """
    if not secrets:
        return None
    return secrets.get(ELEVENLABS_SECRET) or None


#: **The singleton rule, stated once.** At these tiers the *tier itself* is the
#: node's identity: an ingest writes at most one node there, and a re-ingest
#: replaces it **under the same annotation id** rather than adding a second one
#: beside it. Same-id replacement is what lets the change reach the render
#: nodes through provenance — ``nw.stale_after`` compares each parent's current
#: value digest against the one its dependents recorded, so a config or profile
#: rewritten in place reads ``upstream-changed`` downstream, while a second node
#: added beside the first would leave the old renders looking fresh (the
#: failure thorwhalen/braidio#51 exists to prevent). This is nw's own
#: convention for its entity upserts (``identity_key=None``: the tier is the
#: identity). :func:`singleton` / :func:`optional_singleton` read under it and
#: :func:`node_identity` is how the ingest applies it.
SINGLETON_TIERS = frozenset(
    {
        TIER_WEAVE_CONFIG,
        TIER_PRODUCTION_STRUCTURE,
        TIER_RENDER_PROFILE,
        TIER_DIALOGUE_CAST,
    }
)

#: The tiers an ingest owns, in the order it writes them. A re-ingest reconciles
#: exactly these — a node here whose identity the new plan no longer names is
#: removed (its dependents then read ``upstream-missing``); the render tiers
#: are never touched by ingest.
AUTHORING_TIERS = (
    TIER_WEAVE_CONFIG,
    TIER_PRODUCTION_STRUCTURE,
    TIER_RENDER_PROFILE,
    TIER_DIALOGUE_CAST,
    TIER_NARRATIVE_BEAT,
    TIER_DIALOGUE_BEAT,
    TIER_SCENE_BREAK,
    TIER_SOURCE_MEDIA,
    TIER_AUDIO_CLIP,
)

#: Identity-bearing body field on the non-singleton authoring tiers: the
#: zero-padded script index every beat-derived node carries.
BEAT_ID_FIELD = "beat_id"
#: Zero-padding width of a ``beat_id`` (``"0007"``): fixed so ids sort as text
#: in script order. Part of the wire (the voice transform parses it back).
BEAT_ID_WIDTH = 4

#: Body fields a render node gains on completion — its *output*, not part of
#: the value its plan decided. Two render nodes with the same planned value
#: and the same parents are the same decision; whether one already carries
#: an artifact is the completion question, asked separately.
RENDER_OUTPUT_KEYS = frozenset(
    {
        "artifact_id",
        "url",
        "duration_s",
        # the episode's persisted timeline (plan §3) and a video cut's probed
        # geometry + its captions sidecar — all outputs, none of them decided
        # by the plan.
        "timeline",
        "width",
        "height",
        "fps",
        "captions_artifact_id",
    }
)


def beat_id(index: int) -> str:
    """The zero-padded script index — a beat-derived node's identity.

    >>> beat_id(7)
    '0007'
    """
    return f"{index:0{BEAT_ID_WIDTH}d}"


def beat_ordinal(beat: Annotation) -> int:
    """A narrative beat's script position — what casting and pacing seed on."""
    return int(beat.body.get("beat_id", "0") or 0)


def planned_value(ann: Annotation) -> dict:
    """``ann.body`` minus its :data:`RENDER_OUTPUT_KEYS` — what its plan decided.

    JSON-normalised, because a skeleton's body is a python-mode dump (tuples)
    and a stored node's is what came back from the store (lists); the wire
    value is the same and must compare equal.
    """
    import json

    planned = {k: v for k, v in ann.body.items() if k not in RENDER_OUTPUT_KEYS}
    return json.loads(json.dumps(planned, sort_keys=True, default=str))


def annotation_parents(ann: Annotation) -> list:
    """The annotation ids in ``ann``'s ``was_derived_from``, in recorded order.

    ``was_derived_from`` holds annotation ids **and** 64-hex artifact asset ids
    (lacing#31; nw writes the latter for declared schemas, nw#55). Code that
    means "the annotations this node was derived from" reads this half only.
    """
    from lacing.model import partition_provenance_refs

    annotation_ids, _asset_ids = partition_provenance_refs(
        list(ann.provenance.was_derived_from or ())
    )
    return annotation_ids


def fresh_equivalent(project, skeleton: Annotation) -> Annotation | None:
    """An existing node this ``skeleton`` would only duplicate, or ``None``.

    Equivalent means: same tier, the same **annotation** parents, the same
    :func:`planned_value` — and **verified fresh** by nw's freshness walk, so
    its recorded upstream digests still match its parents' current values.
    That last clause is what makes this safe after a re-ingest: a parent
    rewritten in place keeps its id, so an old render still has "the same
    parents" while being exactly the node that must not be reused
    (thorwhalen/braidio#51). Reuse is by *value*, never by id alone.

    This is what makes re-running the weave idempotent — a no-op re-run adds
    no voice-assignment, no render, no episode. It is a full freshness walk
    per call, honest about scale like nw's ``cached_output``: fine at project
    size, and an index behind this signature is the fix if that changes.

    Only the annotation half of ``was_derived_from`` is compared. Since nw#55 a
    declared input schema also contributes the artifact ids its body names
    (``_asset_refs``), so a node written before the declaration has none and a
    whole-list comparison would never match it — one re-plan would re-render
    every such node. Freshness loses nothing by ignoring them: a declared
    asset id is read off a parent's body, so a replaced artifact changes that
    parent's value, which the freshness walk below already refuses. Lineage
    does lose something, deliberately: a reused pre-declaration node keeps
    its old provenance, with no asset edge, until something re-renders it —
    re-rendering instead would mint a new node and orphan whatever was built
    on the old one (video panels, cuts).
    """
    import nw

    parents = set(annotation_parents(skeleton))
    value = planned_value(skeleton)
    candidates = [
        a
        for a in nw.annotations_at_tier(project.root, skeleton.tier)
        if a.id != skeleton.id
        and set(annotation_parents(a)) == parents
        and planned_value(a) == value
    ]
    if not candidates:
        return None
    fresh = {
        v.annotation.id for v in nw.stale_verdicts_all(project.root) if not v.is_stale
    }
    return next((a for a in reversed(candidates) if a.id in fresh), None)


def adopt_output(skeleton: Annotation, hit: Annotation) -> Annotation:
    """``skeleton`` completed with ``hit``'s output — same audio, this provenance.

    A ``cache_key`` hit proves the *audio* is already made; it says nothing
    about whether the node that made it still describes the current graph.
    After a re-ingest the hit may derive from parents that were replaced or
    removed, so returning it would hand the episode a member whose provenance
    dangles (the crash the #56 review reproduced) or reads stale forever. So
    the skeleton — derived from the current parents — is completed with the
    hit's artifact instead: no synthesis, no spend, correct provenance.
    """
    outputs = {k: hit.body[k] for k in RENDER_OUTPUT_KEYS if k in hit.body}
    return skeleton.model_copy(update={"body": {**skeleton.body, **outputs}})


def node_identity(ann: Annotation) -> str | None:
    """``ann``'s ingest identity — what a re-ingest matches it by.

    At a :data:`SINGLETON_TIERS` tier the tier is the identity; elsewhere it
    is the node's ``beat_id``. ``None`` means the node has no identity (an
    ``audio-clip`` / ``source-media`` written before ``beat_id`` existed on
    them): a re-ingest cannot claim it, so it is removed and rewritten.

    >>> from lacing import Annotation, Provenance, RationalTime
    >>> def _node(tier, body):
    ...     return Annotation(
    ...         id=uuid.uuid4(), tier=tier, reference=node_ref(tier), body=body,
    ...         body_schema_uri="annot://schema/x/v1",
    ...         provenance=Provenance(
    ...             was_generated_by="t", was_attributed_to="t",
    ...             was_derived_from=[], generated_at_time=RationalTime.now(),
    ...             activity="t"),
    ...     )
    >>> node_identity(_node(TIER_WEAVE_CONFIG, {"config": {}}))
    'weave-configs'
    >>> node_identity(_node(TIER_NARRATIVE_BEAT, {"beat_id": "0003", "text": "x"}))
    '0003'
    >>> node_identity(_node(TIER_AUDIO_CLIP, {"label": "legacy clip"})) is None
    True
    """
    if ann.tier in SINGLETON_TIERS:
        return ann.tier
    body = ann.body if isinstance(ann.body, dict) else {}
    value = body.get(BEAT_ID_FIELD)
    return None if value is None else str(value)


def node_ref(tier: str) -> NodeRef:
    """A fresh zero-length :class:`NodeRef` for a node in ``tier``.

    Render/authoring nodes that don't point at a media interval still need a
    ``reference``; a NodeRef keyed by a fresh id is the neutral choice.
    """
    return NodeRef(
        scene_path=f"{tier}/{uuid.uuid4()}",
        interval=TimeInterval.from_seconds(0.0, 1.0, rate=_RATE),
    )


def file_url(path: str | Path) -> str:
    """The ``file://`` URL for a local path (what render bodies store)."""
    return Path(path).resolve().as_uri()


def url_to_path(url: str) -> Path:
    """Local path from a ``file://`` URL (inverse of :func:`file_url`)."""
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme != "file":
        raise ValueError(f"expected a file:// URL, got {url!r}")
    return Path(url2pathname(parsed.path))


def media_path(project_root, body: dict, *, what: str) -> Path:
    """The local file holding a body's media — found **by content first**.

    1. ``artifact_id`` → the project's delivery-catalog blob
       (``.reelee/artifacts/blobs/<sha256>``). The id *is* the content hash, so
       the blob is exactly these bytes wherever the project now lives. This is
       what an imported project carries to the server: its recorded urls are
       the importing machine's paths (thorwhalen/braidio#89).
    2. the recorded ``url``, for media never registered in a catalog (a
       project braidio wove itself, a motion pass).

    Deliberately **no basename guess**: a filename says nothing about the
    bytes, and a still re-fetched under the same name would be served as the
    old one. The error names every place looked.
    """
    from braidio.importing._catalog import _IS_DIGEST, HostArtifactCatalog

    tried = []
    artifact_id = str(body.get("artifact_id") or "")
    if _IS_DIGEST.fullmatch(artifact_id):
        blob = HostArtifactCatalog(project_root).blob_path(artifact_id)
        if blob is not None:
            return blob
        tried.append(f"catalog blob {artifact_id[:12]}…")
    url = body.get("url")
    if url:
        path = url_to_path(url)
        if path.exists():
            return path
        tried.append(str(path))
    if not tried:
        raise ValueError(f"{what}: no artifact_id or url — nothing to read")
    raise FileNotFoundError(f"{what}: missing — looked for {'; '.join(tried)}")


def graph_index(project) -> dict:
    """``{annotation.id: annotation}`` across the whole project graph.

    ``Transform.execute`` receives no ``inputs`` — it re-resolves what it
    needs from the graph, keyed by the ids recorded in the skeleton's
    ``provenance.was_derived_from``.
    """
    import nw

    return {a.id: a for a in nw.iter_all_annotations(project.root)}


_DOUBLED_TIER_ADVICE = (
    " — a singleton tier holds one node per project (see SINGLETON_TIERS); "
    "re-running ingest_script / weave_project on this project reconciles it "
    "back to one"
)


def singleton(project, tier: str) -> Annotation:
    """The one annotation at ``tier`` (raises if there isn't exactly one).

    Reads under :data:`SINGLETON_TIERS`: the tier is the identity, so more
    than one node here is a half-written ingest (thorwhalen/braidio#49), and
    the error says how to repair it rather than only that it is wrong.
    """
    import nw

    anns = nw.annotations_at_tier(project.root, tier)
    if len(anns) != 1:
        raise ValueError(
            f"expected exactly one {tier!r} node in the project graph, "
            f"found {len(anns)}" + (_DOUBLED_TIER_ADVICE if anns else "")
        )
    return anns[0]


def optional_singleton(project, tier: str) -> Annotation | None:
    """The one annotation at ``tier``, or ``None`` when the tier is empty.

    The counterpart of :func:`singleton` for a tier a production only writes
    when it asks for something (``production-structures``,
    ``render-profiles``). More than one is still a bug, not a choice — the
    same :data:`SINGLETON_TIERS` rule, with the same repair.
    """
    import nw

    anns = nw.annotations_at_tier(project.root, tier)
    if not anns:
        return None
    if len(anns) > 1:
        raise ValueError(
            f"expected at most one {tier!r} node in the project graph, "
            f"found {len(anns)}" + _DOUBLED_TIER_ADVICE
        )
    return anns[0]


def asset_ref(path: str | Path) -> tuple[str, str]:
    """``(asset_id, file_url)`` for an app-supplied asset (music bed, sting).

    The id is the content-addressed :class:`lacing.Artifact` id of the file's
    bytes, so the graph records *which audio* was used rather than only where it
    sat: replace the file and the id changes, which re-stales what derived from
    it. The URL is what the render actually opens.
    """
    artifact = Artifact.from_path(
        Path(path),
        kind="audio",
        was_generated_by="braidio:ingest",
        was_attributed_to="agent:braidio",
    )
    return artifact.asset_id, file_url(path)


def child_at_tier(project, tier: str, parent_id) -> Annotation:
    """The most recent ``tier`` annotation derived from ``parent_id``."""
    import nw

    hits = [
        a
        for a in nw.annotations_at_tier(project.root, tier)
        if parent_id in a.provenance.was_derived_from
    ]
    if not hits:
        raise ValueError(f"no {tier!r} node derived from {parent_id}")
    return hits[-1]


def resolve_parents(skeleton: Annotation, index: dict) -> list:
    """The annotations ``skeleton`` was derived from, resolved via ``index``."""
    return [index[pid] for pid in skeleton.provenance.was_derived_from if pid in index]


def require_tier(anns, tier: str) -> Annotation:
    """The first annotation in ``anns`` at ``tier`` (raise if none)."""
    for a in anns:
        if a.tier == tier:
            return a
    raise ValueError(
        f"expected a {tier!r} node among derived-from nodes {[a.tier for a in anns]}"
    )


def cached_output(project, tier: str, cache_key: str):
    """An existing completed ``tier`` node with matching ``cache_key`` (or None).

    Non-fal renders (ElevenLabs TTS, ffmpeg extraction) aren't covered by
    falaw's content-addressed cache, so each carries an explicit ``cache_key``
    and compares-and-skips here before doing billable/expensive work. The
    lookup itself is nw's (``nw.transforms.cached_output``, nw#54) — one
    convention, not one per genre; this wrapper only adapts ``project`` to
    its root.
    """
    from nw.transforms import cached_output as transform_cached_output

    return transform_cached_output(project.root, tier, cache_key)


def media_artifact(
    path: str | Path,
    *,
    kind: str,
    transform_name: str,
    derived_from,
    duration_s: float | None = None,
    cost_usd: float | None = None,
    mime: str | None = None,
) -> Artifact:
    """A content-addressed ``lacing.Artifact`` for a file a Transform produced.

    ``cost_usd`` is the cost to produce this file (``None`` = unpriced, never a
    fake ``0.0``); for TTS it is a rate ESTIMATE from
    :func:`braidio.cost.tts_cost_usd` (see that module + thorwhalen/braidio#8).
    """
    return Artifact.from_path(
        Path(path),
        kind=kind,
        was_generated_by=f"transform:{transform_name}",
        was_attributed_to="agent:braidio",
        was_derived_from=tuple(str(x) for x in derived_from),
        duration_s=duration_s,
        cost_usd=cost_usd,
        mime=mime,
    )


def audio_artifact(
    path: str | Path,
    *,
    transform_name: str,
    derived_from,
    duration_s: float | None = None,
    cost_usd: float | None = None,
) -> Artifact:
    """:func:`media_artifact` for audio — the name every audio Transform uses."""
    return media_artifact(
        path,
        kind="audio",
        transform_name=transform_name,
        derived_from=derived_from,
        duration_s=duration_s,
        cost_usd=cost_usd,
    )


def safe_duration(path: str | Path) -> float:
    """Best-effort audio duration in seconds; ``0.0`` if it can't be probed."""
    import braidio

    try:
        return float(braidio.duration_s(path))
    except Exception:  # noqa: BLE001 — duration is advisory; never fail a render on it
        return 0.0
