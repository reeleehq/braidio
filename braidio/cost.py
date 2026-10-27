"""Cost model for braidio's paid operations (ElevenLabs TTS).

braidio's only spend is ElevenLabs synthesis — :func:`braidio.narrate` /
:func:`braidio.text_to_dialogue` and the renderers built on them — billed by
ElevenLabs **per character** of submitted text. Everything else (ffmpeg
extraction, weaving, loudness normalization) is local and free.

This module turns text into a USD figure so renders can attribute a cost onto
``lacing.Artifact.cost_usd`` and a free :func:`estimate_cost` can preview a whole
:class:`braidio.Script` before any synthesis is paid for.

**The figure is an ESTIMATE**, priced at a configurable per-1000-character rate
(env :data:`RATE_ENV_VAR`, the per-model :data:`MODEL_USD_PER_1K_CHARS` table, or
:data:`DEFAULT_USD_PER_1K_CHARS`). It equals what ElevenLabs bills for a *live*
synthesis at that rate — not the provider's exact invoice. braidio does not yet
see the lower-level ``mixing`` on-disk cache, so a render served from that cache
still reports its rate estimate rather than ``$0`` (a safe over-estimate for a
spend ledger; see thorwhalen/braidio#8 to make it exact).

This differs from ``falaw`` (which has no default rate and returns ``None``
whenever a real price is unknown): braidio deliberately supplies a *default* so a
ledger has a number out of the box — while still returning ``None`` (*unpriced*,
never a fake ``0.0`` for real text) when the rate is explicitly disabled or
invalid. Characters are always counted exactly, so a dollar figure can be
recomputed if the rate changes.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Optional, Union

from braidio.script import Script, Narration, Dialogue
from braidio.tts import DEFAULT_MODEL_ID, DIALOGUE_MODEL_ID

#: Env var overriding the USD-per-1000-characters rate (global default for models
#: not in :data:`MODEL_USD_PER_1K_CHARS`). Set it to your ElevenLabs plan's real
#: rate for exact ledgers; set it to ``none`` to mark spend as *unpriced*.
RATE_ENV_VAR = "BRAIDIO_TTS_USD_PER_1K_CHARS"

#: Default USD per 1000 characters when nothing else is configured. Approximate:
#: ElevenLabs bills per credit and the $/credit depends on your plan, so set
#: :data:`RATE_ENV_VAR` to your plan's real rate. Kept slightly conservative so a
#: budget over- rather than under-estimates spend.
DEFAULT_USD_PER_1K_CHARS: float = 0.30

#: Confirmed per-model rate overrides (USD per 1000 chars). A model listed here
#: is priced at its own rate — winning over the env override and the default —
#: because a confirmed price is more accurate than a global guess. Empty until
#: ElevenLabs per-model rates are confirmed (e.g. ``eleven_v3`` dialogue vs
#: ``eleven_multilingual_v2``).
MODEL_USD_PER_1K_CHARS: dict[str, float] = {}

#: Env values (case-insensitive) that explicitly mark the rate as *unknown* so
#: costs report ``None`` (unpriced) rather than defaulting.
_UNPRICED_SENTINELS = ("", "none", "unpriced", "unknown")


def usd_per_1k_chars(model_id: Optional[str] = None) -> Optional[float]:
    """Resolved USD-per-1000-characters rate (most specific source first).

    Resolution: a confirmed per-model rate in :data:`MODEL_USD_PER_1K_CHARS` →
    the env override :data:`RATE_ENV_VAR` → :data:`DEFAULT_USD_PER_1K_CHARS`.
    Returns ``None`` (*unpriced*, not free) when the env override is explicitly
    disabled (one of :data:`_UNPRICED_SENTINELS`) or is not a finite, non-negative
    number — a bad rate must never silently become a dishonest negative/NaN spend.
    """
    if model_id and model_id in MODEL_USD_PER_1K_CHARS:
        return MODEL_USD_PER_1K_CHARS[model_id]
    env = os.environ.get(RATE_ENV_VAR)
    if env is not None:
        if env.strip().lower() in _UNPRICED_SENTINELS:
            return None
        try:
            val = float(env)
        except ValueError:
            return None
        return val if (math.isfinite(val) and val >= 0) else None
    return DEFAULT_USD_PER_1K_CHARS


def billable_chars(text: Optional[str]) -> int:
    """Characters ElevenLabs bills for ``text`` (the whole submitted string).

    ElevenLabs charges for everything sent — including ``eleven_v3`` audio tags
    like ``[excited]`` — so this is just ``len(text)``. A named function keeps the
    billing definition in one place if it ever needs to change.

    >>> billable_chars("hello")
    5
    >>> billable_chars(None)
    0
    """
    return len(text or "")


def tts_cost_usd(
    text: Optional[str], *, model_id: Optional[str] = None
) -> Optional[float]:
    """Estimated USD to synthesize ``text``; ``0.0`` for empty, ``None`` if unpriced.

    Empty text is genuinely free (``0.0``) regardless of the rate; non-empty text
    is ``None`` only when :func:`usd_per_1k_chars` is unpriced.

    >>> tts_cost_usd("")
    0.0
    """
    chars = billable_chars(text)
    if chars == 0:
        return 0.0
    rate = usd_per_1k_chars(model_id)
    if rate is None:
        return None
    return round(chars / 1000 * rate, 6)


def backend_cost_usd(
    text: Optional[str], *, tts: Optional[str], model_id: Optional[str] = None
) -> Optional[float]:
    """Estimated USD for ``text`` on the voice backend that speaks it.

    ``tts=None`` is ElevenLabs (:func:`tts_cost_usd`); a registered backend
    (:func:`braidio.tts.register_tts`) prices at its own rate. A name that is
    not registered (a runner serving cached renders need not register it)
    is unpriced, ``None``, never an error and never ``0.0``.

    >>> backend_cost_usd("", tts="anything")
    0.0
    >>> backend_cost_usd("hi", tts="not-registered") is None
    True
    """
    from braidio.tts import TTS_BACKENDS

    if not tts:
        return tts_cost_usd(text, model_id=model_id)
    if billable_chars(text) == 0:
        return 0.0
    backend = TTS_BACKENDS.get(tts)
    return None if backend is None else backend.cost_usd(text or "")


@dataclass(frozen=True)
class CostLine:
    """One billable line of an estimate (a narration beat or a dialogue beat).

    Named to echo ``falaw.CostLine`` (a line within a rollup) rather than
    ``falaw.CostEstimate`` (which means a per-call price spec) — so the vocabulary
    is consistent across the federation.
    """

    label: str
    kind: str  # 'narration' | 'dialogue'
    characters: int
    usd: Optional[float]
    model_id: str


@dataclass(frozen=True)
class CostRollup:
    """A production's estimated TTS spend, exact on characters, honest on dollars.

    ``usd`` is the sum of the *priced* lines; ``unpriced`` is ``True`` when some
    billable text had no configured rate (so ``usd`` is a lower bound). No billable
    lines (e.g. an all-segment script) gives ``characters=0, usd=0.0,
    unpriced=False``. Named ``CostRollup`` to match ``falaw.CostRollup``.
    """

    characters: int
    usd: Optional[float]
    unpriced: bool
    lines: tuple[CostLine, ...] = ()

    @property
    def summary(self) -> str:
        """One-line human summary (handy for a CLI/MCP preview)."""
        dollars = "unknown" if self.usd is None else f"${self.usd:.4f}"
        tail = " (+ unpriced parts)" if self.unpriced else ""
        return f"{self.characters:,} chars → {dollars}{tail}"


def _script_lines(script: Script) -> list[CostLine]:
    """Billable lines of ``script``: narration + dialogue beats (segments are free).

    Estimates the *default* (personal) cut. Published-cut costing (rights-safe
    rewrites + synthesized clip substitutes) must reuse :func:`braidio.plan_production`
    rather than re-deriving the rights rules here — a follow-up.
    """
    lines: list[CostLine] = []
    for i, beat in enumerate(script.beats):
        if isinstance(beat, Narration):
            lines.append(
                CostLine(
                    label=f"beat[{i}] narration",
                    kind="narration",
                    characters=billable_chars(beat.text),
                    usd=tts_cost_usd(beat.text, model_id=DEFAULT_MODEL_ID),
                    model_id=DEFAULT_MODEL_ID,
                )
            )
        elif isinstance(beat, Dialogue):
            text = "".join(t for _role, t in beat.turns)
            label = f"beat[{i}] dialogue"
            if beat.label:
                label += f" ({beat.label})"
            lines.append(
                CostLine(
                    label=label,
                    kind="dialogue",
                    characters=billable_chars(text),
                    usd=tts_cost_usd(text, model_id=DIALOGUE_MODEL_ID),
                    model_id=DIALOGUE_MODEL_ID,
                )
            )
        # a SegmentBeat is extracted media and a SceneBreak is structure (a
        # sting or silence, nothing synthesized) = free; neither contributes a line.
    return lines


def estimate_cost(
    source: Union[Script, str], *, model_id: Optional[str] = None
) -> CostRollup:
    """Estimate ElevenLabs spend for a :class:`braidio.Script` or a raw string.

    Free/local work (segment extraction, weaving) contributes nothing. The returned
    :class:`CostRollup` reports exact characters and an honest dollar sum (a lower
    bound when some text is unpriced; see :func:`usd_per_1k_chars`).

    >>> from braidio import Script, Narration, SegmentBeat
    >>> s = Script(title="x", id_slug="01", beats=[
    ...     Narration(text="a" * 500), SegmentBeat(reference="clip:1")])
    >>> estimate_cost(s).characters  # only the narration counts; the clip is free
    500
    """
    if isinstance(source, str):
        lines = [
            CostLine(
                label="text",
                kind="narration",
                characters=billable_chars(source),
                usd=tts_cost_usd(source, model_id=model_id or DEFAULT_MODEL_ID),
                model_id=model_id or DEFAULT_MODEL_ID,
            )
        ]
    else:
        lines = _script_lines(source)

    characters = sum(ln.characters for ln in lines)
    priced = [ln.usd for ln in lines if ln.usd is not None]
    unpriced = any(ln.characters > 0 and ln.usd is None for ln in lines)
    usd = round(sum(priced), 6) if priced else (None if unpriced else 0.0)
    return CostRollup(
        characters=characters, usd=usd, unpriced=unpriced, lines=tuple(lines)
    )
