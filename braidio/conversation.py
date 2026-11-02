"""Conversational register: render an exchange as people *talking to each other*.

The second delivery register beside narration (braidio#1). A
:class:`ConversationCast` maps role labels (``"A"``/``"B"``) to contrasting
**conversational** ElevenLabs voices. :func:`render_dialogue` synthesizes the
whole exchange in **one pass** via :func:`braidio.tts.text_to_dialogue` (eleven_v3)
so prosody is conditioned across turns — the key to not sounding narrated.
:func:`render_turns_sequential` is the per-line baseline (each turn synthesized
alone, then concatenated) used for A/B comparison.

**Addressable lines** (braidio#145): :func:`render_dialogue_lines` is the same
one-pass render, timed — it returns a :class:`DialogueTake` whose
:class:`DialogueLine`\\ s say where each turn sits in the take, and
:func:`rerender_line` re-synthesizes ONE turn and splices it in, leaving every
other line's audio (and its cross-turn prosody) untouched and unbilled.
:func:`line_cost` quotes a line before it is rendered.

Casting note: use conversational-labelled voices, not narrator voices; loosen
settings so v3 audio tags fire. The scripted exchange itself must carry the
disfluency (backchannels, interruptions, fragments) — the model won't invent it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

from braidio.cost import CostLine, CostRollup, billable_chars, tts_cost_usd
from braidio.tts import (
    narrate,
    text_to_dialogue,
    text_to_dialogue_with_timestamps,
)

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


def concat_with_gaps(
    parts: list[Path], out: Path, *, gap_s: float, sample_rate: int = 44100
) -> Path:
    """Concatenate audio parts with ``gap_s`` seconds of silence between them."""
    from mixing import concatenate_audio

    _require_ffmpeg()
    out.parent.mkdir(parents=True, exist_ok=True)
    if gap_s <= 0:
        concatenate_audio(*[str(p) for p in parts], output=str(out), crossfade=0.0)
        return out
    # beside its output, named for it: two renders in one folder never share it
    sil = out.with_name(f".{out.stem}-gap.mp3")
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
    try:
        concatenate_audio(*interleaved, output=str(out), crossfade=0.0)
    finally:
        sil.unlink(missing_ok=True)
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
    return concat_with_gaps(parts, out, gap_s=gap_s)


# --- addressable lines (braidio#145) -----------------------------------------


@dataclass(frozen=True)
class DialogueLine:
    """One turn of a :class:`DialogueTake`: who says what, and where it sits.

    ``offset_s``/``duration_s`` are seconds into the take's file. They span
    the turn's voice segments (earliest start to latest end), so the pause
    *between* two turns belongs to neither line and survives a re-render.
    """

    index: int
    role: str
    text: str
    offset_s: float
    duration_s: float

    @property
    def end_s(self) -> float:
        """Where the line ends in the take."""
        return self.offset_s + self.duration_s


@dataclass(frozen=True)
class DialogueTake:
    """A rendered exchange whose turns are addressable lines.

    ``path`` is the take's file; ``lines`` its turns in order, timed;
    ``turns``, ``cast`` and ``output_format`` are what :func:`rerender_line`
    needs to re-voice one of them. :meth:`to_dict`/:meth:`from_dict` round-trip
    it through JSON, so a host can store a take and re-render a line of it in
    a later request.
    """

    path: Path
    lines: tuple[DialogueLine, ...]
    turns: tuple[tuple[str, str], ...]
    cast: ConversationCast = DEFAULT_CAST
    output_format: str = "mp3_44100_128"

    def to_dict(self) -> dict:
        """A JSON-able record of the take (no secret ever lives on a take)."""
        return {
            "path": str(self.path),
            "lines": [
                {
                    "index": ln.index,
                    "role": ln.role,
                    "text": ln.text,
                    "offset_s": ln.offset_s,
                    "duration_s": ln.duration_s,
                }
                for ln in self.lines
            ],
            "turns": [list(t) for t in self.turns],
            "cast": {
                "roles": dict(self.cast.roles),
                "model_id": self.cast.model_id,
                "settings": self.cast.settings,
            },
            "output_format": self.output_format,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DialogueTake:
        """The take :meth:`to_dict` recorded."""
        cast = d["cast"]
        return cls(
            path=Path(d["path"]),
            lines=tuple(DialogueLine(**ln) for ln in d["lines"]),
            turns=tuple((role, text) for role, text in d["turns"]),
            cast=ConversationCast(
                roles=dict(cast["roles"]),
                model_id=cast["model_id"],
                settings=cast["settings"],
            ),
            output_format=d["output_format"],
        )


class DialogueTimingError(RuntimeError):
    """A timed take came back without the timings a line needs."""


#: The output formats a take can be spliced in: ElevenLabs' format family →
#: (ffmpeg encoder, ffmpeg muxer). Raw ``pcm_``/``ulaw_``/``alaw_`` output has
#: no container ffmpeg could read back, so it is refused before any spend.
_SPLICE_CODECS: dict[str, tuple[str, str]] = {
    "mp3": ("libmp3lame", "mp3"),
    "wav": ("pcm_s16le", "wav"),
    "opus": ("libopus", "ogg"),
}


def _encode_args(output_format: str) -> list[str]:
    """ffmpeg output args reproducing ElevenLabs' ``{codec}_{rate}[_{kbps}]``."""
    family, *rest = output_format.split("_")
    if family not in _SPLICE_CODECS or not rest:
        raise ValueError(
            f"output_format {output_format!r} cannot be spliced line by line; "
            f"use one of the {sorted(_SPLICE_CODECS)} formats (e.g. 'mp3_44100_128')"
        )
    encoder, muxer = _SPLICE_CODECS[family]
    args = ["-c:a", encoder, "-ar", rest[0]]
    if len(rest) > 1:
        args += ["-b:a", f"{rest[1]}k"]
    return args + ["-f", muxer]


def _cast_voices(turns, cast: ConversationCast) -> list[tuple[str, str]]:
    """``(role, text)`` turns → ``(voice_id, text)``, naming an unknown role."""
    missing = sorted({role for role, _ in turns} - set(cast.roles))
    if missing:
        raise KeyError(
            f"the cast has no voice for role(s) {missing}; it has {sorted(cast.roles)}"
        )
    return [(cast.roles[role], text) for role, text in turns]


def _input_spans(segments, n_inputs: int) -> dict[int, tuple[float, float]]:
    """Each input's span over its segments: earliest start, latest end."""
    spans: dict[int, tuple[float, float]] = {}
    for seg in segments:
        if not 0 <= seg.input_index < n_inputs:
            raise DialogueTimingError(
                f"a voice segment names input {seg.input_index}, "
                f"but the take has {n_inputs} turn(s)"
            )
        lo, hi = spans.get(seg.input_index, (seg.start_s, seg.end_s))
        spans[seg.input_index] = (min(lo, seg.start_s), max(hi, seg.end_s))
    return spans


def _lines_from_segments(turns, segments) -> tuple[DialogueLine, ...]:
    spans = _input_spans(segments, len(turns))
    missing = [i for i in range(len(turns)) if i not in spans]
    if missing:
        raise DialogueTimingError(
            f"the dialogue take has no voice_segments for turn(s) {missing}, so "
            "those lines cannot be located in it; re-roll the take with "
            "refresh=True, or render those turns alone"
        )
    return tuple(
        DialogueLine(
            index=i,
            role=role,
            text=text,
            offset_s=spans[i][0],
            duration_s=spans[i][1] - spans[i][0],
        )
        for i, (role, text) in enumerate(turns)
    )


def _probe(path: Path) -> tuple[float, int]:
    """``(duration_s, sample_rate)`` of an audio file, per ffprobe."""
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-select_streams",
            "a:0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(r.stdout)
    return float(info["format"]["duration"]), int(info["streams"][0]["sample_rate"])


def _splice(
    take_path: Path,
    line_path: Path,
    *,
    cut: tuple[float, float],
    keep: tuple[float, float],
    out: Path,
    output_format: str,
) -> None:
    """Write ``take[:cut[0]] + line[keep[0]:keep[1]] + take[cut[1]:]`` to ``out``.

    One ffmpeg pass (one decode, one encode), cut on sample boundaries."""
    take_total, take_rate = _probe(take_path)
    _, line_rate = _probe(line_path)

    def _samples(t: float, rate: int) -> int:
        return round(t * rate)

    pieces: list[tuple[int, str]] = []  # (input, atrim args)
    if _samples(cut[0], take_rate) > 0:
        pieces.append((0, f"end_sample={_samples(cut[0], take_rate)}"))
    pieces.append(
        (
            1,
            f"start_sample={_samples(keep[0], line_rate)}"
            f":end_sample={_samples(keep[1], line_rate)}",
        )
    )
    if _samples(cut[1], take_rate) < _samples(take_total, take_rate):
        pieces.append((0, f"start_sample={_samples(cut[1], take_rate)}"))

    uses = sum(1 for src, _ in pieces if src == 0)
    graph = [f"[0:a]asplit={uses}" + "".join(f"[t{k}]" for k in range(uses))]
    labels, k = [], 0
    for n, (src, trim) in enumerate(pieces):
        pad = f"[t{k}]" if src == 0 else "[1:a]"
        k += src == 0
        graph.append(f"{pad}atrim={trim},asetpts=PTS-STARTPTS[p{n}]")
        labels.append(f"[p{n}]")
    graph.append(f"{''.join(labels)}concat=n={len(pieces)}:v=0:a=1[out]")
    tmp = out.with_name(f".{out.stem}-splice{out.suffix}")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(take_path),
            "-i",
            str(line_path),
            "-filter_complex",
            ";".join(graph),
            "-map",
            "[out]",
            *_encode_args(output_format),
            str(tmp),
        ],
        check=True,
        capture_output=True,
    )
    os.replace(tmp, out)


def render_dialogue_lines(
    turns: list[tuple[str, str]],
    cast: ConversationCast = DEFAULT_CAST,
    *,
    out_path: str | Path,
    api_key: str | None = None,
    output_format: str = "mp3_44100_128",
    seed: int | None = None,
    cache=True,
    refresh: bool = False,
    return_cache_status: bool = False,
) -> DialogueTake | tuple[DialogueTake, bool]:
    """:func:`render_dialogue`, as addressable lines: one take, every turn timed.

    ONE Text-to-Dialogue pass (:func:`braidio.tts.text_to_dialogue_with_timestamps`),
    so the cross-turn prosody of :func:`render_dialogue` is kept, and each
    turn's place in the take comes from the API's own voice segments. Returns
    a :class:`DialogueTake`; :func:`rerender_line` re-voices one of its lines.

    Unlike :func:`render_dialogue` there is **no R-tight pass**: trimming the
    dead gaps would move every line after the first trim away from the
    timings the API reported. The take is the raw one.

    Billing, caching and the key are :func:`render_dialogue`'s: the whole
    exchange's characters, once; a cache hit (audio *and* timings, stored
    together) costs $0 and makes no API call, and ``return_cache_status=True``
    returns ``(take, was_cached)`` to say which. ``api_key`` reaches the
    ElevenLabs client only — it is not part of the cache key and not on the
    take. A take whose response lacks the timings of some turn raises
    :class:`DialogueTimingError` naming them (re-roll with ``refresh=True``).
    """
    turns = tuple((role, text) for role, text in turns)
    if not turns:
        raise ValueError("a dialogue take needs at least one turn")
    _encode_args(output_format)  # refuse an unspliceable format before spending
    audio, segments, was_cached = text_to_dialogue_with_timestamps(
        _cast_voices(turns, cast),
        model_id=cast.model_id,
        settings=cast.settings,
        seed=seed,
        output_format=output_format,
        api_key=api_key,
        cache=cache,
        refresh=refresh,
        return_cache_status=True,
    )
    lines = _lines_from_segments(turns, segments)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio)
    take = DialogueTake(
        path=out, lines=lines, turns=turns, cast=cast, output_format=output_format
    )
    return (take, was_cached) if return_cache_status else take


def _line(take: DialogueTake, index: int) -> DialogueLine:
    if not 0 <= index < len(take.lines):
        raise IndexError(
            f"line {index} is not in the take (it has lines 0..{len(take.lines) - 1})"
        )
    return take.lines[index]


def line_cost(take: DialogueTake, index: int, *, text: str | None = None) -> CostRollup:
    """What re-rendering line ``index`` (as ``text``, default its own) would cost.

    Free and offline: a :func:`rerender_line` submits that one line's text and
    nothing else, priced at the cast's model like any other estimate
    (:func:`braidio.cost.tts_cost_usd`; ``usd`` is ``None`` when unpriced).
    """
    line = _line(take, index)
    text = line.text if text is None else text
    model = take.cast.model_id
    return CostRollup.from_lines(
        [
            CostLine(
                label=f"line[{index}] {line.role}",
                kind="dialogue",
                characters=billable_chars(text),
                usd=tts_cost_usd(text, model_id=model),
                model_id=model,
            )
        ]
    )


def rerender_line(
    take: DialogueTake,
    index: int,
    *,
    text: str | None = None,
    api_key: str | None = None,
    out_path: str | Path | None = None,
    seed: int | None = None,
    cache=True,
    refresh: bool = False,
    return_cache_status: bool = False,
) -> DialogueTake | tuple[DialogueTake, bool]:
    """Re-voice line ``index`` of ``take`` and splice it in; the others stay.

    The line (``text``, default its current text — put eleven_v3 ``[audio
    tags]`` in it to change the delivery) is synthesized ALONE, through a
    single-input Text-to-Dialogue call with its role's voice and the cast's
    model and settings, so it matches the take's voice. Only its characters
    are billed (:func:`line_cost` quotes them first). The new audio is cut to
    its own voice segment and replaces the old line's span: the take before
    the line and after it is kept as is, and every later line moves by the
    change in duration. The pauses around the line are the take's own. If
    the API timed two turns as overlapping, the overlap is cut with this line.

    Cached like any dialogue call: re-rendering the same text with the same
    ``seed`` is a $0 cache hit, so pass ``refresh=True`` (or a new ``seed``)
    to re-roll a line. ``return_cache_status=True`` returns
    ``(take, was_cached)``.

    The result is written to ``out_path`` (default: over ``take.path``, in
    place), re-encoded once in the take's ``output_format``. Returns the new
    :class:`DialogueTake`; ``take`` itself is a value and is not changed,
    though with the in-place default its ``path`` now holds the new audio.
    """
    line = _line(take, index)
    text = line.text if text is None else text
    _require_ffmpeg()
    _encode_args(take.output_format)  # refuse before spending
    audio, segments, was_cached = text_to_dialogue_with_timestamps(
        _cast_voices([(line.role, text)], take.cast),
        model_id=take.cast.model_id,
        settings=take.cast.settings,
        seed=seed,
        output_format=take.output_format,
        api_key=api_key,
        cache=cache,
        refresh=refresh,
        return_cache_status=True,
    )
    out = Path(out_path) if out_path is not None else take.path
    out.parent.mkdir(parents=True, exist_ok=True)
    solo = out.with_name(f".{out.stem}-line{index}{take.path.suffix}")
    solo.write_bytes(audio)
    try:
        solo_total, _ = _probe(solo)
        spans = _input_spans(segments, 1)
        start, end = spans.get(0, (0.0, solo_total))
        end = min(end, solo_total)
        take_total, _ = _probe(take.path)
        cut = (line.offset_s, min(line.end_s, take_total))
        _splice(
            take.path,
            solo,
            cut=cut,
            keep=(start, end),
            out=out,
            output_format=take.output_format,
        )
    finally:
        solo.unlink(missing_ok=True)

    duration = end - start
    delta = duration - (cut[1] - cut[0])
    lines = tuple(
        replace(ln, text=text, duration_s=duration)
        if ln.index == index
        else (replace(ln, offset_s=ln.offset_s + delta) if ln.index > index else ln)
        for ln in take.lines
    )
    turns = tuple(
        (role, text) if i == index else (role, t)
        for i, (role, t) in enumerate(take.turns)
    )
    new = replace(take, path=out, lines=lines, turns=turns)
    return (new, was_cached) if return_cache_status else new
