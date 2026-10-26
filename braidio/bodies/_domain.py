"""Generic authoring body schemas for a commentary-weave production.

The media-agnostic domain vocabulary: curated commentary, cited sources,
playable media clips, narrative beats, dialogue beats (a multi-speaker
exchange), scene breaks (the structural boundary), and the episode container.
Registered with lacing on import. (Consumer-specific schemas — e.g. Hamilton's
Genius ``song``/``lyric-line``/``referent``/``annotation`` — live in the
consumer.)
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_serializer

from lacing.schema import register_body_schema

COMMENTARY_V1 = "annot://schema/commentary/v1"
SOURCE_V1 = "annot://schema/source/v1"
AUDIO_CLIP_V1 = "annot://schema/audio-clip/v1"
NARRATIVE_BEAT_V1 = "annot://schema/narrative-beat/v1"
DIALOGUE_BEAT_V1 = "annot://schema/dialogue-beat/v1"
SCENE_BREAK_V1 = "annot://schema/scene-break/v1"
EPISODE_V1 = "annot://schema/episode/v1"

CommentaryFacet = Literal["historical", "musical", "biographical", "production"]
ClipRights = Literal["copyrighted", "owned-local", "public-domain"]
"""Rights posture of a playable segment: ``owned-local`` plays only in the
personal cut; ``copyrighted`` never renders to audio; ``public-domain`` is safe."""


class CommentaryBodyV1(BaseModel):
    """A curated/generated commentary note, grounded in ``source`` nodes."""

    model_config = {"frozen": True, "extra": "forbid"}

    text: str = Field(..., description="The commentary, as narrated/derived.")
    facet: CommentaryFacet = Field(..., description="Lens: historical/musical/…")
    source_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Ids of the source/v1 nodes this draws on.",
    )
    generated_by: str = Field(
        ..., description="'human:<handle>' or 'agent:<model>@<hash>'."
    )


class SourceBodyV1(BaseModel):
    """A citable source node — primary material, a book, a web page."""

    model_config = {"frozen": True, "extra": "forbid"}

    kind: str = Field(
        ..., description="Open source class (e.g. 'founders-online', 'book')."
    )
    title: str = Field(..., description="Source title / heading.")
    citation: str = Field(..., description="Human-readable citation string.")
    url: str = Field("", description="Canonical URL, if any.")
    public_domain: bool = Field(..., description="True if usable in the published cut.")
    excerpt: Optional[str] = Field(None, description="Optional quoted excerpt.")


class AudioClipBodyV1(BaseModel):
    """A playable media segment. The interval is on the annotation's ``MediaRef``;
    this body carries clip metadata and the load-bearing ``rights`` flag."""

    model_config = {"frozen": True, "extra": "forbid"}

    source_node_id: str = Field(
        ..., description="Id of the source-media/song node this clip cuts."
    )
    label: str = Field(..., description="Human label, e.g. 'opening 8 bars'.")
    rights: ClipRights = Field(
        ..., description="Rights posture (drives render profile)."
    )
    gain_db: Optional[float] = Field(None, description="Optional gain (dB) when mixed.")
    fade: Optional[tuple[float, float]] = Field(
        None,
        description="Optional (fade_in_s, fade_out_s) applied when this clip plays.",
    )
    spotlight: Optional[bool] = Field(
        None,
        description=(
            "Fade-to-spotlight override (SegmentBeat.spotlight): the music bed "
            "drops out over this clip. None defers to the production's structure."
        ),
    )
    beat_id: Optional[str] = Field(
        None,
        description=(
            "Zero-padded ordering key of the script beat this clip came from "
            "(e.g. '0007') — the clip's ingest identity, so a re-ingest replaces "
            "it in place. None on clips written before re-ingest existed."
        ),
    )


class NarrativeBeatBodyV1(BaseModel):
    """One unit of the script: spoken narration, ordered, optionally playing a clip."""

    model_config = {"frozen": True, "extra": "forbid"}

    beat_id: str = Field(..., description="Zero-padded ordering key (e.g. '0007').")
    text: str = Field(..., description="Spoken narration text.")
    style: Optional[str] = Field(
        None, description="Optional delivery/voice style hint."
    )
    draws_on: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Ids this beat is derived from (commentary / source / clip nodes).",
    )
    voice: Optional[str] = Field(
        None, description="Per-beat voice id; overrides the weave-config pool."
    )

    @model_serializer(mode="wrap")
    def _omit_unset_voice(self, handler):
        """Leave ``voice`` out while unset, so a body written before the field
        existed is byte-identical on re-write (every writer, not one call site)."""
        data = handler(self)
        if data.get("voice") is None:
            data.pop("voice", None)
        return data

    plays_clip: Optional[str] = Field(
        None, description="Id of an audio-clip/v1 to play here."
    )


class DialogueBeatBodyV1(BaseModel):
    """One multi-speaker exchange of the script — the graph's ``Dialogue`` beat.

    ``turns`` is the authored content, as ``(role, text)`` pairs in speaking
    order; the roles are labels (``"A"`` / ``"host_b"``), not voices. Which
    voice each role gets is a production-level decision recorded once, on the
    singleton ``dialogue-cast/v1`` node, and the ``dialogue-render/v1`` derives
    from both — so a recast re-stales every exchange while an edited exchange
    re-stales only itself (thorwhalen/braidio#46). Rendered in ONE pass
    (Text-to-Dialogue) so the turns sound like people talking to each other.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    beat_id: str = Field(..., description="Zero-padded ordering key (e.g. '0007').")
    turns: tuple[tuple[str, str], ...] = Field(
        ..., description="Ordered (role, text) pairs; roles resolve via the cast."
    )
    label: str = Field("", description="Human label for the exchange, if any.")


class SceneBreakBodyV1(BaseModel):
    """A structural boundary between sections — the graph's ``SceneBreak`` beat.

    Ordered alongside the narrative beats and clips, so the episode transform
    can place a sting (or a pause) exactly where the script asked for one. It
    synthesizes nothing, so it has no render node of its own: the boundary is
    the decision, and :mod:`braidio.structure` turns it into audio at weave
    time (``marker`` overrides the production's ``scene_marker`` default).
    """

    model_config = {"frozen": True, "extra": "forbid"}

    beat_id: str = Field(..., description="Zero-padded ordering key (e.g. '0007').")
    label: str = Field("", description="Section starting here, e.g. 'rebuttal'.")
    marker: Optional[str] = Field(
        None,
        description="'sting' | 'none'; None defers to the production's default.",
    )


class EpisodeBodyV1(BaseModel):
    """An ordered container of narrative beats (and the clips they reference)."""

    model_config = {"frozen": True, "extra": "forbid"}

    title: str = Field(..., description="Episode title.")
    ordered_member_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Ids of narrative-beat nodes, in play order.",
    )


DOMAIN_SCHEMAS: dict[str, type[BaseModel]] = {
    COMMENTARY_V1: CommentaryBodyV1,
    SOURCE_V1: SourceBodyV1,
    AUDIO_CLIP_V1: AudioClipBodyV1,
    NARRATIVE_BEAT_V1: NarrativeBeatBodyV1,
    DIALOGUE_BEAT_V1: DialogueBeatBodyV1,
    SCENE_BREAK_V1: SceneBreakBodyV1,
    EPISODE_V1: EpisodeBodyV1,
}

for _uri, _model in DOMAIN_SCHEMAS.items():
    register_body_schema(_uri, _model)
