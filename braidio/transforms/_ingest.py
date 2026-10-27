"""Ingest a braidio :class:`~braidio.script.Script` into an nw project graph.

The script is first filtered through :func:`braidio.rights.plan_production` —
the *same* rights filter the no-graph fast path
(:func:`braidio.render.render_production`) runs, not a second copy of it — so a
beat the profile refuses is never ingested, never extracted, and never paid
for, and a beat it substitutes is ingested as the substitute
(thorwhalen/braidio#47).

This writes the **authoring** layer the render Transforms consume:

- one ``render-profile/v1`` snapshot when the production declares a profile —
  the rights decision plus what it dropped/substituted;
- one ``weave-config/v1`` snapshot (the singleton render-choices node);
- one ``production-structure/v1`` snapshot when the production declares
  structural music (stings / fade-to-spotlight / a music bed) — written only
  then, so a format that declares none leaves the graph untouched;
- one ``narrative-beat/v1`` per :class:`~braidio.script.Narration`;
- one ``dialogue-beat/v1`` per :class:`~braidio.script.Dialogue`, plus — only
  when there is at least one — the singleton ``dialogue-cast/v1`` recording
  which voice each role speaks with (thorwhalen/braidio#46);
- one ``scene-break/v1`` per :class:`~braidio.script.SceneBreak` — the
  structural boundary, ordered with the rest of the spine (thorwhalen/braidio#39);
- a ``source-media/v1`` + ``audio-clip/v1`` pair per
  :class:`~braidio.script.SegmentBeat` (its playable window resolved via a
  :class:`~braidio.sources.SegmentSource`).

Everything is written **through** ``project.graph.add_annotation`` so the
whole pipeline (authoring → render) lives in one graph that
``nw.stale_after`` can traverse.

Two phases, and the order is the point (thorwhalen/braidio#49, #51)
----------------------------------------------------------------------

**Plan** is pure: the rights plan, every ``SegmentSource.resolve``, every
asset content-hash and every dialogue role's lookup in the cast all happen
before anything is written, so a failure leaves the graph exactly as it was.
**Commit** writes
*by identity* under the rule stated once in
:data:`braidio.transforms._common.SINGLETON_TIERS`: a node whose identity
already exists is replaced under its **existing annotation id** (and not
written at all when its value is unchanged), a node whose identity the new
plan no longer names is removed, and a tier that was doubled by an earlier
half-written ingest is reconciled back to one. Same-id replacement is what
carries the change to the render nodes — their verifying traces record each
parent's value digest, so a rewritten config or profile reads
``upstream-changed`` and a removed beat ``upstream-missing`` — and it is what
makes the whole write idempotent: a retry after a failure converges instead
of doubling, and re-ingesting the same script twice writes nothing.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass

from lacing import Annotation, MediaRef, TimeInterval

from braidio.conversation import DEFAULT_CAST, ConversationCast
from braidio.rights import (
    DEFAULT_PROFILE,
    PUBLISHABLE_CLIP_RIGHTS,
    Profile,
    RenderPlan,
    RightsPolicy,
    plan_production,
)
from braidio.script import Script, SegmentBeat
from braidio.tts import tts_backend
from braidio.weave_config import WeaveConfig
from braidio.bodies._domain import (
    NARRATIVE_BEAT_V1,
    NarrativeBeatBodyV1,
    DIALOGUE_BEAT_V1,
    DialogueBeatBodyV1,
    SCENE_BREAK_V1,
    SceneBreakBodyV1,
    AUDIO_CLIP_V1,
    AudioClipBodyV1,
)
from braidio.bodies._render_nodes import (
    WEAVE_CONFIG_V1,
    WeaveConfigBodyV1,
    PRODUCTION_STRUCTURE_V1,
    ProductionStructureBodyV1,
    RENDER_PROFILE_V1,
    RenderProfileBodyV1,
    DIALOGUE_CAST_V1,
    DialogueCastBodyV1,
    SOURCE_MEDIA_V1,
    SourceMediaBodyV1,
)
from braidio.transforms._common import (
    AUTHORING_TIERS,
    TIER_WEAVE_CONFIG,
    TIER_PRODUCTION_STRUCTURE,
    TIER_RENDER_PROFILE,
    TIER_DIALOGUE_CAST,
    TIER_NARRATIVE_BEAT,
    TIER_DIALOGUE_BEAT,
    TIER_SCENE_BREAK,
    TIER_SOURCE_MEDIA,
    TIER_AUDIO_CLIP,
    asset_ref,
    beat_id,
    node_identity,
    node_ref,
    _RATE,
)

#: ``PlannedBeat.kind`` → the tier its authoring node lives at.
_TIER_OF_KIND = {
    "narration": TIER_NARRATIVE_BEAT,
    "dialogue": TIER_DIALOGUE_BEAT,
    "clip": TIER_AUDIO_CLIP,
    "scene_break": TIER_SCENE_BREAK,
}
#: ``PlannedBeat.kind`` → the kind label :attr:`IngestedScript.ordered` uses.
_ORDERED_KIND = {
    "narration": "narration",
    "dialogue": "dialogue",
    "clip": "segment",
    "scene_break": "scene_break",
}


@dataclass(frozen=True)
class IngestedScript:
    """Handles to the authoring nodes an ingest wrote, in script order."""

    config: Annotation
    narration_beats: tuple[Annotation, ...]
    audio_clips: tuple[Annotation, ...]
    scene_breaks: tuple[Annotation, ...] = ()
    #: The singleton ``production-structure/v1`` node, when the production
    #: declared structural music; ``None`` when it declared none.
    structure: Annotation | None = None
    #: ``(kind, authoring_annotation)`` in script order — ``kind`` is
    #: ``"narration"``, ``"dialogue"``, ``"segment"`` or ``"scene_break"``.
    #: The episode is assembled in this order.
    ordered: tuple[tuple[str, Annotation], ...] = ()
    #: The singleton ``render-profile/v1`` node, when the production declared a
    #: rights profile; ``None`` when it declared none.
    render_profile: Annotation | None = None
    #: The rights plan the ingest actually followed — its ``dropped`` /
    #: ``substituted`` are what the profile refused and swapped.
    plan: RenderPlan | None = None
    #: The ``dialogue-beat/v1`` nodes, in script order.
    dialogue_beats: tuple[Annotation, ...] = ()
    #: The singleton ``dialogue-cast/v1`` node, when the script has a dialogue
    #: beat to cast; ``None`` when it has none (thorwhalen/braidio#46).
    cast: Annotation | None = None


@dataclass(frozen=True)
class _AuthoringNode:
    """One authoring node the ingest intends to write — planned, not yet in the graph.

    ``identity`` is what a re-ingest matches it by (see
    :func:`~braidio.transforms._common.node_identity`). ``reference`` is
    ``None`` for nodes whose reference is incidental (a fresh
    :func:`~braidio.transforms._common.node_ref`, or the existing node's when
    there is one) and a :class:`lacing.MediaRef` when it carries the playable
    window. ``link`` names a body field to fill with the committed id of
    another planned node — the clip's ``source_node_id`` — so the plan can be
    built without knowing which ids the commit will keep.
    """

    tier: str
    identity: str
    body: dict
    body_schema_uri: str
    reference: MediaRef | None = None
    link: tuple[str, str, str] | None = None  # (body field, tier, identity)


def ingest_script(
    project,
    script: Script,
    *,
    config: WeaveConfig | None = None,
    source=None,
    structure=None,
    bed=None,
    profile: Profile | None = None,
    rights: RightsPolicy | None = None,
    cast: ConversationCast | None = None,
) -> IngestedScript:
    """Write ``script``'s authoring nodes into ``project``'s graph.

    ``config`` defaults to a plain :class:`WeaveConfig`. ``source`` (a
    :class:`~braidio.sources.SegmentSource`) is required iff the script has
    :class:`SegmentBeat`\\ s — it resolves each reference to a playable window.

    ``cast`` (a :class:`~braidio.conversation.ConversationCast`) is the
    production's dialogue cast — which voice each :class:`~braidio.script.Dialogue`
    role speaks with — and defaults to :data:`~braidio.conversation.DEFAULT_CAST`,
    exactly as on :func:`braidio.render.render_production`. It is recorded as
    one ``dialogue-cast/v1`` node **only when the script has a dialogue beat**
    (there is nothing to cast otherwise), so a script without dialogue leaves
    the tier empty whatever cast was passed; every dialogue render derives
    from that node, so a recast re-stales the dialogue renders and nothing
    else. A turn whose role the cast does not name fails here, in the plan,
    before anything is written (thorwhalen/braidio#46).

    ``structure`` (a :class:`~braidio.structure.MusicStructure`, typically a
    format's declared one) and ``bed`` (a :class:`~braidio.music.MusicBed`) are
    the production's structural music. Given either, one
    ``production-structure/v1`` node records the decision — including the
    content-addressed ids of the app-supplied sting/bed assets — so the episode
    transform can play a sting at each scene break and drop the bed under a
    spotlit exhibit. Given neither, nothing is written and the render is
    byte-for-byte what it was before this layer existed.

    ``profile`` (a :class:`~braidio.rights.Profile`) is the production's rights
    projection and ``rights`` (a :class:`~braidio.rights.RightsPolicy`) the
    caller-injected set of publishable segment rights. The script is filtered
    through :func:`~braidio.rights.plan_production` exactly as
    :func:`braidio.render.render_production` filters it, so a beat the profile
    refuses never reaches the graph — and is therefore never extracted, never
    synthesized and never paid for. ``profile=None`` means *undeclared*: the
    filter still runs, under :data:`~braidio.rights.DEFAULT_PROFILE` (the same
    default the fast path uses), which passes every beat through unchanged, and
    no ``render-profile/v1`` node is written.

    **Re-ingest.** Calling this again on the same project is how a script,
    format or profile change reaches the graph. Nodes are matched by identity
    (the singleton tiers by tier, beats by their zero-padded script index):
    an unchanged node is left alone, a changed one is rewritten under its
    existing id so its renders read stale through provenance, and one the new
    plan no longer names is removed. Nothing is written until the whole plan
    has validated, so a failing source or asset leaves the graph untouched
    and a retry after any failure converges (see the module docstring).
    """
    config = config or WeaveConfig()
    if config.tts:  # an unregistered voice backend fails here, before any write
        tts_backend(config.tts)
    publishable = rights.publishable_clip_rights if rights else PUBLISHABLE_CLIP_RIGHTS
    plan = plan_production(
        script,
        profile if profile is not None else DEFAULT_PROFILE,
        publishable_clip_rights=publishable,
    )

    # Phase 1 — plan. Pure: resolves, hashes and refuses, writes nothing.
    nodes = _plan_nodes(
        script,
        plan,
        config=config,
        source=source,
        structure=structure,
        bed=bed,
        profile=profile,
        publishable=publishable,
        cast=cast if cast is not None else DEFAULT_CAST,
    )
    # Phase 2 — commit, by identity.
    committed = _commit_nodes(project, nodes)

    ordered = tuple(
        (
            _ORDERED_KIND[planned.kind],
            committed[(_TIER_OF_KIND[planned.kind], beat_id(planned.from_index))],
        )
        for planned in plan.beats
    )
    return IngestedScript(
        config=committed[(TIER_WEAVE_CONFIG, TIER_WEAVE_CONFIG)],
        narration_beats=tuple(a for k, a in ordered if k == "narration"),
        audio_clips=tuple(a for k, a in ordered if k == "segment"),
        scene_breaks=tuple(a for k, a in ordered if k == "scene_break"),
        structure=committed.get((TIER_PRODUCTION_STRUCTURE, TIER_PRODUCTION_STRUCTURE)),
        ordered=ordered,
        render_profile=committed.get((TIER_RENDER_PROFILE, TIER_RENDER_PROFILE)),
        plan=plan,
        dialogue_beats=tuple(a for k, a in ordered if k == "dialogue"),
        cast=committed.get((TIER_DIALOGUE_CAST, TIER_DIALOGUE_CAST)),
    )


# --- phase 1: plan -----------------------------------------------------------


def _plan_nodes(
    script: Script,
    plan: RenderPlan,
    *,
    config: WeaveConfig,
    source,
    structure,
    bed,
    profile: Profile | None,
    publishable: frozenset[str],
    cast: ConversationCast,
) -> list[_AuthoringNode]:
    """Every authoring node this ingest will write, validated, in write order.

    Raises on anything the commit could not carry out — an unresolvable
    segment, a missing sting/bed asset, a dialogue role the cast does not
    name — *before* the caller has written a single node
    (thorwhalen/braidio#49).
    """
    nodes = [_config_node(config)]
    structure_node = _structure_node(structure=structure, bed=bed)
    if structure_node is not None:
        nodes.append(structure_node)
    profile_node = _profile_node(profile=profile, publishable=publishable, plan=plan)
    if profile_node is not None:
        nodes.append(profile_node)
    cast_node = _cast_node(cast=cast, plan=plan, tts=config.tts)
    if cast_node is not None:
        nodes.append(cast_node)

    # The rights plan, not the raw script: a beat the profile dropped is simply
    # absent here, and a substituted one arrives already rewritten. ``orig`` is
    # the beat it came from — the authored one still carries the knobs (label,
    # style, marker, spotlight) that the plan does not repeat.
    for planned in plan.beats:
        orig = script.beats[planned.from_index]
        ident = beat_id(planned.from_index)
        if planned.kind == "narration":
            nodes.append(
                _AuthoringNode(
                    tier=TIER_NARRATIVE_BEAT,
                    identity=ident,
                    body=_json(
                        NarrativeBeatBodyV1(
                            beat_id=ident,
                            # the profile's resolved text: the authored narration,
                            # a ``published_text`` rewrite, or a segment's
                            # substitute.
                            text=planned.content,
                            style=getattr(orig, "style", None),
                            voice=getattr(orig, "voice", None),
                            voice_settings=getattr(orig, "voice_settings", None),
                        )
                    ),
                    body_schema_uri=NARRATIVE_BEAT_V1,
                )
            )
        elif planned.kind == "clip":
            nodes.extend(_segment_nodes(orig, beat_id=ident, source=source))
        elif planned.kind == "dialogue":
            nodes.append(
                _AuthoringNode(
                    tier=TIER_DIALOGUE_BEAT,
                    identity=ident,
                    body=_json(
                        DialogueBeatBodyV1(
                            beat_id=ident,
                            turns=_cast_turns(planned.turns, cast=cast, beat=ident),
                            label=getattr(orig, "label", ""),
                        )
                    ),
                    body_schema_uri=DIALOGUE_BEAT_V1,
                )
            )
        elif planned.kind == "scene_break":
            nodes.append(
                _AuthoringNode(
                    tier=TIER_SCENE_BREAK,
                    identity=ident,
                    body=_json(
                        SceneBreakBodyV1(
                            beat_id=ident, label=orig.label, marker=orig.marker
                        )
                    ),
                    body_schema_uri=SCENE_BREAK_V1,
                )
            )
        else:  # pragma: no cover — PlannedBeat.kind is a closed set
            raise TypeError(f"unknown planned-beat kind {planned.kind!r}")
    return nodes


def _config_node(config: WeaveConfig) -> _AuthoringNode:
    """The singleton ``weave-config/v1`` node."""
    return _AuthoringNode(
        tier=TIER_WEAVE_CONFIG,
        identity=TIER_WEAVE_CONFIG,
        body=_json(WeaveConfigBodyV1(config=config.to_dict())),
        body_schema_uri=WEAVE_CONFIG_V1,
    )


def _profile_node(
    *, profile: Profile | None, publishable: frozenset[str], plan: RenderPlan
) -> _AuthoringNode | None:
    """The singleton ``render-profile/v1`` node, or ``None``.

    Absent a declared ``profile`` the production made no rights choice, so
    nothing is written — the same convention the production-structure node
    follows, and what keeps a profile-less legacy project's graph, provenance
    and render exactly as they were.
    """
    if profile is None:
        return None
    return _AuthoringNode(
        tier=TIER_RENDER_PROFILE,
        identity=TIER_RENDER_PROFILE,
        body=_json(
            RenderProfileBodyV1(
                profile=profile.value,
                publishable_clip_rights=tuple(sorted(publishable)),
                dropped=tuple(plan.dropped),
                substituted=tuple(plan.substituted),
            )
        ),
        body_schema_uri=RENDER_PROFILE_V1,
    )


def _cast_node(
    *, cast: ConversationCast, plan: RenderPlan, tts: str | None = None
) -> _AuthoringNode | None:
    """The singleton ``dialogue-cast/v1`` node, or ``None``.

    Written iff the plan has a dialogue beat: the cast is the decision of what
    those turns sound like, and without turns there is nothing to decide — so
    a script without dialogue keeps the graph it had before this node existed,
    whatever cast the caller or format supplied. With dialogue it is always
    written, default cast included, because the dialogue renders must derive
    from the voices they actually used (thorwhalen/braidio#46). ``tts`` (the
    config's registered voice backend, braidio#132) is recorded only when set,
    so an ElevenLabs production keeps the exact body it had.
    """
    if not any(b.kind == "dialogue" for b in plan.beats):
        return None
    body = _json(
        DialogueCastBodyV1(
            roles=dict(cast.roles),
            model_id=cast.model_id,
            settings=dict(cast.settings) if cast.settings is not None else None,
            tts=tts,
        )
    )
    if body["tts"] is None:  # the absence rule
        del body["tts"]
    return _AuthoringNode(
        tier=TIER_DIALOGUE_CAST,
        identity=TIER_DIALOGUE_CAST,
        body=body,
        body_schema_uri=DIALOGUE_CAST_V1,
    )


def _cast_turns(
    turns, *, cast: ConversationCast, beat: str
) -> tuple[tuple[str, str], ...]:
    """``turns`` as ``(role, text)`` pairs, every role checked against ``cast``.

    The lookup ``render_dialogue`` would do at synthesis time, done here in the
    plan instead — so an unknown role fails before the first write, naming the
    roles the cast does have, rather than as a ``KeyError`` inside a paid call.
    """
    pairs = tuple((str(role), str(text)) for role, text in turns or ())
    unknown = sorted({role for role, _ in pairs} - set(cast.roles))
    if unknown:
        raise ValueError(
            f"ingest_script: dialogue beat {beat} uses role(s) {unknown} that the "
            f"cast does not name (cast roles: {sorted(cast.roles)}); use the "
            "format's cast roles as the turn roles, or pass a cast that names them"
        )
    return pairs


def _structure_node(*, structure, bed) -> _AuthoringNode | None:
    """The singleton ``production-structure/v1`` node, or ``None``.

    Absent both a structure and a bed the production declared no structural
    music, so the node is not written at all — that is what keeps a format
    without structure identical to the pre-structure graph (and render).
    Hashing the assets here, in the plan, is what makes a missing sting fail
    before the first write.
    """
    if structure is None and bed is None:
        return None

    from braidio.structure import DEFAULT_STRUCTURE

    structure = structure if structure is not None else DEFAULT_STRUCTURE
    sting_id, sting_url = (
        asset_ref(structure.sting.asset_path) if structure.sting else (None, None)
    )
    bed_id, bed_url = asset_ref(bed.asset_path) if bed else (None, None)
    return _AuthoringNode(
        tier=TIER_PRODUCTION_STRUCTURE,
        identity=TIER_PRODUCTION_STRUCTURE,
        body=_json(
            ProductionStructureBodyV1(
                structure=_knobs(structure, drop="sting"),
                sting=(
                    _knobs(structure.sting, drop="asset_path")
                    if structure.sting
                    else None
                ),
                sting_asset_id=sting_id,
                sting_url=sting_url,
                bed=_knobs(bed, drop="asset_path") if bed else None,
                bed_asset_id=bed_id,
                bed_url=bed_url,
            )
        ),
        body_schema_uri=PRODUCTION_STRUCTURE_V1,
    )


def _knobs(obj, *, drop: str) -> dict:
    """``obj``'s dataclass fields as a dict, minus ``drop`` (its asset field).

    The asset is recorded as a content-addressed id + a URL, never as whatever
    locator the caller happened to be holding.
    """
    from dataclasses import asdict

    return {k: v for k, v in asdict(obj).items() if k != drop}


def _segment_nodes(
    beat: SegmentBeat, *, beat_id: str, source
) -> tuple[_AuthoringNode, _AuthoringNode]:
    """The source-media + audio-clip pair for one segment beat.

    Resolving the reference is the validation: it happens here, in the plan,
    so an unresolvable segment fails before anything is written.
    """
    if source is None:
        raise ValueError(
            "ingest_script: a SegmentSource is required to resolve SegmentBeats "
            f"(beat {beat.reference!r}); pass source=..."
        )
    resolved = source.resolve(beat.reference)
    if resolved is None:
        raise ValueError(
            f"ingest_script: SegmentSource could not resolve {beat.reference!r}"
        )

    label = beat.label or beat.reference
    src = _AuthoringNode(
        tier=TIER_SOURCE_MEDIA,
        identity=beat_id,
        body=_json(
            SourceMediaBodyV1(
                label=label,
                asset_id=str(resolved.asset_path),
                rights=beat.rights,
                beat_id=beat_id,
            )
        ),
        body_schema_uri=SOURCE_MEDIA_V1,
    )
    clip = _AuthoringNode(
        tier=TIER_AUDIO_CLIP,
        identity=beat_id,
        # The playable window lives on the clip's MediaRef interval — the
        # segment-extraction Transform reads start/end from here.
        reference=MediaRef(
            asset_id=str(resolved.asset_path),
            interval=TimeInterval.from_seconds(
                resolved.start_s, resolved.end_s, rate=_RATE
            ),
        ),
        body=_json(
            AudioClipBodyV1(
                # filled at commit with the source-media node's committed id
                source_node_id="",
                label=label,
                rights=beat.rights,
                spotlight=beat.spotlight,
                beat_id=beat_id,
            )
        ),
        body_schema_uri=AUDIO_CLIP_V1,
        link=("source_node_id", TIER_SOURCE_MEDIA, beat_id),
    )
    return src, clip


def _json(body) -> dict:
    """A body model as the dict that round-trips through the store.

    ``mode="json"`` is what comes back from the graph — a tuple field dumps to
    a tuple in python mode but reads back as a list — so comparing a planned
    body against a stored one in python mode would report every unchanged
    node as changed and rewrite it (the same reason nw's entity upsert dumps
    in json mode).
    """
    return body.model_dump(mode="json")


# --- phase 2: commit ---------------------------------------------------------


def _commit_nodes(
    project, nodes: list[_AuthoringNode]
) -> dict[tuple[str, str], Annotation]:
    """Write ``nodes`` into the graph by identity; return ``{(tier, identity): node}``.

    For each planned node the existing node of the same identity (if any) is
    the one it replaces — under the same id, or not at all when its value is
    already what the plan says. A tier doubled by an earlier half-written
    ingest keeps its earliest node's id and drops the rest. Once every
    planned node is in, whatever the plan did not name at an authoring tier
    is removed, traces and all. Every step is a no-op when re-run, so the
    sequence is idempotent even though nw's graph API is per annotation.
    """
    existing = _existing_by_identity(project)
    committed: dict[tuple[str, str], Annotation] = {}
    for node in nodes:
        key = (node.tier, node.identity)
        current, *duplicates = existing.pop(key, None) or [None]
        for dup in duplicates:
            project.graph.remove_annotation(dup.id)

        body = dict(node.body)
        if node.link is not None:
            field, tier, identity = node.link
            body[field] = str(committed[(tier, identity)].id)
        reference = node.reference or (
            current.reference if current is not None else node_ref(node.tier)
        )
        if current is not None and _same_value(
            current,
            body=body,
            reference=reference,
            body_schema_uri=node.body_schema_uri,
        ):
            committed[key] = current  # unchanged: not rewritten, not re-dated
            continue

        ann = Annotation(
            id=current.id if current is not None else uuid.uuid4(),
            tier=node.tier,
            reference=reference,
            body=body,
            body_schema_uri=node.body_schema_uri,
            provenance=_authored_provenance(),
        )
        if current is not None:
            project.graph.remove_annotation(current.id)
        project.graph.add_annotation(ann)
        committed[key] = ann

    for leftovers in existing.values():
        for ann in leftovers:
            project.graph.remove_annotation(ann.id)
    return committed


def _existing_by_identity(project) -> dict[tuple[str, str | None], list[Annotation]]:
    """Every node at an authoring tier, grouped by identity, oldest first.

    Oldest first is what makes a doubled tier reconcile to the node the
    renders already derive from (the first ingest's), so their provenance
    edges survive the repair.
    """
    import nw

    groups: dict[tuple[str, str | None], list[Annotation]] = defaultdict(list)
    for tier in AUTHORING_TIERS:
        for ann in nw.annotations_at_tier(project.root, tier):
            groups[(tier, node_identity(ann))].append(ann)
    for anns in groups.values():
        anns.sort(
            key=lambda a: (a.provenance.generated_at_time.to_seconds(), str(a.id))
        )
    return groups


def _same_value(current: Annotation, *, body: dict, reference, body_schema_uri) -> bool:
    """Whether ``current`` already carries exactly this value (so no write)."""
    return (
        current.body == body
        and current.body_schema_uri == body_schema_uri
        and current.reference == reference
    )


def _authored_provenance():
    """Provenance for a hand-authored (not transform-derived) node."""
    from lacing import Provenance, RationalTime

    return Provenance(
        was_generated_by="braidio:ingest",
        was_attributed_to="agent:braidio",
        was_derived_from=[],
        generated_at_time=RationalTime.now(rate=_RATE),
        activity="ingest",
    )
