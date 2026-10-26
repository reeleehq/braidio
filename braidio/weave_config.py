"""WeaveConfig — every editing choice for weaving narration + segments (#20).

One frozen config object that exposes **all** the knobs (casting, turns, pacing,
timing, clip weaving, loudness) so a composer picks combinations rather than
inheriting one hardcoded style (the "enable all choices" mandate of #18/#28).

Defaults come from ``docs/research/podcast-audio-weaving-editing.md``. This
module is deliberately **Hamilton-agnostic** — no Genius/LRCLIB/episode imports —
so it can move to the reusable weave package unchanged (#19). It references the
generic voice pools/ids only.

The config is designed to serialize cleanly (:meth:`WeaveConfig.to_dict`) into a
``render-config`` provenance node (#26), so a render is reproducible from its
recorded choices.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, Mapping

from braidio.multivoice import POOL_4, POOL_MANY
from braidio.pacing import SEGMENTATION_UNITS
from braidio.tts import DEFAULT_VOICE_ID

# The interruption value object lives with the layout that consumes it (so the
# weave stays free of this module's TTS imports); re-exported here for callers.
from braidio.weave import MAX_OVERLAP_TURN_S, TurnOverlap  # noqa: F401

# Base narration settings. Shaped for eleven_v3, which is the default model:
# it honours `stability` and silently ignores `style` / `similarity_boost` /
# `speed`, so carrying those here would advertise control that does not exist.
# 0.0 is the expressive end; see braidio.delivery for the measurements.
_DEFAULT_VOICE_SETTINGS: dict[str, Any] = {
    "stability": 0.0,
    "use_speaker_boost": True,
}


@dataclass(frozen=True)
class WeaveConfig:
    """All editing choices for a narration+segment weave. Frozen + serializable."""

    # --- Casting ------------------------------------------------------------
    voices: tuple[str, ...] = (DEFAULT_VOICE_ID,)  # 1 id = single; >1 = cycled pool
    pool_label: str = "single"
    voice_seed: int = 7
    avoid_immediate_repeat: bool = True

    # --- Delivery (TTS) -----------------------------------------------------
    model_id: str = "eleven_multilingual_v2"
    voice_settings: Mapping[str, Any] = field(
        default_factory=lambda: dict(_DEFAULT_VOICE_SETTINGS)
    )

    # --- Segmentation / turns ----------------------------------------------
    # How a narration beat is cut into turns. ``"beat"`` (the default) = one TTS
    # call for the whole beat, i.e. no intra-beat pacing at all — historical
    # behavior. Anything else engages :mod:`braidio.pacing` in
    # :func:`braidio.render.render_production`, which is what makes
    # ``min_turn``/``max_turn``/``speed_jitter``/``gap_turn_s`` audible there.
    segmentation_unit: str = "beat"  # beat | paragraph | sentence | clause
    min_turn: int = 2  # segments a voice speaks before switching
    max_turn: int = 4

    # --- Pacing -------------------------------------------------------------
    speed_base: float = 1.0  # fallback center when the Delivery sets no speed
    speed_jitter: float = 0.04  # ± per turn (models with a speed knob only)

    # --- Timing between turns ----------------------------------------------
    crossfade_s: float = 0.12
    gap_turn_s: float = 0.0  # silence at a *sentence* boundary between turns;
    # other boundaries scale off it (braidio.pacing.BOUNDARIES)
    # Speaker interruption (Hamilton#22): at a *change of speaker* between
    # narration parts, the next voice starts up to this much before the previous
    # one ends (0 = off; at most MAX_OVERLAP_TURN_S). Rendered by the weave
    # (``braidio.weave.layout_placed``), so it engages the mixed path.
    overlap_turn_s: float = 0.0
    overlap_jitter: float = 0.3  # each overlap is shortened by up to this fraction
    overlap_duck_db: float = 0.0  # lower the outgoing voice over the overlap (≤ 0)

    # --- Clip weaving (#21) -------------------------------------------------
    clip_pre_roll_s: float = 0.4  # extend extraction before the words
    clip_post_roll_s: float = 0.3  # …and after — capture words cleanly
    clip_fade_in_s: float = 0.5
    clip_fade_out_s: float = 0.8
    clip_min_len_s: float = (
        2.2  # floor length for a clip (short clips extend + get adaptive fades)
    )
    clip_edge_overlap_s: float = 0.5  # tuck faded clip edges under narration
    duck_db: float = -15.0  # duck clip under speech (speech dominant)

    # --- Global mix ---------------------------------------------------------
    target_lufs: float = -16.0
    true_peak_dbtp: float = -1.0
    sample_rate: int = 44100

    def __post_init__(self) -> None:
        if not self.voices:
            raise ValueError("WeaveConfig.voices must have at least one voice id")
        if not (1 <= self.min_turn <= self.max_turn):
            raise ValueError("require 1 <= min_turn <= max_turn")
        self.turn_overlap()  # TurnOverlap owns the overlap_* rules
        if self.segmentation_unit not in SEGMENTATION_UNITS:
            raise ValueError(
                f"WeaveConfig.segmentation_unit must be one of {SEGMENTATION_UNITS}, "
                f"got {self.segmentation_unit!r} — a typo here would silently "
                "disable intra-beat pacing"
            )

    @property
    def paces_narration(self) -> bool:
        """Whether a render cuts each narration beat into separately-spoken turns.

        ``False`` (the default, ``segmentation_unit="beat"``) is one TTS call per
        beat — no intra-beat gaps, no per-turn speed, so ``gap_turn_s`` and
        ``speed_jitter`` do nothing on the :func:`braidio.render.render_production`
        path. Opt in by setting ``segmentation_unit`` to a smaller unit.

        >>> WeaveConfig().paces_narration
        False
        >>> WeaveConfig(segmentation_unit="sentence").paces_narration
        True
        """
        return self.segmentation_unit != "beat"

    @property
    def is_multivoice(self) -> bool:
        return len(self.voices) > 1

    def turn_overlap(self) -> TurnOverlap:
        """The speaker-interruption knobs as the value the weave consumes.

        Its ``seed`` is ``voice_seed``: one seed fixes casting and interruptions.
        """
        return turn_overlap_of(self.to_dict())

    def with_(self, **changes: Any) -> "WeaveConfig":
        """Return a copy with fields overridden (e.g. ``cfg.with_(min_turn=1)``)."""
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        """Stable serialization for a ``render-config`` provenance node (#26)."""
        d = asdict(self)
        d["voices"] = list(self.voices)
        d["voice_settings"] = dict(self.voice_settings)
        return d


_FIELD_DEFAULTS = {f.name: f.default for f in fields(WeaveConfig)}


def turn_overlap_of(config: Mapping[str, Any]) -> TurnOverlap:
    """:meth:`WeaveConfig.turn_overlap` for a serialized config (``to_dict()``).

    A knob the mapping omits takes the :class:`WeaveConfig` default, so a
    snapshot written before the knob existed means "no interruption".

    >>> turn_overlap_of({}).is_on
    False
    >>> turn_overlap_of({"overlap_turn_s": 0.2, "voice_seed": 3}).seed
    3
    """
    get = lambda name: config.get(name, _FIELD_DEFAULTS[name])  # noqa: E731
    return TurnOverlap(
        max_s=get("overlap_turn_s"),
        jitter=get("overlap_jitter"),
        duck_db=get("overlap_duck_db"),
        seed=get("voice_seed"),
    )


# --- Presets -----------------------------------------------------------------

_POOL_4_IDS = tuple(v.id for v in POOL_4)
_POOL_MANY_IDS = tuple(v.id for v in POOL_MANY)

SINGLE_NARRATOR = WeaveConfig(voices=(DEFAULT_VOICE_ID,), pool_label="single")

DOCUMENTARY_LYRICS = WeaveConfig(  # the research default: one narrator, tight turns
    voices=(DEFAULT_VOICE_ID,), pool_label="single", min_turn=1, max_turn=3
)

PANEL_4 = WeaveConfig(voices=_POOL_4_IDS, pool_label="panel-4", min_turn=2, max_turn=4)

INTERVIEW_MANY = WeaveConfig(  # Hamilton's chosen default
    voices=_POOL_MANY_IDS, pool_label="interview-many", min_turn=2, max_turn=4
)

PRESETS: dict[str, WeaveConfig] = {
    "single_narrator": SINGLE_NARRATOR,
    "documentary_lyrics": DOCUMENTARY_LYRICS,
    "panel_4": PANEL_4,
    "interview_many": INTERVIEW_MANY,
}
