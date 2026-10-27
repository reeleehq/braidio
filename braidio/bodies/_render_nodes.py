"""Render-provenance body schemas — record the render *choices* + outputs.

These make a render traceable and **partially re-renderable**: each output node
records what it was derived from (via the annotation's
``provenance.was_derived_from``) plus a **cache key** hashing the inputs that
actually affect the audio. Change one beat or one parameter → only its
descendants are stale (see :mod:`braidio.provenance`). See the design in
``misc/docs/Extraction and nw-App Design.md`` and nw's render-provenance doc.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from lacing.schema import register_body_schema

WEAVE_CONFIG_V1 = "annot://schema/weave-config/v1"
PRODUCTION_STRUCTURE_V1 = "annot://schema/production-structure/v1"
RENDER_PROFILE_V1 = "annot://schema/render-profile/v1"
DIALOGUE_CAST_V1 = "annot://schema/dialogue-cast/v1"
SOURCE_MEDIA_V1 = "annot://schema/source-media/v1"
VOICE_ASSIGNMENT_V1 = "annot://schema/voice-assignment/v1"
NARRATION_RENDER_V1 = "annot://schema/narration-render/v1"
DIALOGUE_RENDER_V1 = "annot://schema/dialogue-render/v1"
SEGMENT_EXTRACTION_V1 = "annot://schema/segment-extraction/v1"
EPISODE_RENDER_V1 = "annot://schema/episode-render/v1"

#: ``NarrationRenderBodyV1.source`` values: a synthesised take, or a recording
#: a person uploaded in its place.
NARRATION_SOURCE_TTS = "tts"
NARRATION_SOURCE_UPLOAD = "upload"
NARRATION_SOURCES: tuple[str, ...] = (NARRATION_SOURCE_TTS, NARRATION_SOURCE_UPLOAD)


class WeaveConfigBodyV1(BaseModel):
    """A frozen snapshot of every render choice (``WeaveConfig.to_dict()``)."""

    model_config = {"frozen": True, "extra": "forbid"}

    config: dict[str, Any] = Field(..., description="The full WeaveConfig snapshot.")


class ProductionStructureBodyV1(BaseModel):
    """The production's structural-music decision — stings, spotlight, bed.

    A singleton sibling of the weave-config: the weave-config holds the editing
    knobs, this holds the *musical structure* a format declares
    (:class:`braidio.structure.MusicStructure`) plus the app-supplied assets
    that make it audible. It is written only when a production actually asks for
    structure, so a format that declares none leaves the graph — and the render
    — exactly as it was.

    Assets are recorded as **ids, not just locations**: ``*_asset_id`` is the
    content-addressed :class:`lacing.Artifact` id of the supplied file, so
    swapping the sting for different audio changes this body and re-stales the
    episode. ``*_url`` is the ``file://`` locator the render reads (same
    convention as every other render body here). ``sting`` / ``bed`` snapshot
    the remaining :class:`~braidio.structure.Sting` /
    :class:`~braidio.music.MusicBed` knobs.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    structure: dict[str, Any] = Field(
        ..., description="MusicStructure knobs: scene_marker, spotlight_clips, pause_s."
    )
    sting: Optional[dict[str, Any]] = Field(
        None, description="Sting knobs (gain_db, max_len_s, fade_out_s, gap_after_s)."
    )
    sting_asset_id: Optional[str] = Field(
        None, description="lacing Artifact asset_id of the sting audio."
    )
    sting_url: Optional[str] = Field(
        None, description="file:// URL of the sting audio."
    )
    bed: Optional[dict[str, Any]] = Field(
        None, description="MusicBed knobs (gain_db, fades, lead_in_s, start_s, loop…)."
    )
    bed_asset_id: Optional[str] = Field(
        None, description="lacing Artifact asset_id of the bed audio."
    )
    bed_url: Optional[str] = Field(None, description="file:// URL of the bed audio.")


class RenderProfileBodyV1(BaseModel):
    """The production's declared rights projection — and what it decided.

    A singleton sibling of the weave-config: the weave-config holds the editing
    knobs, this holds the **rights** decision
    (:class:`braidio.rights.Profile` + the publishable clip-rights set the
    caller injected via :class:`~braidio.rights.RightsPolicy`).

    It also records the *outcome*, because a rights decision that leaves no
    trace is a rights decision nobody can audit later: ``dropped`` names the
    segment beats the profile refused outright (non-publishable audio, no
    substitute) and ``substituted`` those it swapped for rights-safe narration.
    Both come straight from :func:`braidio.rights.plan_production` — the graph
    path runs the same filter as the no-graph one, it does not re-decide.

    Written only when a production actually declares a profile, so one that
    declares none keeps exactly the graph (and the episode's provenance) it had
    before this layer existed. See thorwhalen/braidio#47.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    profile: str = Field(..., description="Rights profile: personal | published.")
    publishable_clip_rights: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Segment `rights` values allowed to play in the published cut.",
    )
    dropped: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Segment beats the profile refused (no publishable audio, no substitute).",
    )
    substituted: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Beats the profile swapped for rights-safe narration.",
    )


class DialogueCastBodyV1(BaseModel):
    """The production's dialogue cast — which voice each role speaks with.

    A singleton sibling of the weave-config, holding exactly what a
    :class:`braidio.conversation.ConversationCast` holds: the role → voice map
    plus the Text-to-Dialogue model and its settings. Every
    ``dialogue-render/v1`` derives from this node, and from nothing else that
    a cast change would touch, so recasting re-stales the dialogue renders and
    only them — the narration, extractions and their voice assignments do not
    derive from it and stay fresh.

    Written only when the ingested script has a dialogue beat (the cast is the
    decision of *what those turns sound like*; without turns there is nothing
    to decide), so a project without dialogue keeps exactly the graph it had
    before this node existed. A dialogue script that declared no cast records
    the default cast rather than omitting the node: the render must derive
    from the voices it actually used (thorwhalen/braidio#46).
    """

    model_config = {"frozen": True, "extra": "forbid"}

    roles: dict[str, str] = Field(
        ..., description="Role label → ElevenLabs voice id (ConversationCast.roles)."
    )
    model_id: str = Field(
        ..., description="Text-to-Dialogue model id (e.g. 'eleven_v3')."
    )
    settings: Optional[dict[str, Any]] = Field(
        None, description="Model settings passed to Text-to-Dialogue (stability…)."
    )
    # Additive (default None, and omitted from the stored body when unset, so a
    # project voiced by ElevenLabs keeps the exact cast node it had): the
    # registered voice backend the turns are spoken with (braidio#132).
    tts: Optional[str] = Field(
        None, description="Registered voice backend (braidio.tts); None: ElevenLabs."
    )


class SourceMediaBodyV1(BaseModel):
    """A pointer to an imported source asset (its content-addressed artifact)."""

    model_config = {"frozen": True, "extra": "forbid"}

    label: str = Field(
        ..., description="Human label for the source (e.g. a song title)."
    )
    asset_id: str = Field(
        ..., description="lacing Artifact asset_id of the source media."
    )
    rights: str = Field("owned-local", description="Rights posture of the source.")
    beat_id: Optional[str] = Field(
        None,
        description=(
            "Zero-padded ordering key of the script beat this source was "
            "resolved for (e.g. '0007') — its ingest identity, so a re-ingest "
            "replaces it in place. None on nodes written before re-ingest existed."
        ),
    )


class VoiceAssignmentBodyV1(BaseModel):
    """Which voice a turn got (+ pool + seed) — a render choice."""

    model_config = {"frozen": True, "extra": "forbid"}

    voice_id: str = Field(..., description="Voice id used for this turn.")
    pool_label: str = Field("single", description="Pool the voice was drawn from.")
    seed: int = Field(0, description="Assignment seed (for reproducibility).")


class NarrationRenderBodyV1(BaseModel):
    """The audio Artifact for one synthesized narration turn + its cache key."""

    model_config = {"frozen": True, "extra": "forbid"}

    cache_key: str = Field(..., description="hash(text, voice, model, settings).")
    artifact_id: Optional[str] = Field(
        None, description="lacing Artifact asset_id (once rendered)."
    )
    url: Optional[str] = Field(
        None, description="file:// (or hosted) URL of the rendered audio."
    )
    duration_s: float = Field(0.0, description="Rendered duration, seconds.")
    # Additive (default "tts") so every row written before it exists still
    # loads and reads as the synthesised take it was. Without it an uploaded
    # recording is indistinguishable from a synthesised one, and a UI cannot
    # offer "put the robot back" (commentary-studio plan §3).
    source: str = Field(
        NARRATION_SOURCE_TTS,
        description="Where the take came from: 'tts' | 'upload'.",
    )


class DialogueRenderBodyV1(BaseModel):
    """The audio Artifact for one synthesized dialogue exchange + its cache key.

    The dialogue counterpart of :class:`NarrationRenderBodyV1`: one node per
    ``dialogue-beat/v1``, rendered in one Text-to-Dialogue pass under the
    production's ``dialogue-cast/v1``. ``cache_key`` hashes the turns, the
    cast's roles, model and settings — everything that reaches the audio.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    cache_key: str = Field(..., description="hash(turns, roles, model, settings).")
    artifact_id: Optional[str] = Field(
        None, description="lacing Artifact asset_id (once rendered)."
    )
    url: Optional[str] = Field(
        None, description="file:// (or hosted) URL of the rendered audio."
    )
    duration_s: float = Field(0.0, description="Rendered duration, seconds.")


class SegmentExtractionBodyV1(BaseModel):
    """The cut+padded audio Artifact for one segment + its cache key."""

    model_config = {"frozen": True, "extra": "forbid"}

    cache_key: str = Field(
        ..., description="hash(source asset, start, end, pads, fades)."
    )
    start_s: float = Field(..., description="Padded extraction start (seconds).")
    end_s: float = Field(..., description="Padded extraction end (seconds).")
    # Additive (default True), so every row written before it exists reads as
    # the recorded span it is. `start_s`/`end_s` are REQUIRED floats, so there
    # is no null to mean "nobody recorded this" — and an imported production
    # whose driver did not persist the span would otherwise have to invent one.
    # (0.0, duration) reads exactly like a real cut from the head of the
    # source, which is a claim about somebody else's recording. A required
    # bool says it instead of leaning on a sentinel: False means the pair is
    # not a span and nothing may reason from it.
    source_span_recorded: bool = Field(
        True,
        description="False: start_s/end_s are unknown, not a span. Do not use.",
    )
    artifact_id: Optional[str] = Field(None, description="lacing Artifact asset_id.")
    url: Optional[str] = Field(
        None, description="file:// (or hosted) URL of the extracted audio."
    )


class EpisodeRenderBodyV1(BaseModel):
    """The assembled output Artifact for an episode + its profile."""

    model_config = {"frozen": True, "extra": "forbid"}

    profile: str = Field(..., description="Render profile: personal | published.")
    ordered_member_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Ids of the episode's members, in play order: a render node "
            "(narration-render / dialogue-render / segment-extraction) or a "
            "scene-break node."
        ),
    )
    artifact_id: Optional[str] = Field(None, description="lacing Artifact of the mix.")
    url: Optional[str] = Field(
        None, description="file:// (or hosted) URL of the assembled episode audio."
    )
    duration_s: float = Field(0.0, description="Total duration, seconds.")
    # Additive (default None). The graph path used to produce NO timeline —
    # the cut points died with the process, which is why a picture track could
    # not survive a reopen. Written by weave_to_episode going forward as
    # ``TimelineBreakdown.to_dict()``; for a row written before this field
    # existed, ``braidio.transforms.episode_timeline`` reconstructs it from
    # ``ordered_member_ids`` + the members' durations (plan §3).
    timeline: Optional[dict[str, Any]] = Field(
        None, description="TimelineBreakdown.to_dict() of the assembled mix."
    )


RENDER_SCHEMAS: dict[str, type[BaseModel]] = {
    WEAVE_CONFIG_V1: WeaveConfigBodyV1,
    PRODUCTION_STRUCTURE_V1: ProductionStructureBodyV1,
    RENDER_PROFILE_V1: RenderProfileBodyV1,
    DIALOGUE_CAST_V1: DialogueCastBodyV1,
    SOURCE_MEDIA_V1: SourceMediaBodyV1,
    VOICE_ASSIGNMENT_V1: VoiceAssignmentBodyV1,
    NARRATION_RENDER_V1: NarrationRenderBodyV1,
    DIALOGUE_RENDER_V1: DialogueRenderBodyV1,
    SEGMENT_EXTRACTION_V1: SegmentExtractionBodyV1,
    EPISODE_RENDER_V1: EpisodeRenderBodyV1,
}

for _uri, _model in RENDER_SCHEMAS.items():
    register_body_schema(_uri, _model)
