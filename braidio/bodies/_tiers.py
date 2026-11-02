"""Generic tiers for a commentary-weave production + its render nodes.

Standoff layers (no temporal nesting); a consumer adds its own structural spine
(e.g. Hamilton's ``songs → sections → lyric-lines → words``). Add to a store
with :func:`register_tiers`.
"""

from __future__ import annotations

from lacing.tier import Tier

# Authoring layers.
_COMMENTARY = Tier("commentary")
_SOURCES = Tier("sources")
_AUDIO_CLIPS = Tier("audio-clips")
_NARRATIVE_BEATS = Tier("narrative-beats")
_DIALOGUE_BEATS = Tier("dialogue-beats")
_SCENE_BREAKS = Tier("scene-breaks")
# A person's recording standing in for a beat's take (braidio#86).
_TAKE_OVERRIDES = Tier("take-overrides")
# Render-provenance layers.
_WEAVE_CONFIGS = Tier("weave-configs")
_PRODUCTION_STRUCTURES = Tier("production-structures")
_RENDER_PROFILES = Tier("render-profiles")
_DIALOGUE_CASTS = Tier("dialogue-casts")
_SOURCE_MEDIA = Tier("source-media")
_VOICE_ASSIGNMENTS = Tier("voice-assignments")
_NARRATION_RENDERS = Tier("narration-renders")
_DIALOGUE_RENDERS = Tier("dialogue-renders")
_SEGMENT_EXTRACTIONS = Tier("segment-extractions")
_EPISODE_RENDERS = Tier("episode-renders")
# Picture-track layers (the commentary-studio plan, §3).
_STILLS = Tier("stills")
_VIDEO_PANELS = Tier("video-panels")
_VIDEO_CUTS = Tier("video-cuts")
_LABEL_TRACKS = Tier("label-tracks")

TIERS: tuple[Tier, ...] = (
    _COMMENTARY,
    _SOURCES,
    _AUDIO_CLIPS,
    _NARRATIVE_BEATS,
    _DIALOGUE_BEATS,
    _SCENE_BREAKS,
    _TAKE_OVERRIDES,
    _WEAVE_CONFIGS,
    _PRODUCTION_STRUCTURES,
    _RENDER_PROFILES,
    _DIALOGUE_CASTS,
    _SOURCE_MEDIA,
    _VOICE_ASSIGNMENTS,
    _NARRATION_RENDERS,
    _DIALOGUE_RENDERS,
    _SEGMENT_EXTRACTIONS,
    _EPISODE_RENDERS,
    _STILLS,
    _VIDEO_PANELS,
    _VIDEO_CUTS,
    _LABEL_TRACKS,
)
"""braidio's generic + render tiers."""


def register_tiers(store) -> None:
    """Add every tier in :data:`TIERS` to ``store`` (has ``add_tier``)."""
    for tier in TIERS:
        store.add_tier(tier)
