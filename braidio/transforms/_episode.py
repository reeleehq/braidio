"""``weave_to_episode.default`` — weave member renders into one episode.

A **batch** local-render Transform (N inputs → 1 output): it consumes all the
narration-render + dialogue-render + segment-extraction nodes (in order) —
the two spoken kinds weave alike — and mixes them with
``braidio.weave_timeline`` (duck/crossfade/loudness from the weave-config),
and emits one ``episode-render/v1`` referencing the assembled audio. It
derives from ``[*members, weave-config]``, so any member re-render (or a
config change) re-stales the episode — the top of the partial-re-render DAG.

**Structural music** (thorwhalen/braidio#39) rides the same list: a
``scene-break/v1`` node is a member with no render node of its own, and the
weave turns it into a sting or a pause via ``braidio.structure`` — the same
primitives the no-graph path uses, never a forked DSP. When the project carries
a ``production-structure/v1`` node the episode also derives from it, so
swapping the sting asset or flipping fade-to-spotlight re-stales exactly the
episode; when it does not, nothing about the plan or the render changes.

The **rights profile** rides the same seam (thorwhalen/braidio#47), with one
difference that matters. The episode's ``profile`` used to be the literal
``"personal"``, so a graph render claimed a personal cut whatever was actually
asked for. It is now read from the ``render-profile/v1`` node the ingest wrote,
a declared input of this Transform.

But the filtering happens **once, at ingest**, so — unlike the sting asset — a
changed profile cannot be repaired by re-running this Transform. Re-staling
only the episode is therefore a *hazard*, not the feature it is for the other
inputs: it invites exactly the wrong repair, re-weaving the members the old
profile chose under the new profile's name. So this Transform does not trust
the label. It re-asks :func:`braidio.rights.clip_plays_under` — the same rule
:func:`~braidio.rights.plan_production` applied at ingest, asked again, never
reimplemented — over its clip members, and raises
:class:`~braidio.rights.RightsViolation` when they disagree with the profile it
is about to stamp on them. A profile change is only correctly applied by
re-ingesting the script.

**The timeline is persisted** (commentary-studio plan §3). The graph path used
to produce none — ``weave_timeline`` returns only a ``Path`` — so the cut
points a picture track needs died with the process. ``execute`` now builds a
:class:`~braidio.timeline.TimelineBreakdown` from the *same* members,
durations and layout knobs it hands the mixer, and writes it into
``EpisodeRenderBodyV1.timeline``. For a row written before that field existed,
:func:`episode_timeline` reconstructs it through the same function from
``ordered_member_ids`` + each member's duration — the two traps being that a
``segment-extraction`` stores ``start_s``/``end_s`` of the *unpadded* window
(the rendered clip is longer, by the weave-config's pads and its minimum
length) and a ``scene-break`` carries no duration at all (its pause comes from
``production-structure.structure["pause_s"]``, or its sting from the sting
knobs). Both are answered by probing the member's rendered file where it
still exists — the probe the renderer itself used — and by reproducing the
renderer's arithmetic where it does not.

This is the genre's ``projection_entrypoint``: the step that turns the graph
into the delivered artifact.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from falaw import Plan
from lacing import Annotation
from nw import BaseTransform, TransformInputs, TransformResult, register_transform
from nw.transforms._provenance import derive_provenance

from braidio.weave import TimelineItem
from braidio.timeline import TimelineBreakdown, build_timeline
from braidio.tts import DEFAULT_VOICE_ID
from braidio.weave_config import turn_overlap_of
from braidio.bodies._domain import SCENE_BREAK_V1
from braidio.rights import (
    DEFAULT_PROFILE,
    PUBLISHABLE_CLIP_RIGHTS,
    Profile,
    RightsViolation,
    clip_plays_under,
)
from braidio.bodies._render_nodes import (
    WEAVE_CONFIG_V1,
    PRODUCTION_STRUCTURE_V1,
    RENDER_PROFILE_V1,
    NARRATION_RENDER_V1,
    DIALOGUE_RENDER_V1,
    SEGMENT_EXTRACTION_V1,
    EPISODE_RENDER_V1,
    EpisodeRenderBodyV1,
)
from braidio.transforms._common import (
    TIER_AUDIO_CLIP,
    TIER_NARRATIVE_BEAT,
    TIER_DIALOGUE_BEAT,
    TIER_WEAVE_CONFIG,
    TIER_PRODUCTION_STRUCTURE,
    TIER_RENDER_PROFILE,
    TIER_NARRATION_RENDER,
    TIER_VOICE_ASSIGNMENT,
    TIER_DIALOGUE_RENDER,
    TIER_SEGMENT_EXTRACTION,
    TIER_SCENE_BREAK,
    TIER_EPISODE_RENDER,
    singleton,
    optional_singleton,
    fresh_equivalent,
    graph_index,
    resolve_parents,
    require_tier,
    node_ref,
    audio_artifact,
    safe_duration,
    file_url,
    url_to_path,
)
from braidio.transforms._segment import _pads_fades

NAME = "weave_to_episode.default"

#: Timeline label of a narration beat: a snippet of its text (the fast path's
#: convention in ``braidio.render``), so a span planner can see what is said.
_LABEL_SNIPPET_CHARS = 48

# Weave defaults, applied when the weave-config snapshot omits a knob. They
# mirror braidio.weave.weave_timeline's own signature defaults.
_DEFAULT_CLIP_EDGE_OVERLAP_S = 0.5
_DEFAULT_CROSSFADE_S = 0.12
_DEFAULT_TARGET_LUFS = -16.0
_DEFAULT_TRUE_PEAK = -1.0
_DEFAULT_SAMPLE_RATE = 44100


def _structure_and_bed(node: Annotation | None):
    """``(MusicStructure, MusicBed | None)`` from a ``production-structure`` node.

    ``None`` (no such node) resolves to braidio's inert default structure and no
    bed — the pre-structure behaviour, reconstructed rather than special-cased
    at every use site.
    """
    from braidio.music import MusicBed
    from braidio.structure import DEFAULT_STRUCTURE, MusicStructure, Sting

    if node is None:
        return DEFAULT_STRUCTURE, None
    body = node.body
    sting = None
    if body.get("sting_url"):
        sting = Sting(str(url_to_path(body["sting_url"])), **(body.get("sting") or {}))
    bed = None
    if body.get("bed_url"):
        bed = MusicBed(str(url_to_path(body["bed_url"])), **(body.get("bed") or {}))
    return MusicStructure(sting=sting, **(body.get("structure") or {})), bed


def _layout_knobs(config: dict) -> tuple[float, float]:
    """``(clip_edge_overlap_s, narration_crossfade_s)`` from a weave-config snapshot."""
    return (
        float(config.get("clip_edge_overlap_s", _DEFAULT_CLIP_EDGE_OVERLAP_S)),
        float(config.get("crossfade_s", _DEFAULT_CROSSFADE_S)),
    )


def _member_role(member: Annotation, index: dict, *, structure):
    """``(role, label, source_span)`` for one episode member — the timeline's
    per-beat metadata, mirroring ``braidio.render``'s conventions exactly.

    ``role`` is the aggregation kind (``clip`` / ``narration`` or the beat's
    style / ``dialogue`` / ``sting`` / ``scene-break``); ``build_timeline``
    lays every non-``clip`` role out as spoken, which is what the mixer does.
    """
    parents = resolve_parents(member, index)
    if member.tier == TIER_SCENE_BREAK:
        plays = structure.plays_sting_of(member.body.get("marker"))
        return ("sting" if plays else "scene-break"), member.body.get("label", ""), None
    if member.tier == TIER_SEGMENT_EXTRACTION:
        clip = next((p for p in parents if p.tier == TIER_AUDIO_CLIP), None)
        label = clip.body.get("label", "") if clip is not None else ""
        span = (float(member.body["start_s"]), float(member.body["end_s"]))
        return "clip", label, span
    if member.tier == TIER_DIALOGUE_RENDER:
        beat = next((p for p in parents if p.tier == TIER_DIALOGUE_BEAT), None)
        label = (beat.body.get("label") if beat is not None else "") or "dialogue"
        return "dialogue", label, None
    beat = next((p for p in parents if p.tier == TIER_NARRATIVE_BEAT), None)
    style = (beat.body.get("style") if beat is not None else None) or "narration"
    text = (beat.body.get("text", "") if beat is not None else "") or ""
    return style, text[:_LABEL_SNIPPET_CHARS], None


def _member_speaker(member: Annotation, index: dict) -> str | None:
    """The voice a member talks with, for speaker interruption (``None``: nobody).

    Only a narration render has one — the voice its ``voice-assignment`` parent
    carries. A dialogue render is several speakers in one file, a clip or a
    break has none: the same rule as ``braidio.render``'s ``speakers``.
    """
    if member.tier != TIER_NARRATION_RENDER:
        return None
    va = next(
        (p for p in resolve_parents(member, index) if p.tier == TIER_VOICE_ASSIGNMENT),
        None,
    )
    return (va.body.get("voice_id") or DEFAULT_VOICE_ID) if va is not None else None


def member_timeline(
    members,
    durations,
    index: dict,
    *,
    config: dict,
    structure,
    profile: str,
    title: str = "",
) -> TimelineBreakdown:
    """The episode timeline for ``members`` at ``durations`` — pure.

    One function for both the render (``execute`` calls it with the durations
    it probed for the mixer) and the reconstruction (:func:`episode_timeline`
    calls it with durations recovered from the graph), so the two cannot
    disagree on layout: both go through :func:`braidio.timeline.build_timeline`
    and therefore the same ``layout_placed`` the mixer uses.
    """
    roles, labels, spans = [], [], []
    for member in members:
        role, label, span = _member_role(member, index, structure=structure)
        roles.append(role)
        labels.append(label)
        spans.append(span)
    edge_overlap_s, crossfade_s = _layout_knobs(config)
    speakers = [_member_speaker(m, index) for m in members]
    return build_timeline(
        kinds=roles,
        durations=[float(d) for d in durations],
        labels=labels,
        source_spans=spans,
        clip_edge_overlap_s=edge_overlap_s,
        narration_crossfade_s=crossfade_s,
        speakers=speakers,
        overlap=turn_overlap_of(config),
        title=title,
        settings={
            "weave": dict(config),
            "profile": profile,
            "resolved": {
                "clip_edge_overlap_s": edge_overlap_s,
                "crossfade_s": crossfade_s,
            },
        },
    )


def _member_duration(project, member: Annotation, *, config: dict, structure) -> float:
    """A member's rendered duration, for a row that persisted no timeline.

    The rendered file is the truth where it still exists (its probe is what
    the mixer used); the fallback reproduces the renderer's own arithmetic —
    ``extract_padded``'s pads and floor for a clip, the pause or the trimmed
    sting plus its gap for a break, the body's ``duration_s`` for a spoken take.
    """
    import braidio

    if member.tier == TIER_SCENE_BREAK:
        rendered = project.root / "data" / "breaks" / f"{member.id}.mp3"
        if rendered.exists():
            return float(braidio.duration_s(rendered))
        if structure.plays_sting_of(member.body.get("marker")):
            sting = structure.sting
            length = min(float(braidio.duration_s(sting.asset_path)), sting.max_len_s)
            return length + sting.gap_after_s
        return float(structure.pause_s)

    url = member.body.get("url")
    if url:
        path = url_to_path(url)
        if path.exists():
            return float(braidio.duration_s(path))

    if member.tier == TIER_SEGMENT_EXTRACTION:
        (pre, post), _fades, min_len = _pads_fades(config)
        start = max(0.0, float(member.body["start_s"]) - pre)
        end = float(member.body["end_s"]) + post
        if end - start < min_len:
            end = start + min_len
        return max(0.05, end - start)
    return float(member.body.get("duration_s") or 0.0)


def episode_timeline(project, episode: Annotation) -> TimelineBreakdown:
    """The :class:`~braidio.timeline.TimelineBreakdown` of an ``episode-render``.

    Persisted rows answer from ``body["timeline"]``. A row written before the
    field existed is reconstructed through :func:`member_timeline` from its
    ``ordered_member_ids`` and each member's recovered duration — the same
    layout as the render, so a panel plan over a reconstructed timeline lands
    on the same cut points the mixer used.
    """
    persisted = episode.body.get("timeline")
    if persisted:
        return TimelineBreakdown.from_dict(persisted)

    index = graph_index(project)
    parents = resolve_parents(episode, index)
    cfg = next((p for p in parents if p.tier == TIER_WEAVE_CONFIG), None)
    if cfg is None:
        cfg = singleton(project, TIER_WEAVE_CONFIG)
    config = cfg.body.get("config", {})
    structure, _bed = _structure_and_bed(
        next((p for p in parents if p.tier == TIER_PRODUCTION_STRUCTURE), None)
    )
    members = _resolve_members(
        episode,
        index,
        what="cannot reconstruct its timeline",
        fix="Re-weave the project instead.",
    )
    durations = [
        _member_duration(project, m, config=config, structure=structure)
        for m in members
    ]
    return member_timeline(
        members,
        durations,
        index,
        config=config,
        structure=structure,
        profile=str(episode.body.get("profile", DEFAULT_PROFILE.value)),
        title=_project_title(project),
    )


def _project_title(project) -> str:
    try:
        return str(getattr(project.spec, "title", "") or "")
    except Exception:  # noqa: BLE001 — a title is decoration on a timeline
        return ""


#: Timeline offsets and durations are each rounded to the millisecond, so a
#: lane end (rounded start + rounded duration) and the next start can differ by
#: a little over 1 ms for the same instant.
_PLACEMENT_EPS_S = 2.5e-3


@dataclass(frozen=True)
class MemberSpan:
    """Where one member of an episode is heard on its mix.

    ``start_s`` / ``end_s`` are ``None`` together when placement is unknown: the
    timeline has no span at the member's position, or no timeline can be read
    that pairs with the members. A caller shows that rather than inventing one.

    ``concurrent`` marks a member laid *under* what follows it (a clip with
    ``placement="under"``): it plays at the same time as its neighbours, so its
    interval is the true one and overlaps theirs.
    """

    member_id: str
    position: int
    start_s: Optional[float] = None
    end_s: Optional[float] = None
    concurrent: bool = False


def member_spans(project, episode: Annotation) -> tuple[MemberSpan, ...]:
    """Each member of ``episode``, in play order, with its place on the mix.

    The pairing is the one the captions and the picture track already use:
    ``ordered_member_ids[i]`` is the member behind the timeline span whose
    ``index`` is ``i`` (see :func:`episode_script`). Durations cannot stand in
    for it: the weave lays members out with overlaps, pads and pauses that only
    the timeline records.

    **The sequential members come back exclusive.** The weave tucks each
    sequential member a fraction of a second under the previous one's end (a
    narration crossfade, a clip's faded edge), so a raw span starts before its
    predecessor finishes. Each sequential placement begins where the previous
    sequential one finished, which is how :func:`braidio.captions.cues_for`
    clamps its cues; those intervals are ordered and never overlap.

    **A member laid under what follows keeps its true span** and is marked
    ``concurrent``: it plays at the same time as its neighbours, and squeezing it
    into the sequential lane would place audio that is really heard nowhere. The
    timeline records each span's ``placement``. A breakdown written before that
    field existed is read the way :func:`braidio.weave.layout_placed` lays one
    out: an ``under`` member starts exactly at the end of the sequential lane so
    far, and the sequential member after it starts at or before it (tucked back
    by the crossfade or the clip edge). That reading assumes members longer
    than their tuck; a layout of parts shorter than the clip edge can start
    several of them at 0, which only a recorded placement disambiguates.

    A sequential member shorter than its tuck (a 0.3 s part under a 0.5 s clip
    edge) is heard entirely inside its predecessor's tail; it too keeps its
    true span and is marked ``concurrent`` rather than squeezed to nothing.

    Every interval is bounded by the mix (``duration_s``, when recorded).

    Placement is unknown (all ``None``) when the timeline cannot be read or
    reconstructed, or when its indices are not positions of this member list
    (duplicated, or past its end): pairing by index there would hand each member
    a neighbour's interval.
    """
    member_ids = [str(m) for m in episode.body.get("ordered_member_ids", ())]
    unplaced = tuple(MemberSpan(m, i) for i, m in enumerate(member_ids))
    try:
        beats = episode_timeline(project, episode).beats
    except (KeyError, TypeError, ValueError):  # malformed, or no way to rebuild one
        return unplaced
    indices = [b.index for b in beats]
    if len(set(indices)) != len(indices) or not all(
        0 <= i < len(member_ids) for i in indices
    ):
        return unplaced
    by_index = {b.index: b for b in beats}
    mix_end = float(episode.body.get("duration_s") or 0.0) or None

    def bounded(t: float) -> float:
        t = max(t, 0.0)
        return round(min(t, mix_end) if mix_end is not None else t, 3)

    placed = [p for p in range(len(member_ids)) if p in by_index]
    recorded = all(by_index[p].placement for p in placed)
    later_min: dict[int, float] = {}
    running = float("inf")
    for p in reversed(placed):
        later_min[p] = running
        running = min(running, float(by_index[p].start))

    def is_under(p: int, lane_end: float) -> bool:
        span = by_index[p]
        if recorded:
            return span.placement == "under"
        start = float(span.start)
        return abs(start - lane_end) <= _PLACEMENT_EPS_S and later_min[p] <= start

    out = list(unplaced)
    floor = lane_end = 0.0
    for p in placed:
        span = by_index[p]
        under = is_under(p, lane_end)
        if not under:
            lane_end = float(span.end)  # layout_placed's cursor
        # shorter than its tuck: heard entirely inside its predecessor's tail
        tucked = not under and float(span.end) <= floor and float(span.duration) > 0
        if under or tucked:
            start, end = float(span.start), float(span.end)
        else:
            start = max(float(span.start), floor)
            end = max(float(span.end), start)
            floor = end
        out[p] = MemberSpan(
            member_ids[p], p, bounded(start), bounded(end), concurrent=under or tucked
        )
    return tuple(out)


def episode_script(project, episode: Annotation):
    """The :class:`~braidio.script.Script` an ``episode-render`` was woven from,
    rebuilt from its members in play order — one beat per member, so
    ``script.beats[i]`` is the beat behind ``timeline.beats[i]``.

    What the graph path needs where the fast path had the authored script in
    hand: :func:`braidio.captions.cues_for` matches beats to timeline spans by
    index. A segment member becomes an **empty** narration beat: the graph
    stores a clip's *label* (``beat.label or beat.reference``), and a label
    is not what is heard — captioning ``♪ hook ♪`` over a sung line would be
    a claim the pictures do not support — so a clip contributes no cue. A
    member whose authoring node is gone (a beat a re-ingest removed) is
    likewise captioned as nothing rather than raising: captions are a
    courtesy, the timeline is the record.
    """
    from braidio.script import Dialogue, Narration, SceneBreak, Script

    index = graph_index(project)
    beats = []
    for sid in episode.body.get("ordered_member_ids", ()):
        member = index.get(uuid.UUID(sid))
        if member is None:
            beats.append(Narration(text=""))
            continue
        parents = resolve_parents(member, index)
        if member.tier == TIER_SCENE_BREAK:
            beats.append(SceneBreak(label=member.body.get("label", "")))
        elif member.tier == TIER_SEGMENT_EXTRACTION:
            beats.append(Narration(text=""))  # the clip's text is not known here
        elif member.tier == TIER_DIALOGUE_RENDER:
            beat = next((p for p in parents if p.tier == TIER_DIALOGUE_BEAT), None)
            turns = tuple(
                (str(r), str(t))
                for r, t in (beat.body.get("turns", ()) if beat else ())
            )
            beats.append(
                Dialogue(
                    turns=turns, label=(beat.body.get("label", "") if beat else "")
                )
            )
        else:
            beat = next((p for p in parents if p.tier == TIER_NARRATIVE_BEAT), None)
            beats.append(Narration(text=(beat.body.get("text", "") if beat else "")))
    return Script(title=_project_title(project), id_slug=str(episode.id), beats=beats)


def _profile_value(node: Annotation | None) -> str:
    """The declared rights profile's value from a ``render-profile`` node.

    ``None`` (the production declared none) resolves to
    :data:`braidio.rights.DEFAULT_PROFILE` — the same default the no-graph fast
    path applies — rather than to a literal, so the episode can never claim a
    projection the ingest did not actually render (thorwhalen/braidio#47).
    """
    return DEFAULT_PROFILE.value if node is None else str(node.body["profile"])


def _verify_members_against_profile(members, index, node: Annotation | None) -> None:
    """Refuse to weave members the declared profile forbids.

    The rights filter runs **once, at ingest**, which leaves one way to get a
    lying episode: change the profile afterwards and re-run only this transform
    — precisely the repair a "the episode is stale" signal invites. The members
    would still be the ones the *old* profile chose, and the episode would
    stamp the *new* profile's name on them.

    So this transform verifies rather than trusts: every clip member is
    re-checked with :func:`braidio.rights.clip_plays_under` — the same rule
    :func:`~braidio.rights.plan_production` applied, asked again, not
    reimplemented — against the profile now declared. A mismatch means the
    graph must be rebuilt from the script, so it raises rather than quietly
    producing a mislabelled cut (thorwhalen/braidio#47).

    The real fix is a re-run of ``weave_project`` on the same project: ingest
    reconciles the graph by identity (thorwhalen/braidio#51), so the refused
    clip is removed and the profile node rewritten in place, and this
    transform then sees members the new profile actually chose.
    """
    if node is None:  # undeclared → DEFAULT_PROFILE, which refuses nothing
        return
    profile = Profile(node.body["profile"])
    publishable = frozenset(
        node.body.get("publishable_clip_rights") or PUBLISHABLE_CLIP_RIGHTS
    )
    refused = [
        clip.body.get("label") or str(clip.id)
        for clip in _clip_parents(members, index)
        if not clip_plays_under(profile, clip.body.get("rights", ""), publishable)
    ]
    if refused:
        raise RightsViolation(
            f"profile {profile.value!r} forbids source audio still in this "
            f"episode's members: {', '.join(refused)}. The rights filter runs at "
            "ingest, so a profile change must go through re-ingest (re-run "
            "weave_project on this project with the new profile) — re-running "
            "the weave alone would mislabel the old cut."
        )


def _clip_parents(members, index):
    """The ``audio-clip`` node behind each segment-extraction member."""
    for member in members:
        if member.tier != TIER_SEGMENT_EXTRACTION:
            continue
        parents = resolve_parents(member, index)
        yield require_tier(parents, TIER_AUDIO_CLIP)


def _resolve_members(episode: Annotation, index: dict, *, what: str, fix: str) -> list:
    """The members named by ``episode.body["ordered_member_ids"]``, in order.

    Raises if any is absent from ``index``. A timeline is paired to its members
    by POSITION (``member_spans``, ``episode_script`` + ``captions.cues_for``,
    the picture track), so a member silently skipped would shift every member
    after it (braidio#98). ``what`` / ``fix`` word the error for the caller.
    """
    member_ids = [uuid.UUID(s) for s in episode.body.get("ordered_member_ids", ())]
    missing = [str(m) for m in member_ids if m not in index]
    if missing:
        raise ValueError(
            f"episode {episode.id}: {what} — members {missing} are no longer in the "
            f"graph. {fix}"
        )
    return [index[m] for m in member_ids]


@register_transform(NAME)
class WeaveToEpisode(BaseTransform):
    """All members — renders and scene breaks — (+ weave-config, + the
    production-structure and render-profile nodes when there are any) → one
    ``episode-render/v1``."""

    name = NAME
    input_kinds = (
        NARRATION_RENDER_V1,
        DIALOGUE_RENDER_V1,
        SEGMENT_EXTRACTION_V1,
        SCENE_BREAK_V1,
        WEAVE_CONFIG_V1,
        PRODUCTION_STRUCTURE_V1,
        RENDER_PROFILE_V1,
    )
    output_kind = EPISODE_RENDER_V1
    is_batch = True

    def plan(
        self, project, inputs: TransformInputs, *, params=None
    ) -> tuple[Plan, tuple[Annotation, ...]]:
        members = tuple(inputs.primary)
        cfg = singleton(project, TIER_WEAVE_CONFIG)
        structure = optional_singleton(project, TIER_PRODUCTION_STRUCTURE)
        render_profile = optional_singleton(project, TIER_RENDER_PROFILE)
        # Verify before planning anything: a profile that no longer matches the
        # members must fail here, not become a mislabelled render.
        _verify_members_against_profile(members, graph_index(project), render_profile)
        ordered_member_ids = tuple(str(a.id) for a in members)

        context = {WEAVE_CONFIG_V1: (cfg,)}
        # Only a production that declared structural music derives from it, so
        # one that did not keeps exactly the provenance (and identity) it had.
        if structure is not None:
            context[PRODUCTION_STRUCTURE_V1] = (structure,)
        # Same rule for the rights profile — and deriving from it is what makes
        # a profile change re-stale the episode through ordinary freshness.
        if render_profile is not None:
            context[RENDER_PROFILE_V1] = (render_profile,)
        full = TransformInputs(primary=members, context=context)
        skeleton = Annotation(
            id=uuid.uuid4(),
            tier=TIER_EPISODE_RENDER,
            reference=node_ref(TIER_EPISODE_RENDER),
            body=EpisodeRenderBodyV1(
                profile=_profile_value(render_profile),
                ordered_member_ids=ordered_member_ids,
            ).model_dump(),
            body_schema_uri=EPISODE_RENDER_V1,
            provenance=derive_provenance(self, full, attributed_to="agent:braidio"),
        )
        return Plan(calls=()), (skeleton,)

    def execute(
        self,
        project,
        plan: Plan,
        skeleton: tuple[Annotation, ...],
        *,
        use_cache: bool = True,
        force: bool = False,
    ) -> TransformResult:
        import braidio  # runtime attr access so tests can monkeypatch weave_timeline

        skel = skeleton[0]
        if use_cache and not force:
            # Idempotent re-run: the same members under the same, still-fresh
            # config/structure/profile is the episode already there (braidio#51).
            existing = fresh_equivalent(project, skel)
            if existing is not None:
                return TransformResult(
                    annotations=(existing,),
                    artifacts=(),
                    cost_usd_actual=0.0,
                    cache_hit_savings_usd=0.0,
                )
        index = graph_index(project)
        members = _resolve_members(
            skel,
            index,
            what="cannot render",
            fix="Re-run the weave so the skeleton is planned over the current graph.",
        )

        parents = resolve_parents(skel, index)
        cfg = require_tier(parents, TIER_WEAVE_CONFIG)
        config = cfg.body.get("config", {})
        structure, bed = _structure_and_bed(
            next((a for a in parents if a.tier == TIER_PRODUCTION_STRUCTURE), None)
        )
        target_lufs = float(config.get("target_lufs", _DEFAULT_TARGET_LUFS))

        breaks_dir = project.root / "data" / "breaks"
        items = [
            self._item(
                m,
                index,
                structure=structure,
                bed=bed,
                target_lufs=target_lufs,
                breaks_dir=breaks_dir,
            )
            for m in members
        ]

        # The timeline is built from the SAME parts the mixer is about to lay
        # out — probed the way weave_timeline probes them — and persisted on
        # the episode, so the cut points survive the process (plan §3).
        edge_overlap_s, crossfade_s = _layout_knobs(config)
        timeline = member_timeline(
            members,
            [braidio.duration_s(it.path) for it in items],
            index,
            config=config,
            structure=structure,
            profile=str(skel.body["profile"]),
            title=_project_title(project),
        )

        out_path = project.root / "data" / "episodes" / f"{skel.id}.mp3"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Interruption is off unless the config asks: leave the call exactly as
        # it was for a project that never set it (the absence rule, as above).
        overlap = turn_overlap_of(config)  # also what member_timeline laid out
        overlap_kw = {"overlap": overlap} if overlap.is_on else {}
        braidio.weave_timeline(
            items,
            out_path,
            clip_edge_overlap_s=edge_overlap_s,
            narration_crossfade_s=crossfade_s,
            target_lufs=target_lufs,
            true_peak=float(config.get("true_peak_dbtp", _DEFAULT_TRUE_PEAK)),
            sample_rate=int(config.get("sample_rate", _DEFAULT_SAMPLE_RATE)),
            bed=bed,
            **overlap_kw,
        )
        duration = safe_duration(out_path)
        artifact = audio_artifact(
            out_path,
            transform_name=self.name,
            derived_from=skel.provenance.was_derived_from,
            duration_s=duration,
        )
        completed = skel.model_copy(
            update={
                "body": {
                    **skel.body,
                    "artifact_id": artifact.asset_id,
                    "url": file_url(out_path),
                    "duration_s": duration,
                    "timeline": timeline.to_dict(),
                }
            }
        )
        project.graph.add_annotation(completed)
        return TransformResult(
            annotations=(completed,),
            artifacts=(artifact,),
            cost_usd_actual=0.0,
            cache_hit_savings_usd=0.0,
        )

    def _item(
        self,
        member: Annotation,
        index: dict,
        *,
        structure,
        bed,
        target_lufs: float,
        breaks_dir,
    ) -> TimelineItem:
        """One timeline part for ``member`` — a rendered beat, or a scene break.

        A scene break has no render node: its audio is prepared here, by the
        same :mod:`braidio.structure` primitives the no-graph path uses.
        Fade-to-spotlight is resolved only when there is a bed to drop out, so a
        production without one does no extra graph work. A narration render
        and a dialogue render are both *talk* on the timeline (the fast path
        labels a dialogue beat ``"narration"`` too); only an extraction is a
        clip.
        """
        from braidio.structure import prepare_pause, prepare_sting

        if member.tier == TIER_SCENE_BREAK:
            out = breaks_dir / f"{member.id}.mp3"
            part = (
                prepare_sting(structure.sting, out, target_lufs=target_lufs)
                if structure.plays_sting_of(member.body.get("marker"))
                else prepare_pause(structure.pause_s, out)
            )
            return TimelineItem(kind="sting", path=str(part), placement="sequential")

        is_clip = member.tier == TIER_SEGMENT_EXTRACTION
        return TimelineItem(
            kind="clip" if is_clip else "narration",
            path=str(url_to_path(member.body["url"])),
            placement="sequential",
            speaker=_member_speaker(member, index),
            spotlight=(
                is_clip
                and bed is not None
                and structure.spotlight_of(_clip_spotlight(member, index))
            ),
        )


def _clip_spotlight(extraction: Annotation, index: dict):
    """The ``spotlight`` override on the audio-clip an extraction came from."""
    clip = require_tier(resolve_parents(extraction, index), TIER_AUDIO_CLIP)
    return clip.body.get("spotlight")
