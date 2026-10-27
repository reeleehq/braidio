"""Multi-voice narration: cycle a pool of voices across segments (issue #10).

When one voice reads flat, variety across voices is a stronger lever than any
single-voice setting. This renders narration by splitting it into segments
(sentences) and assigning each a voice drawn from a pool — randomly (seeded, so
it's reproducible) and avoiding immediate repeats so it doesn't feel like a
rigid round-robin. Speed is jittered slightly per segment, even within one
speaker, to break regularity.

Two curated pools of **premade** ElevenLabs voices (all production quality,
deliberately less familiar than "George", 2M/2F in :data:`POOL_4`, wider in
:data:`POOL_MANY`). For a truly large "many people / interviews" pool, the
shared Voice Library (thousands) can be tapped, but quality varies there, so
those need curation — the premade pools below are the safe default.
"""

from __future__ import annotations

import random
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mixing import concatenate_audio

from braidio.leveling import DEFAULT_TARGET_LUFS, level_part
from braidio.tts import narrate

_MARKUP_RE = re.compile(r"<[^>]*>|\[[^\]]*\]")
_SENT_SPLIT = re.compile(r"(?<=[.?!])\s+(?=[\"“']?[A-Z])")


@dataclass(frozen=True)
class Voice:
    """A pooled narration voice."""

    id: str
    name: str
    gender: str  # "M" | "F"
    accent: str = ""
    note: str = ""


# 2M / 2F, mixed accents/registers, all less familiar than "George".
POOL_4: list[Voice] = [
    Voice("EXAVITQu4vr4xnSDxMaL", "Sarah", "F", "American", "mature, reassuring"),
    Voice("Xb7hH8MSUJpSbSDYk0k2", "Alice", "F", "British", "clear, engaging"),
    Voice("IKne3meq5aSn9XLyUdCD", "Charlie", "M", "Australian", "deep, energetic"),
    Voice("cjVigY5qzO86Huf0OWal", "Eric", "M", "American", "smooth, trustworthy"),
]

# Wider pool for a "lots of different people / interviews" feel (5M / 5F).
POOL_MANY: list[Voice] = POOL_4 + [
    Voice("cgSgspJ2msm6clMCkdW9", "Jessica", "F", "American", "playful, bright"),
    Voice("XrExE9yKIg1WjnnlVkGX", "Matilda", "F", "American", "professional"),
    Voice("pFZP5JQG7iQjIQuC4Bku", "Lily", "F", "British", "velvety"),
    Voice("N2lVS1w4EtoT3dr4eOWO", "Callum", "M", "American", "husky"),
    Voice("bIHbv24MWmeRgasZH58o", "Will", "M", "American", "relaxed"),
    Voice("pqHfZKP75CvOlQylNhV4", "Bill", "M", "American", "wise, mature"),
]

POOLS: dict[str, list[Voice]] = {"four": POOL_4, "many": POOL_MANY}

_BASE_SETTINGS = {
    "stability": 0.4,
    "similarity_boost": 0.75,
    "style": 0.3,
    "use_speaker_boost": True,
}


def strip_markup(text: str) -> str:
    return _MARKUP_RE.sub(" ", text)


def split_segments(text: str) -> list[str]:
    """Split narration into sentence-level segments (markup removed).

    Splits on sentence-final ``.?!`` (not the ``…`` used for in-thought pacing),
    so connected clauses stay with one speaker.
    """
    clean = re.sub(r"\s+", " ", strip_markup(text)).strip()
    return [s.strip() for s in _SENT_SPLIT.split(clean) if s.strip()]


def assign_voices(
    n: int, pool: list[Voice], *, seed: int = 0, avoid_repeats: bool = True
) -> list[Voice]:
    """Assign a voice to each of ``n`` turns — random, seeded, no immediate
    repeats (so it isn't a rigid round-robin)."""
    rng = random.Random(seed)
    out: list[Voice] = []
    prev: Voice | None = None
    for _ in range(n):
        choices = [v for v in pool if not (avoid_repeats and v is prev)] or pool
        v = rng.choice(choices)
        out.append(v)
        prev = v
    return out


def group_turns(
    segments: list[str], *, min_turn: int = 1, max_turn: int = 1, seed: int = 0
) -> list[str]:
    """Group consecutive segments into *turns* of ``min_turn..max_turn`` segments.

    A turn is what one voice speaks before the next takes over. Bigger turns =
    each speaker talks longer (fewer switches). Each turn's segments are joined
    into one utterance so prosody is continuous within a speaker. Turn sizes are
    seeded-random within the range.
    """
    if min_turn < 1 or max_turn < min_turn:
        raise ValueError("require 1 <= min_turn <= max_turn")
    rng = random.Random(seed * 17 + 3)
    turns: list[str] = []
    i = 0
    while i < len(segments):
        k = rng.randint(min_turn, max_turn)
        turns.append(" ".join(segments[i : i + k]))
        i += k
    return turns


def render_multivoice(
    segments: list[str],
    pool: list[Voice],
    *,
    out_path: str | Path,
    api_key: str | None = None,
    work_dir: str | Path = "data/tts/multivoice",
    seed: int = 7,
    min_turn: int = 2,
    max_turn: int = 4,
    avoid_immediate_repeat: bool = True,
    model_id: str = "eleven_multilingual_v2",
    base_settings: dict | None = None,
    speed_base: float = 1.0,
    speed_jitter: float = 0.04,
    crossfade_s: float = 0.1,
    gap_s: float = 0.0,
    target_lufs: float = DEFAULT_TARGET_LUFS,
) -> list[tuple[Voice, str]]:
    """Render ``segments`` cycling ``pool`` → ``out_path``.

    Segments are first grouped into *turns* of ``min_turn..max_turn`` segments
    (bigger = each voice talks longer). One voice per turn, no immediate repeat,
    with a jittered speed even within a speaker. ``gap_s`` inserts silence
    between turns (0 = none). Returns ``[(voice, turn_text), …]`` for reporting.

    ``api_key`` is an optional per-request ElevenLabs key threaded to every
    :func:`braidio.tts.narrate` call; ``None`` (default) keeps the
    ``$ELEVENLABS_API_KEY`` fallback.

    NOTE: overlapping/interrupting speakers and clip ducking are separate,
    upcoming parameters (tracked as issues) — this renders turns sequentially.
    """
    settings = dict(base_settings if base_settings is not None else _BASE_SETTINGS)
    turns = group_turns(segments, min_turn=min_turn, max_turn=max_turn, seed=seed)
    rng = random.Random(seed * 31 + 1)
    voices = assign_voices(
        len(turns), pool, seed=seed, avoid_repeats=avoid_immediate_repeat
    )
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for i, (turn, v) in enumerate(zip(turns, voices)):
        speed = round(speed_base + rng.uniform(-speed_jitter, speed_jitter), 3)
        raw = narrate(
            turn,
            work / f"turn{i:03d}-{v.name}.mp3",
            api_key=api_key,
            voice_id=v.id,
            model_id=model_id,
            voice_settings={**settings, "speed": speed},
        )
        parts.append(
            level_part(raw, work / f"norm{i:03d}.mp3", target_lufs=target_lufs)
        )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if gap_s > 0:
        _concat_with_gaps(parts, out, gap_s=gap_s)
    else:
        concatenate_audio(
            *[str(p) for p in parts], output=str(out), crossfade=crossfade_s
        )
    return list(zip(voices, turns))


def _concat_with_gaps(parts: list[Path], out: Path, *, gap_s: float) -> Path:
    """Concatenate audio parts with ``gap_s`` seconds of silence between them."""
    from mixing import Audio  # local import; audio object model

    silence = None
    pieces: list[str] = []
    for p in parts:
        pieces.append(str(p))
    # Build via ffmpeg: create a silence file and interleave.
    sil = out.parent / "_gap.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=44100:cl=stereo",
            "-t",
            f"{gap_s:.3f}",
            str(sil),
        ],
        check=True,
        capture_output=True,
    )
    interleaved: list[str] = []
    for i, p in enumerate(pieces):
        if i:
            interleaved.append(str(sil))
        interleaved.append(p)
    concatenate_audio(*interleaved, output=str(out), crossfade=0.0)
    return out
