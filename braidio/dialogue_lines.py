"""Addressable dialogue lines (braidio#145): one take, every turn re-voiceable.

:func:`render_dialogue_lines` is :func:`braidio.conversation.render_dialogue`'s
one Text-to-Dialogue pass, timed: it returns a :class:`DialogueTake` whose
:class:`DialogueLine`\\ s say where each turn sits in the take.
:func:`rerender_line` re-synthesizes ONE turn and splices it in, leaving every
other line's audio (and its cross-turn prosody) untouched and unbilled.
:func:`line_cost` quotes a line before it is rendered.

Three choices keep a take editable many times over:

- **A lossless master.** Beside the deliverable (``take.path``, in the take's
  ``output_format``) sits a FLAC master (``take.master``). Splices operate on
  the master; the deliverable is re-encoded from it after each edit. So an
  unedited line is always ONE lossy generation from the master, however many
  edits came before, instead of one more generation per edit.
- **Content-checked takes.** A take records the master's SHA-256. A take whose
  master has since changed (another edit wrote over it in place) is refused
  with :class:`StaleTakeError`, before any spend, rather than cut at offsets
  that no longer describe the file.
- **Cuts in the pauses, with fades.** A re-rendered line replaces the take from
  the middle of the pause before it to the middle of the pause after it (the
  file's edges for the first and last line), and brings the same amount of its
  own lead-in and decay along, so neither the old line's tail nor the new
  line's is lost. Each join gets mixing's join fade, out and in.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol, Sequence

from braidio.conversation import DEFAULT_CAST, ConversationCast
from braidio.cost import CostLine, CostRollup, billable_chars, tts_cost_usd
from braidio.tts import DialogueSegment, text_to_dialogue_with_timestamps

DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"

#: The lossless master's name beside the deliverable: ``take.mp3`` ->
#: ``take.mp3.master.flac``.
MASTER_SUFFIX = ".master.flac"

#: Float slack when checking that lines do not overlap: offsets are sums of
#: float durations, and a touching pair must not read as overlapping.
_TIME_EPS_S = 1e-6

#: The output formats a take can be spliced in: ElevenLabs' format family ->
#: (ffmpeg encoder, ffmpeg muxer). Raw ``pcm_``/``ulaw_``/``alaw_`` output has
#: no container ffmpeg could read back, so it is refused before any spend.
_SPLICE_CODECS: dict[str, tuple[str, str]] = {
    "mp3": ("libmp3lame", "mp3"),
    "wav": ("pcm_s16le", "wav"),
    "opus": ("libopus", "ogg"),
}


class DialogueTimingError(RuntimeError):
    """A timed take came back without the timings a line needs."""


class StaleTakeError(RuntimeError):
    """A take's master no longer holds the audio the take describes."""


@dataclass(frozen=True)
class DialogueLine:
    """One turn of a :class:`DialogueTake`: who says what, and where it sits.

    ``offset_s``/``duration_s`` are seconds into the take. They span the
    turn's voice segments (earliest start to latest end), so the pause
    *between* two turns belongs to neither line.
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

    ``path`` is the deliverable (in ``output_format``); ``master`` the lossless
    FLAC every splice operates on, and ``master_sha256`` its content hash, so
    a take whose master has changed since is refused instead of mis-cut.
    ``lines`` are the turns in order, timed (``turns`` is their ``(role, text)``
    view); ``cast`` and ``output_format`` are what :func:`rerender_line` needs to
    re-voice one.

    A take is checked when built: one line per turn, indexed ``0..n-1`` in
    order, each with its turn's role and text, every role in the cast, and the
    lines in time order without overlap.

    :meth:`to_dict`/:meth:`from_dict` round-trip it through JSON, so a host can
    store a take and re-render a line of it in a later request. A stored dict
    is data the host wrote: ``from_dict`` checks its consistency, NOT where
    ``path`` and ``master`` point. A host that keeps takes for several users
    must check both against its own root before trusting them, since
    :func:`rerender_line` writes to them.
    """

    path: Path
    lines: tuple[DialogueLine, ...]
    master: Path
    master_sha256: str
    cast: ConversationCast = DEFAULT_CAST
    output_format: str = DEFAULT_OUTPUT_FORMAT

    def __post_init__(self):
        _check_take(self)

    @property
    def turns(self) -> tuple[tuple[str, str], ...]:
        """``(role, text)`` per line: what was sent to the dialogue endpoint."""
        return tuple((ln.role, ln.text) for ln in self.lines)

    def to_dict(self) -> dict:
        """A JSON-able record of the take (no secret ever lives on a take)."""
        return {
            "path": str(self.path),
            "master": str(self.master),
            "master_sha256": self.master_sha256,
            "lines": [
                {
                    "index": ln.index,
                    "role": ln.role,
                    "text": ln.text,
                    "offset_s": ln.offset_s,
                    "duration_s": ln.duration_s,
                    "end_s": ln.end_s,
                }
                for ln in self.lines
            ],
            "cast": {
                "roles": dict(self.cast.roles),
                "model_id": self.cast.model_id,
                "settings": self.cast.settings,
            },
            "output_format": self.output_format,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DialogueTake:
        """The take :meth:`to_dict` recorded; raises ``ValueError`` if inconsistent."""
        cast = d["cast"]
        return cls(
            path=Path(d["path"]),
            master=Path(d["master"]),
            master_sha256=d["master_sha256"],
            lines=tuple(
                DialogueLine(**{k: ln[k] for k in _LINE_FIELDS}) for ln in d["lines"]
            ),
            cast=ConversationCast(
                roles=dict(cast["roles"]),
                model_id=cast["model_id"],
                settings=cast["settings"],
            ),
            output_format=d["output_format"],
        )


#: The stored fields of a line (``end_s`` is derived, so a stored one is ignored).
_LINE_FIELDS = ("index", "role", "text", "offset_s", "duration_s")


def _check_take(take: DialogueTake) -> None:
    unknown = sorted({ln.role for ln in take.lines} - set(take.cast.roles))
    if unknown:
        raise ValueError(f"the take's cast has no voice for role(s) {unknown}")
    prev_end = 0.0
    for i, ln in enumerate(take.lines):
        if ln.index != i:
            raise ValueError(f"line {i} must have index {i}; got index {ln.index}")
        if ln.duration_s <= 0 or ln.offset_s < prev_end - _TIME_EPS_S:
            raise ValueError(
                f"line {i} ({ln.offset_s:.3f}s + {ln.duration_s:.3f}s) is empty "
                f"or starts before line {i - 1} ends ({prev_end:.3f}s)"
            )
        prev_end = ln.end_s


# --- small helpers -------------------------------------------------------------


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH (brew install ffmpeg).")


def _require_text(turns) -> None:
    empty = [i for i, (_, text) in enumerate(turns) if not text.strip()]
    if empty:
        raise ValueError(f"turn(s) {empty} have no text to speak")


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
    """``(role, text)`` turns -> ``(voice_id, text)``, naming an unknown role."""
    missing = sorted({role for role, _ in turns} - set(cast.roles))
    if missing:
        raise KeyError(
            f"the cast has no voice for role(s) {missing}; it has {sorted(cast.roles)}"
        )
    return [(cast.roles[role], text) for role, text in turns]


def _probe(path: Path) -> tuple[float, int]:
    """``(duration_s, sample_rate)`` of an audio file."""
    from mixing.probe import probe

    info = probe(path, count_packets=False)
    if not info.has_audio or info.duration is None or not info.sample_rate:
        raise ValueError(f"{path} has no readable audio stream")
    return info.duration, info.sample_rate


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _master_path(deliverable: Path) -> Path:
    return deliverable.with_name(f"{deliverable.name}{MASTER_SUFFIX}")


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *args], check=True, capture_output=True
    )


def _to_master(src: Path, master: Path) -> None:
    """Decode ``src`` once into the lossless master (its own sample rate)."""
    _ffmpeg("-i", str(src), "-map", "0:a:0", "-c:a", "flac", "-f", "flac", str(master))


def _encode(master: Path, out: Path, output_format: str) -> None:
    """The deliverable: ``master`` encoded once in ``output_format``."""
    _ffmpeg("-i", str(master), *_encode_args(output_format), str(out))


# --- timings -------------------------------------------------------------------


def _input_spans(segments, n_inputs: int) -> dict[int, tuple[float, float]]:
    """Each input's span over its segments: earliest start, latest end."""
    spans: dict[int, tuple[float, float]] = {}
    for seg in segments:
        if not 0 <= seg.input_index < n_inputs:
            raise DialogueTimingError(
                f"a voice segment names input {seg.input_index}, "
                f"but only {n_inputs} turn(s) were sent"
            )
        lo, hi = spans.get(seg.input_index, (seg.start_s, seg.end_s))
        spans[seg.input_index] = (min(lo, seg.start_s), max(hi, seg.end_s))
    return spans


def _turn_spans(segments, n_turns: int) -> list[list[float]]:
    """Every turn's ``[start, end]``, in order and without overlap.

    Turns the API timed as overlapping (an interruption) are split at the
    midpoint of the overlap, so each line owns half of it. A turn that starts
    before the previous one, or lies wholly inside it (a backchannel spoken
    over the other voice), cannot be cut out on its own and raises
    :class:`DialogueTimingError`.
    """
    spans = _input_spans(segments, n_turns)
    missing = [i for i in range(n_turns) if i not in spans]
    if missing:
        raise DialogueTimingError(
            f"the dialogue take has no voice_segments for turn(s) {missing}, so "
            "those lines cannot be located in it; re-roll the take with "
            "refresh=True, or render those turns alone"
        )
    bounds = [list(spans[i]) for i in range(n_turns)]
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        if b[0] < a[0] or b[1] <= a[1]:
            raise DialogueTimingError(
                f"turn {i + 1} is timed inside or before turn {i} "
                f"({b[0]:.3f}-{b[1]:.3f}s vs {a[0]:.3f}-{a[1]:.3f}s), so it "
                "cannot be cut out as a line of its own"
            )
        if a[1] > b[0]:  # an overlap: each line gets half of it
            a[1] = b[0] = (a[1] + b[0]) / 2
    return bounds


def _lines(turns, bounds, *, total_s: float) -> tuple[DialogueLine, ...]:
    """The take's lines, each span clamped into ``[0, total_s]``."""
    lines = []
    for i, ((role, text), (start, end)) in enumerate(zip(turns, bounds)):
        start, end = max(0.0, start), min(end, total_s)
        if end <= start:
            raise DialogueTimingError(
                f"turn {i} is timed outside the take's {total_s:.3f}s of audio"
            )
        lines.append(DialogueLine(i, role, text, start, end - start))
    return tuple(lines)


# --- the splice (mixing's replace_span_file does the cutting) --------------------------------------------


def _insert_window(
    insert: Path, keep: tuple[float, float], *, rate: int
) -> "np.ndarray":
    """``insert[keep0:keep1]`` as float samples at ``rate``, exactly ``keep1 - keep0`` long.

    The window may reach outside the insert: what falls outside is silence, so a
    line that brings its lead-in or decay along from a pause it does not have
    still fills the span it replaces.
    """
    import numpy as np
    from mixing.decode import decode_float_chunks

    k0, k1 = keep
    n = round((k1 - k0) * rate)
    if n <= 0:
        raise ValueError(f"nothing to insert: keep window {keep} is empty")
    _, chunks = decode_float_chunks(insert, sample_rate=rate)
    audio = np.concatenate(list(chunks), axis=0)
    window = np.zeros((n, audio.shape[1]), dtype=audio.dtype)
    first = round(k0 * rate)  # sample of ``audio`` at the window's start (may be < 0)
    lo, hi = max(first, 0), min(first + n, len(audio))
    if hi > lo:
        window[lo - first : hi - first] = audio[lo:hi]
    return window


def _splice_line(
    base: Path,
    insert: Path,
    *,
    span: tuple[float, float],
    rate: int,
    keep: tuple[float, float],
    out: Path,
) -> None:
    """Write ``base[:span0] + insert[keep0:keep1] + base[span1:]`` to ``out``.

    ``rate`` is ``base``'s sample rate. The cutting, the faded joins (mixing's
    own join fade) and the single lossless encode are
    :func:`mixing.splice.replace_span_file`'s; what is braidio's is only which
    window of the re-rendered line goes in.
    """
    from mixing.splice import replace_span_file

    replace_span_file(base, span, _insert_window(insert, keep, rate=rate), output=out)


def _scratch(near: Path, tag: str, suffix: str) -> Path:
    """A fresh, unique, hidden scratch file beside ``near`` (empty, created)."""
    import tempfile

    fd, name = tempfile.mkstemp(
        dir=near.parent, prefix=f".{near.stem}{tag}-", suffix=suffix
    )
    os.close(fd)
    return Path(name)


# --- the seam: how one line is voiced ------------------------------------------


class LineSynthesizer(Protocol):
    """Voices ONE line: ``(audio, segments, was_cached)``.

    ``audio`` is in ``output_format``; ``segments`` must time input ``0`` (the
    line) in it; ``was_cached`` says whether no paid call was made. A
    synthesizer without native timings returns one segment spanning its
    voiced audio.
    """

    def __call__(
        self,
        voice_id: str,
        text: str,
        *,
        model_id: str,
        settings: dict | None,
        seed: int | None,
        output_format: str,
        api_key: str | None,
        cache,
        refresh: bool,
    ) -> tuple[bytes, Sequence[DialogueSegment], bool]: ...


def synthesize_line(
    voice_id: str,
    text: str,
    *,
    model_id: str,
    settings: dict | None,
    seed: int | None,
    output_format: str,
    api_key: str | None,
    cache,
    refresh: bool,
) -> tuple[bytes, Sequence[DialogueSegment], bool]:
    """The default :class:`LineSynthesizer`: a single-input timed Text-to-Dialogue call.

    Same voice, model and settings as the take, so the line matches it; billed
    for this line's characters only, and cached like any dialogue call.
    """
    return text_to_dialogue_with_timestamps(
        [(voice_id, text)],
        model_id=model_id,
        settings=settings,
        seed=seed,
        output_format=output_format,
        api_key=api_key,
        cache=cache,
        refresh=refresh,
        return_cache_status=True,
    )


# --- the API -------------------------------------------------------------------


def render_dialogue_lines(
    turns: list[tuple[str, str]],
    cast: ConversationCast = DEFAULT_CAST,
    *,
    out_path: str | Path,
    api_key: str | None = None,
    output_format: str = DEFAULT_OUTPUT_FORMAT,
    seed: int | None = None,
    cache=True,
    refresh: bool = False,
    return_cache_status: bool = False,
) -> DialogueTake | tuple[DialogueTake, bool]:
    """:func:`~braidio.conversation.render_dialogue`, as addressable lines.

    ONE Text-to-Dialogue pass (:func:`braidio.tts.text_to_dialogue_with_timestamps`),
    so the cross-turn prosody of ``render_dialogue`` is kept, and each turn's
    place in the take comes from the API's own voice segments (turns timed as
    overlapping are split at the overlap's midpoint; see :class:`DialogueLine`).
    Returns a :class:`DialogueTake`; :func:`rerender_line` re-voices one of its
    lines.

    ``out_path`` gets the API's audio as is; beside it, the lossless master
    (``MASTER_SUFFIX``) later edits splice. Unlike ``render_dialogue`` there is
    **no R-tight pass**: trimming the dead gaps would move every line after the
    first trim away from the timings the API reported.

    Billing, caching and the key are ``render_dialogue``'s: the whole
    exchange's characters, once; a cache hit (audio *and* timings, stored
    together) costs $0 and makes no API call, and ``return_cache_status=True``
    returns ``(take, was_cached)`` to say which. ``api_key`` reaches the
    ElevenLabs client only — not the cache key, not the take. An empty turn,
    an unknown role or an unspliceable ``output_format`` is refused before any
    spend; a response lacking some turn's timings raises
    :class:`DialogueTimingError` naming them (re-roll with ``refresh=True``).
    """
    turns = tuple((role, text) for role, text in turns)
    if not turns:
        raise ValueError("a dialogue take needs at least one turn")
    _require_text(turns)
    voices = _cast_voices(turns, cast)
    _encode_args(output_format)
    _require_ffmpeg()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    audio, segments, was_cached = text_to_dialogue_with_timestamps(
        voices,
        model_id=cast.model_id,
        settings=cast.settings,
        seed=seed,
        output_format=output_format,
        api_key=api_key,
        cache=cache,
        refresh=refresh,
        return_cache_status=True,
    )
    bounds = _turn_spans(segments, len(turns))
    out.write_bytes(audio)
    master = _master_path(out)
    _to_master(out, master)
    total_s, _ = _probe(master)
    take = DialogueTake(
        path=out,
        lines=_lines(turns, bounds, total_s=total_s),
        master=master,
        master_sha256=_sha256(master),
        cast=cast,
        output_format=output_format,
    )
    return (take, was_cached) if return_cache_status else take


def _line(take: DialogueTake, index: int) -> DialogueLine:
    if not 0 <= index < len(take.lines):
        raise IndexError(
            f"line {index} is not in the take (it has lines 0..{len(take.lines) - 1})"
        )
    return take.lines[index]


def _check_master(take: DialogueTake) -> None:
    if not take.master.is_file():
        raise StaleTakeError(f"the take's master {take.master} is missing")
    if _sha256(take.master) != take.master_sha256:
        raise StaleTakeError(
            f"the take's master {take.master} has changed since this take was "
            "made (a later edit wrote over it?); re-render from the take that "
            "edit returned"
        )


def _cut_points(take: DialogueTake, index: int, total_s: float):
    """Where line ``index`` is cut out of the take: mid-pause on each side.

    Returns ``((c0, c1), pre, post)``: ``c1`` is ``None`` for the last line
    (to the end of the take), and ``pre``/``post`` are how much room around
    the line's voice the cut removes, which the new line brings back.
    """
    line, lines = take.lines[index], take.lines
    c0 = 0.0 if index == 0 else (lines[index - 1].end_s + line.offset_s) / 2
    last = index == len(lines) - 1
    c1 = None if last else (line.end_s + lines[index + 1].offset_s) / 2
    post = (total_s if last else c1) - line.end_s
    return (c0, c1), line.offset_s - c0, max(0.0, post)


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
    synthesize: LineSynthesizer = synthesize_line,
) -> DialogueTake | tuple[DialogueTake, bool]:
    """Re-voice line ``index`` of ``take`` and splice it in; the others stay.

    The line (``text``, default its current text — put eleven_v3 ``[audio
    tags]`` in it to change the delivery) is voiced ALONE by ``synthesize``
    (default :func:`synthesize_line`: a single-input timed Text-to-Dialogue
    call with its role's voice and the cast's model and settings). Only its
    characters are billed (:func:`line_cost` quotes them first).

    The take is cut from the middle of the pause before the line to the
    middle of the pause after it (the file's edges for the first and last
    line), and the new voice goes in with as much of its own lead-in and
    decay as the cut removed, so the pauses keep their length and every later
    line moves by the change in the line's duration. Joins are faded
    (mixing's join fade). The splice runs on the lossless master; the
    deliverable is then encoded once from it in the take's ``output_format``.

    Refused before any spend: empty text, a role the cast lacks, an
    unspliceable format, and a stale take (:class:`StaleTakeError`: its master
    has changed since, typically because an in-place edit returned a newer
    take — use that one). A response without timings for the line raises
    :class:`DialogueTimingError`; it is cached, so re-roll with
    ``refresh=True``.

    Cached like any dialogue call: re-rendering the same text with the same
    ``seed`` is a $0 cache hit, so pass ``refresh=True`` (or a new ``seed``)
    to re-roll a line. ``return_cache_status=True`` returns
    ``(take, was_cached)``.

    Written to ``out_path`` (default: over ``take.path``, in place) with its
    master beside it. Returns the new :class:`DialogueTake`; ``take`` itself
    is a value and is not changed, but after an in-place edit it is stale.
    """
    line = _line(take, index)
    text = line.text if text is None else text
    _require_text([(line.role, text)])
    ((voice_id, _),) = _cast_voices([(line.role, text)], take.cast)
    _encode_args(take.output_format)
    _require_ffmpeg()
    _check_master(take)
    total_s, rate = _probe(take.master)
    cut, pre, post = _cut_points(take, index, total_s)
    out = Path(out_path) if out_path is not None else take.path
    out.parent.mkdir(parents=True, exist_ok=True)

    audio, segments, was_cached = synthesize(
        voice_id,
        text,
        model_id=take.cast.model_id,
        settings=take.cast.settings,
        seed=seed,
        output_format=take.output_format,
        api_key=api_key,
        cache=cache,
        refresh=refresh,
    )
    master = _master_path(out)
    # Unique per call, so two edits of one take cannot unlink each other's files.
    solo = _scratch(out, f"-line{index}", take.path.suffix)
    new_master = _scratch(out, "-master", ".flac")
    new_out = _scratch(out, "-encoded", out.suffix)
    try:
        solo.write_bytes(audio)
        solo_total, _ = _probe(solo)
        span = _input_spans(segments, 1).get(0)
        if span is None:
            raise DialogueTimingError(
                f"the re-render of line {index} came back without a voice segment "
                "for it, so its voice cannot be located; re-roll with refresh=True"
            )
        start, end = max(0.0, span[0]), min(span[1], solo_total)
        if end <= start:
            raise DialogueTimingError(
                f"the re-render of line {index} is timed outside its "
                f"{solo_total:.3f}s of audio"
            )
        _splice_line(
            take.master,
            solo,
            span=(cut[0], total_s if cut[1] is None else cut[1]),
            rate=rate,
            keep=(start - pre, end + post),
            out=new_master,
        )
        _encode(new_master, new_out, take.output_format)
        digest = _sha256(new_master)
        os.replace(new_master, master)  # the master first: a crash between the
        os.replace(new_out, out)  # two leaves a take that is refused, not wrong
    finally:
        for p in (solo, new_master, new_out):
            p.unlink(missing_ok=True)

    duration = end - start
    delta = duration - line.duration_s
    lines = tuple(
        replace(ln, text=text, duration_s=duration)
        if ln.index == index
        else (replace(ln, offset_s=ln.offset_s + delta) if ln.index > index else ln)
        for ln in take.lines
    )
    new = replace(take, path=out, master=master, master_sha256=digest, lines=lines)
    return (new, was_cached) if return_cache_status else new
