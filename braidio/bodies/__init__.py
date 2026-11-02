"""braidio's lacing body schemas + tiers (the nw-app domain vocabulary).

Importing this package **registers every schema** with lacing (side effect of
importing the submodules). Requires ``lacing``; it is imported lazily by
``braidio/__init__.py`` only when available, so the functional core never
needs it.
"""

from __future__ import annotations

from braidio.bodies._domain import (  # noqa: F401  (import registers)
    COMMENTARY_V1,
    SOURCE_V1,
    AUDIO_CLIP_V1,
    NARRATIVE_BEAT_V1,
    DIALOGUE_BEAT_V1,
    SCENE_BREAK_V1,
    EPISODE_V1,
    CommentaryBodyV1,
    SourceBodyV1,
    AudioClipBodyV1,
    NarrativeBeatBodyV1,
    DialogueBeatBodyV1,
    SceneBreakBodyV1,
    EpisodeBodyV1,
    DOMAIN_SCHEMAS,
)
from braidio.bodies._render_nodes import (  # noqa: F401  (import registers)
    WEAVE_CONFIG_V1,
    PRODUCTION_STRUCTURE_V1,
    RENDER_PROFILE_V1,
    DIALOGUE_CAST_V1,
    SOURCE_MEDIA_V1,
    VOICE_ASSIGNMENT_V1,
    NARRATION_RENDER_V1,
    DIALOGUE_RENDER_V1,
    SEGMENT_EXTRACTION_V1,
    EPISODE_RENDER_V1,
    TAKE_OVERRIDE_V1,
    WeaveConfigBodyV1,
    ProductionStructureBodyV1,
    RenderProfileBodyV1,
    DialogueCastBodyV1,
    SourceMediaBodyV1,
    VoiceAssignmentBodyV1,
    NarrationRenderBodyV1,
    DialogueRenderBodyV1,
    SegmentExtractionBodyV1,
    EpisodeRenderBodyV1,
    TakeOverrideBodyV1,
    RENDER_SCHEMAS,
    NARRATION_SOURCE_TTS,
    NARRATION_SOURCE_UPLOAD,
    NARRATION_SOURCES,
)
from braidio.bodies._video import (  # noqa: F401  (import registers)
    STILL_V1,
    VIDEO_PANEL_V1,
    VIDEO_CUT_V1,
    LABEL_TRACK_V1,
    FootageRefV1,
    RectV1,
    StillBodyV1,
    VideoPanelBodyV1,
    VideoCutBodyV1,
    LabelTrackBodyV1,
    VIDEO_SCHEMAS,
    MOVES,
    DEFAULT_MOVE,
    DEFAULT_ZOOM,
    CUT_STAGES,
    CUT_PROFILES,
    LABEL_KINDS,
    RIGHTS_FIELDS,
    credit_line,
)
from braidio.bodies._tiers import TIERS, register_tiers  # noqa: F401

# All schema URIs braidio registers (domain + render + picture track).
SCHEMA_URIS: dict = {**DOMAIN_SCHEMAS, **RENDER_SCHEMAS, **VIDEO_SCHEMAS}
