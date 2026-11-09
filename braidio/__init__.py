"""braidio — weave narration with extracted media segments into productions.

Braid two kinds of strand into one production: authored **narration** (TTS —
single voice or a cycled pool) and extracted **segments** of source media (song
clips, audiobook passages, news, SFX). The result renders to an audiovisual
object.

This is the **functional core** (pure Python over files + numbers; deps:
``mixing``, ``elevenlabs``, and ``ffmpeg`` on PATH). An optional nw-app layer
(graph bodies, transforms, provenance / partial re-render) will be added on top
and imported only when ``nw`` is available — ``import braidio`` never requires
it.

Extracted from the Hamilton lyrics-podcast; the extraction is designed in
Hamilton#18 (the epic) and Hamilton#28 (placement + naming). Those numbers are
in the *Hamilton* repo — braidio has its own #18 about something else.
"""

from __future__ import annotations

# --- composition model ---
from braidio.script import (  # noqa: F401
    Script,
    Narration,
    SegmentBeat,
    Dialogue,
    SceneBreak,
    SCENE_MARKERS,
    Beat,
    narration_segments,
)

# --- rights profiles ---
from braidio.rights import (  # noqa: F401
    Profile,
    DEFAULT_PROFILE,
    RightsPolicy,
    RenderPlan,
    PlannedBeat,
    plan_production,
    clip_plays_under,
    rights_are_publishable,
    RightsViolation,
    find_verbatim_text,
    content_violations,
    segment_is_publishable,
    PUBLISHABLE_CLIP_RIGHTS,
)

# --- segment sources (reference -> cuttable window) ---
from braidio.sources import (  # noqa: F401
    SegmentSource,
    ResolvedSegment,
    Segment,
    TimedLine,
    NamespacedSegmentSource,
    TimedLineSegmentSource,
    find_segment,
    load_timing,
    cut_quote,
)

# --- user-overridable defaults (explicit arg > env > config file > package) ---
from braidio.defaults import (  # noqa: F401
    PACKAGE_DEFAULT_DELIVERY,
    config_path,
    default_delivery,
    default_voice_id,
    default_voice_settings,
    describe_defaults,
    user_config,
)

# --- narration synthesis ---
from braidio.tts import (  # noqa: F401
    narrate,
    text_to_dialogue,
    resolve_voice_id,
    DEFAULT_VOICE_ID,
    DEFAULT_MODEL_ID,
    DEFAULT_VOICE_SETTINGS,
    VOICE_ENV_VAR,
)

# --- cost model (ElevenLabs TTS spend) ---
from braidio.cost import (  # noqa: F401
    estimate_cost,
    tts_cost_usd,
    billable_chars,
    usd_per_1k_chars,
    CostRollup,
    CostLine,
    RATE_ENV_VAR,
    DEFAULT_USD_PER_1K_CHARS,
)
from braidio.conversation import (  # noqa: F401
    ConversationCast,
    DEFAULT_CAST,
    render_dialogue,
    render_turns_sequential,
)
from braidio.delivery import (  # noqa: F401
    Delivery,
    DELIVERIES,
    BASELINE,
    V2_TUNED,
    V2_AGGRESSIVE,
    V2_PRESENTER,
    V2_NARRATOR,
    V3_NATURAL,
    V3_CREATIVE,
    V3_PRESENTER,
    V3_NARRATOR,
    V4_PRESENTER,
    V4_NARRATOR,
    NARRATION,
    CONVERSATIONAL,
)

# --- intra-beat narration pacing (pure planner; the renderer executes it) ---
from braidio.pacing import (  # noqa: F401
    NarrationTurn,
    Boundary,
    BOUNDARIES,
    SEGMENTATION_UNITS,
    plan_turns,
    classify_boundary,
    split_units,
)

# --- multi-voice casting ---
from braidio.multivoice import (  # noqa: F401
    Voice,
    POOL_4,
    POOL_MANY,
    POOLS,
    strip_markup,
    split_segments,
    assign_voices,
    group_turns,
    render_multivoice,
)

# --- configuration ---
from braidio.weave_config import WeaveConfig, PRESETS  # noqa: F401

# --- music bed (instrumental underscore) ---
from braidio.music import (  # noqa: F401
    MusicBed,
    bed_for_intensity,
    BED_GAIN_BY_INTENSITY,
)

# --- structural music (stings at scene breaks, fade-to-spotlight) ---
from braidio.structure import (  # noqa: F401
    Sting,
    MusicStructure,
    DEFAULT_STRUCTURE,
)

# --- ready-made format templates (standard-named presets) ---
from braidio.formats import (  # noqa: F401
    Format,
    render_format,
    describe_asset_application,
    FORMATS,
    SOLO_EXPLAINER,
    DEEP_DIVE,
    INTERVIEW,
    SONG_EXPLODER,
    PANEL,
    DEBATE,
    DOCUMENTARY_VO,
)

# --- composition + weaving (audio) ---
from braidio.compose import compose_narration  # noqa: F401
from braidio.render import render_production  # noqa: F401
from braidio.sound_design import SoundDesign  # noqa: F401
from braidio.timeline import (  # noqa: F401
    BeatSpan,
    TimelineBreakdown,
    build_timeline,
    render_settings,
)

# --- captions (pure: authored text + the render's own timeline, no ASR) ---
from braidio.captions import (  # noqa: F401
    Cue,
    captions_for,
    captions_from_cues,
    cues_for,
    format_srt,
)

# --- time an authored script on audio braidio did not render (measured words) ---
from braidio.align import Alignment, align_script  # noqa: F401

# --- markdown document -> narration beats (read-aloud) ---
from braidio.markdown_script import (  # noqa: F401
    TagMismatch,
    script_from_markdown,
    untagged_differences,
)

# --- text prep + style audit (reusable script helpers) ---
from braidio.textprep import clean_ocr, strip_speaker_labels  # noqa: F401
from braidio.style import (  # noqa: F401
    Finding,
    PLATITUDE_PATTERNS,
    audit_platitudes,
    audit_expressiveness,
    audio_tag_rate,
    audio_tags,
    TAG_RATE_FLOOR,
    TAG_RATE_CEILING,
    platitude_rate,
)
from braidio.weave import (  # noqa: F401
    TimelineItem,
    extract_padded,
    weave_timeline,
    layout_starts,
    duration_s,
)

# --- production kinds (pure) ---
from braidio.kinds import WeaveKind  # noqa: F401

# --- version ---------------------------------------------------------------
# Read from installed distribution metadata rather than written literally here:
# CI bumps the version in pyproject.toml and pushes back, so a literal in this
# file would silently drift from the released version. `"unknown"` is the honest
# answer for a source tree that was never installed (not even editable).
from importlib.metadata import PackageNotFoundError, version as _dist_version

try:
    __version__ = _dist_version("braidio")
except PackageNotFoundError:  # pragma: no cover - source tree, not installed
    __version__ = "unknown"

# --- optional nw-app layer (graph bodies + provenance) --------------------
# Registers braidio's domain/render body schemas with lacing and exposes the
# provenance / partial-re-render helpers. Guarded: `import braidio` never needs
# lacing/nw. `HAS_GRAPH` / `HAS_NW` report what's available.
HAS_GRAPH = False
HAS_NW = False
#: Why a layer did not load (``None`` when it did): the ``ImportError`` the
#: guards below swallow. The swallowing is right for a bare install, and the
#: same guard would hide a *broken* layer (an nw below braidio's floor, a bad
#: import), so CI asserts the layer loads whenever its dependencies import
#: (``tests/test_optional_layers.py``, braidio#187) and names this on failure.
_GRAPH_IMPORT_ERROR: ImportError | None = None
_NW_IMPORT_ERROR: ImportError | None = None
#: Optional distributions a layer needs. A layer whose dependencies are not all
#: installed is simply absent (the bare install, silent); anything that fails
#: *after* that preflight is a defect in braidio or a version skew (braidio#207).
_GRAPH_DEPS = ("lacing",)
_NW_DEPS = ("lacing", "nw", "falaw")


def _missing(deps) -> list[str]:
    """The distributions in ``deps`` that cannot be imported (decided without importing).

    A dependency whose spec cannot be found, or is a bare namespace folder with
    no code (``origin is None``), is absent; one whose spec lookup itself raises is
    treated as present, so the import that follows reports it.
    """
    import importlib.util

    out = []
    for dep in deps:
        try:
            spec = importlib.util.find_spec(dep)
        except (ValueError, ImportError):
            continue
        if spec is None or spec.origin is None:
            out.append(dep)
    return out


def _layer_failed(layer: str, exc: ImportError, loaded: list[str]) -> None:
    """Report a layer that failed although its dependencies are installed.

    Left half-registered with its flag ``False`` (lacing and nw have no unregister
    to roll it back with), so it is said once, naming what did load. A
    ``RuntimeWarning``, because ``ImportWarning`` is ignored by default and the
    point is that this is not hidden.
    """
    import warnings

    detail = (
        "imported before the failure: "
        + ", ".join(loaded)
        + " (what they register stays registered)"
        if loaded
        else "no module of it had loaded (a module that failed partway may still "
        "have registered some of its own)"
    )
    warnings.warn(
        f"braidio's {layer} layer failed to load ({exc!r}); {detail}",
        RuntimeWarning,
        stacklevel=1,
    )


#: What each layer registered before it failed (empty when it loaded or was absent).
_graph_loaded: list[str] = []
_nw_loaded: list[str] = []

if _absent := _missing(_GRAPH_DEPS):
    _GRAPH_IMPORT_ERROR = ModuleNotFoundError(f"No module named {_absent[0]!r}")
else:
    try:
        from braidio import bodies  # noqa: F401  (registers schemas)

        _graph_loaded.append("bodies")
        from braidio.provenance import (  # noqa: F401
            record_render,
            stale_after,
            descendants_of,
        )

        HAS_GRAPH = True
    except ImportError as _exc:  # pragma: no cover - defect, not absence
        _GRAPH_IMPORT_ERROR = _exc
        _layer_failed("graph", _exc, _graph_loaded)
if _absent := _missing(_NW_DEPS):
    _NW_IMPORT_ERROR = ModuleNotFoundError(f"No module named {_absent[0]!r}")
elif not HAS_GRAPH:  # the nw layer imports the graph's bodies: one failure, one report
    _NW_IMPORT_ERROR = _GRAPH_IMPORT_ERROR
else:
    try:
        from braidio.project import Project  # noqa: F401

        _nw_loaded.append("project")
        from braidio import transforms  # noqa: F401  (registers braidio's nw.Transforms)

        _nw_loaded.append("transforms")
        from braidio.transforms import WeaveReceipt, weave_project  # noqa: F401
        from braidio.genre import COMMENTARY_WEAVE  # noqa: F401  (registers the nw.Genre)

        HAS_NW = True
    except ImportError as _exc:  # pragma: no cover - defect, not absence
        _NW_IMPORT_ERROR = _exc
        _layer_failed("nw", _exc, _nw_loaded)
del _absent  # layer bookkeeping, not API


def skills_dir():
    """Path to the agent skills that ship with braidio.

    They install with the package, so an agent host can be pointed at them
    without cloning the repo::

        ln -s "$(python -c 'import braidio; print(braidio.skills_dir())')/braidio" \\
              ~/.claude/skills/braidio
    """
    from pathlib import Path

    return Path(__file__).parent / "data" / "skills"


# --- optional video layer (Ken Burns film over stills) --------------------
# `braidio.video` itself imports cleanly with nothing extra installed — its
# dependencies (burns, pillow) are imported inside the functions that need them,
# so the pure planners stay usable. `HAS_VIDEO` reports whether the *render* path
# is actually available; `braidio.video.missing_dependencies()` names what's absent.
from braidio.video import HAS_VIDEO  # noqa: F401,E402

__all__ = [
    "__version__",
    # production kinds
    "WeaveKind",
    "HAS_GRAPH",
    "HAS_NW",
    "HAS_VIDEO",
    "skills_dir",
    # markdown read-aloud
    "TagMismatch",
    "script_from_markdown",
    "untagged_differences",
    # captions
    "Cue",
    "captions_for",
    "captions_from_cues",
    "Alignment",
    "align_script",
    "cues_for",
    "format_srt",
    # script
    "Script",
    "Narration",
    "SegmentBeat",
    "Dialogue",
    "SceneBreak",
    "SCENE_MARKERS",
    "Sting",
    "MusicStructure",
    "DEFAULT_STRUCTURE",
    "Beat",
    "narration_segments",
    # rights
    "Profile",
    "DEFAULT_PROFILE",
    "RightsPolicy",
    "RenderPlan",
    "PlannedBeat",
    "plan_production",
    "clip_plays_under",
    "rights_are_publishable",
    "RightsViolation",
    "find_verbatim_text",
    "content_violations",
    "segment_is_publishable",
    "PUBLISHABLE_CLIP_RIGHTS",
    # render
    "render_production",
    "SoundDesign",
    # sources
    "SegmentSource",
    "ResolvedSegment",
    "Segment",
    "TimedLine",
    "NamespacedSegmentSource",
    "TimedLineSegmentSource",
    "find_segment",
    "load_timing",
    "cut_quote",
    # tts
    "narrate",
    "resolve_voice_id",
    "DEFAULT_VOICE_ID",
    "DEFAULT_MODEL_ID",
    "DEFAULT_VOICE_SETTINGS",
    "VOICE_ENV_VAR",
    # cost (ElevenLabs TTS spend)
    "estimate_cost",
    "tts_cost_usd",
    "billable_chars",
    "usd_per_1k_chars",
    "CostRollup",
    "CostLine",
    "RATE_ENV_VAR",
    "DEFAULT_USD_PER_1K_CHARS",
    # delivery
    "Delivery",
    "DELIVERIES",
    "BASELINE",
    "V2_TUNED",
    "V2_AGGRESSIVE",
    "V2_PRESENTER",
    "V2_NARRATOR",
    "V3_NATURAL",
    "V3_CREATIVE",
    "NARRATION",
    "CONVERSATIONAL",
    # multivoice
    "Voice",
    "POOL_4",
    "POOL_MANY",
    "POOLS",
    "strip_markup",
    "split_segments",
    "assign_voices",
    "group_turns",
    "render_multivoice",
    # config
    "WeaveConfig",
    "PRESETS",
    # music bed
    "MusicBed",
    "bed_for_intensity",
    "BED_GAIN_BY_INTENSITY",
    # formats (ready-made templates)
    "Format",
    "render_format",
    "describe_asset_application",
    "FORMATS",
    "SOLO_EXPLAINER",
    "DEEP_DIVE",
    "INTERVIEW",
    "SONG_EXPLODER",
    "PANEL",
    "DEBATE",
    "DOCUMENTARY_VO",
    # compose + weave
    "compose_narration",
    "TimelineItem",
    "extract_padded",
    "weave_timeline",
    "layout_starts",
    "duration_s",
    # timeline breakdown
    "BeatSpan",
    "TimelineBreakdown",
    "build_timeline",
    "render_settings",
    # text prep + style audit
    "clean_ocr",
    "strip_speaker_labels",
    "Finding",
    "PLATITUDE_PATTERNS",
    "PACKAGE_DEFAULT_DELIVERY",
    "config_path",
    "default_delivery",
    "default_voice_id",
    "default_voice_settings",
    "describe_defaults",
    "user_config",
    "audit_platitudes",
    "audit_expressiveness",
    "audio_tag_rate",
    "audio_tags",
    "TAG_RATE_FLOOR",
    "TAG_RATE_CEILING",
    "platitude_rate",
]
