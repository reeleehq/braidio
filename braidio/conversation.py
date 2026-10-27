"""Conversational register: render an exchange as people *talking to each other*.

The second delivery register beside narration (braidio#1). A
:class:`ConversationCast` maps role labels (``"A"``/``"B"``) to contrasting
**conversational** ElevenLabs voices. :func:`render_dialogue` synthesizes the
whole exchange in **one pass** via :func:`braidio.tts.text_to_dialogue` (eleven_v3)
so prosody is conditioned across turns — the key to not sounding narrated.
:func:`render_turns_sequential` is the per-line baseline (each turn synthesized
alone, then concatenated) used for A/B comparison.

Casting note: use conversational-labelled voices, not narrator voices; loosen
settings so v3 audio tags fire. The scripted exchange itself must carry the
disfluency (backchannels, interruptions, fragments) — the model won't invent it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from braidio.tts import narrate, text_to_dialogue

# Curated conversational voices (casual, not narrator).
JESSICA = "cgSgspJ2msm6clMCkdW9"  # playful, bright, warm (F)
WILL = "bIHbv24MWmeRgasZH58o"  # relaxed optimist (M)
CHRIS = "iP95p4xoKVk53GoZ742B"  # charming, down-to-earth (M)
LAURA = "FGY2WhTYpPnrIDTdsKH5"  # enthusiast, quirky attitude (F)


@dataclass(frozen=True)
class ConversationCast:
    """Role → voice mapping + model/settings for a conversational exchange.

    Default cast: **Jessica** (playful/bright, F) + **Chris** (charming/
    down-to-earth, M) — snappier than the "relaxed" voices. ``stability=0.45``
    is the research sweet spot for a lively but coherent read (never 1.0/Robust,
    which mutes v3 tags; ~0.1 is too unstable). See braidio#1 + the pacing doc.
    """

    roles: dict[str, str] = field(default_factory=lambda: {"A": JESSICA, "B": CHRIS})
    model_id: str = "eleven_v3"
    settings: dict | None = field(default_factory=lambda: {"stability": 0.45})


DEFAULT_CAST = ConversationCast()


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH.")


def render_dialogue(
    turns: list[tuple[str, str]],
    cast: ConversationCast = DEFAULT_CAST,
    *,
    api_key: str | None = None,
    out_path: str | Path,
    output_format: str = "mp3_44100_128",
    seed: int | None = None,
    cache=True,
    refresh: bool = False,
    tighten_gaps_s: float = 0.6,
    return_cache_status: bool = False,
) -> Path | tuple[Path, bool]:
    """One-pass render of ``turns`` (``[(role, text), …]``) via Text-to-Dialogue.

    Cached by default (see :func:`braidio.tts.text_to_dialogue`): an unchanged
    exchange renders instantly on re-run. ``refresh=True`` re-rolls the take.

    ``api_key`` is an optional per-request ElevenLabs key threaded to
    :func:`braidio.tts.text_to_dialogue`; ``None`` (default) keeps the
    ``$ELEVENLABS_API_KEY`` fallback.

    ``tighten_gaps_s`` (>0) applies the "R-tight" pass: ffmpeg trims only the
    over-long dead gaps (silences ≥ this many seconds) that make v3 dialogue
    feel draggy, while leaving natural short pauses. Set 0 to keep the raw take.

    ``return_cache_status``: when ``True``, return ``(path, was_cached)`` where
    ``was_cached`` is ``True`` iff the take came from the on-disk cache (no
    ElevenLabs call = $0 real spend) — what lets the graph path attribute real
    cost (braidio#8). Default ``False`` keeps the ``Path`` return.
    """
    vturns = [(cast.roles[role], text) for role, text in turns]
    audio, was_cached = text_to_dialogue(
        vturns,
        model_id=cast.model_id,
        settings=cast.settings,
        seed=seed,
        output_format=output_format,
        api_key=api_key,
        cache=cache,
        refresh=refresh,
        return_cache_status=True,
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio)
    if tighten_gaps_s and tighten_gaps_s > 0:
        _require_ffmpeg()
        tmp = out.parent / (out.stem + "-tight.mp3")
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(out),
                "-af",
                f"silenceremove=stop_periods=-1:stop_duration={tighten_gaps_s}:stop_threshold=-38dB",
                str(tmp),
            ],
            check=True,
            capture_output=True,
        )
        os.replace(tmp, out)
    return (out, was_cached) if return_cache_status else out


def _concat_with_gaps(
    parts: list[Path], out: Path, *, gap_s: float, sample_rate: int = 44100
) -> Path:
    """Concatenate audio parts with ``gap_s`` seconds of silence between them."""
    from mixing import concatenate_audio

    _require_ffmpeg()
    out.parent.mkdir(parents=True, exist_ok=True)
    if gap_s <= 0:
        concatenate_audio(*[str(p) for p in parts], output=str(out), crossfade=0.0)
        return out
    sil = out.parent / "_gap.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={sample_rate}:cl=stereo",
            "-t",
            f"{gap_s:.3f}",
            str(sil),
        ],
        check=True,
        capture_output=True,
    )
    interleaved: list[str] = []
    for i, p in enumerate(parts):
        if i:
            interleaved.append(str(sil))
        interleaved.append(str(p))
    concatenate_audio(*interleaved, output=str(out), crossfade=0.0)
    return out


def render_turns_sequential(
    turns: list[tuple[str, str]],
    cast: ConversationCast = DEFAULT_CAST,
    *,
    api_key: str | None = None,
    out_path: str | Path,
    work_dir: str | Path = "data/tts/conversation",
    voice_settings: dict | None = None,
    gap_s: float = 0.25,
    narrate_fn=None,
) -> Path:
    """Per-line baseline: synthesize each turn alone (its role's voice) and
    concatenate with ``gap_s`` gaps. Prosody is NOT shared across turns — this is
    what tends to sound like alternating monologues (the thing to beat).

    ``api_key`` is threaded to :func:`braidio.tts.narrate`; ``None`` (default)
    keeps the ``$ELEVENLABS_API_KEY`` fallback. ``narrate_fn`` is the per-turn
    synthesizer (default :func:`braidio.tts.narrate`): a renderer given another
    TTS backend (:func:`braidio.tts.narrator_from_speak`) renders dialogue here,
    since only ElevenLabs has the one-pass path."""
    synthesize = narrate_fn or narrate
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for i, (role, text) in enumerate(turns):
        raw = synthesize(
            text,
            work / f"turn{i:02d}-{role}.mp3",
            api_key=api_key,
            voice_id=cast.roles[role],
            model_id=cast.model_id,
            voice_settings=voice_settings,
        )
        parts.append(raw)
    out = Path(out_path)
    return _concat_with_gaps(parts, out, gap_s=gap_s)
