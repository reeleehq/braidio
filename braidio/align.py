"""Time an authored script on narration braidio did not render (braidio#212).

:func:`~braidio.captions.captions_for` and :func:`~braidio.video.plan_spans` both
need the :class:`~braidio.timeline.TimelineBreakdown` of braidio's *own* render.
Narration made elsewhere has none: a long script voiced track by track through
another TTS, or a recording somebody made by hand. This module builds that
timeline from the audio's **measured word times** (any word-level transcript of
it: ElevenLabs Scribe, Whisper, a forced aligner), while the words that appear in
a caption stay the **authored** ones. Only the *times* are measured; a recogniser
that heard "mate" for «Matte.» never reaches a subtitle.

The one entry point is :func:`align_script`::

    alignment = align_script(script_or_text, words)
    alignment.timeline          # plugs into braidio.video.plan_spans
    format_srt(alignment.cues)  # or captions_from_cues(alignment.cues, max_chars=42)

Free, offline, pure and dependency-free (stdlib ``difflib``): part of the
functional core.

Seams (keyword-only, each defaulting to the strongest built-in):

- ``normalize``: how a text is reduced to comparable tokens (default: lowercase,
  accents folded, letters and digits only).
- ``unit``: what a timeline beat is, one per ``"paragraph"`` or one per ``"beat"``.
- ``max_chars``: the longest caption cue before it is cut at whitespace.

Examples:
    >>> words = [
    ...     {"text": "Hello", "start": 0.5, "end": 0.9},
    ...     {"text": "there.", "start": 0.9, "end": 1.4},
    ...     {"text": "Goodbye", "start": 3.0, "end": 3.4},
    ... ]
    >>> a = align_script("Hello there.\\n\\nGoodbye.", words, duration_s=4.0)
    >>> [(c.text, c.start_s, c.end_s) for c in a.cues]
    [('Hello there.', 0.5, 1.4), ('Goodbye.', 3.0, 3.4)]
    >>> [(b.start, b.duration) for b in a.timeline.beats]
    [(0.0, 3.0), (3.0, 1.0)]
    >>> a.unmatched
    ()
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from braidio.captions import Cue, _wrap
from braidio.script import Dialogue, Narration, Script
from braidio.timeline import TimelineBreakdown, build_timeline

__all__ = ["Alignment", "align_script", "tokens"]

#: Timeline beat granularities :func:`align_script` knows.
UNITS = ("paragraph", "beat")

#: Longest caption cue (chars) before it is cut at whitespace.
DEFAULT_MAX_CHARS = 84

#: Where a sentence ends: end punctuation (or a closing guillemet), whitespace,
#: then a capital or an opening quote. The text is authored, so no NLP splitter.
_SENTENCE_END = re.compile(r"(?<=[.!?…»])\s+(?=[A-ZÀ-Ý«\"“])")

#: Markup that is not spoken: ``[audio tags]`` and SSML ``<break .../>`` tags.
_UNSPOKEN = re.compile(r"\[[^\]]*\]|<[^>]*>")

#: A blank line separates paragraphs.
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")

#: What a missed chunk is given, placed between its neighbours (seconds).
_MISSED_GAP_S = 0.2
_MISSED_MIN_S = 0.2
_MISSED_MAX_S = 2.0

#: Word-list entry types that are not speech (Scribe's ``spacing`` / ``audio_event``).
_NON_SPEECH = frozenset({"spacing", "audio_event"})

#: Beat kind recorded on the timeline.
_KIND = "narration"


class Alignment(NamedTuple):
    """What :func:`align_script` measured.

    ``timeline``: one sequential beat per unit, contiguous from 0 to the end of
    the audio (a beat is its text plus the silence after it, the first also
    holds the lead-in), so ``plan_spans`` and ``captions`` take it as they take a
    render's. ``cues``: caption cues carrying the authored text at measured
    times, never overlapping. ``unmatched``: the chunks no recognised word
    matched, whose times were interpolated between their neighbours.
    """

    timeline: TimelineBreakdown
    cues: tuple[Cue, ...]
    unmatched: tuple[str, ...]


def tokens(text: str) -> list[str]:
    """Comparable tokens of ``text``: lowercase, accents folded, letters and digits.

    >>> tokens("Été, déjà-vu 2!")
    ['ete', 'deja', 'vu', '2']
    """
    folded = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in folded if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", stripped)


def align_script(
    script_or_text: Script | str,
    words: Iterable[Mapping[str, Any]],
    *,
    duration_s: float | None = None,
    unit: str = "paragraph",
    max_chars: int = DEFAULT_MAX_CHARS,
    normalize: Callable[[str], list[str]] = tokens,
    title: str = "",
) -> Alignment:
    """Align the authored ``script_or_text`` to the measured ``words`` of its audio.

    Args:
        script_or_text: a :class:`~braidio.script.Script` (its narration and
            dialogue beats; segment beats are other audio and are skipped) or
            plain text, paragraphs separated by a blank line. ``[tags]`` and
            ``<break/>`` markup is not spoken and is dropped.
        words: ``{"text" (or "word"), "start", "end"}`` mappings in playback
            order, times in seconds on the audio's own clock. Entries whose
            ``type`` is ``spacing`` / ``audio_event`` are ignored. For audio
            made in several tracks, offset each track's words and concatenate.
        duration_s: length of the audio; the last beat runs to it. Default: the
            end of the last cue.
        unit: ``"paragraph"`` (a timeline beat per paragraph) or ``"beat"`` (one
            per script beat; for plain text, the whole text).
        max_chars: cue length cap, cut at whitespace.
        normalize: text to comparable tokens, see :func:`tokens`.
        title: the timeline's title.

    Returns:
        An :class:`Alignment`.

    Raises:
        ValueError: ``unit`` unknown, no text to align, or no usable words.
    """
    if unit not in UNITS:
        raise ValueError(f"unit must be one of {UNITS}, got {unit!r}")
    spoken = _speech(words)
    if not spoken:
        raise ValueError("align_script needs at least one word with a start and end")
    units = _units(script_or_text, unit)
    chunks = [  # (unit index, caption text)
        (i, piece)
        for i, text in enumerate(units)
        for piece in _cue_pieces(text, max_chars)
    ]
    if not chunks:
        raise ValueError("align_script found no text to align")

    spans, unmatched = _chunk_times(
        [piece for _, piece in chunks], spoken, normalize=normalize
    )
    cues = _non_overlapping(
        [Cue(s, e, piece) for (_, piece), (s, e) in zip(chunks, spans)]
    )
    timeline = _timeline(
        units,
        [i for i, _ in chunks],
        cues,
        total=duration_s if duration_s is not None else cues[-1].end_s,
        title=title,
    )
    return Alignment(timeline, tuple(cues), tuple(unmatched))


def _speech(words) -> list[tuple[str, float, float]]:
    """``(raw text, start, end)`` of the spoken entries, in the order given."""
    out = []
    for w in words:
        if w.get("type") in _NON_SPEECH:
            continue
        text = w.get("text", w.get("word"))
        start, end = w.get("start"), w.get("end")
        if not text or start is None or end is None:
            continue
        out.append((str(text), float(start), float(end)))
    return out


def _clean(text: str) -> str:
    return " ".join(_UNSPOKEN.sub(" ", text).split())


def _units(script_or_text, unit: str) -> list[str]:
    """The texts that become timeline beats."""
    if isinstance(script_or_text, str):
        texts = [script_or_text]
    elif isinstance(script_or_text, Script):
        texts = [t for t in map(_beat_text, script_or_text.beats) if t]
    else:
        raise TypeError("script_or_text must be a Script or a str")
    if unit == "paragraph":
        texts = [p for t in texts for p in _PARAGRAPH_BREAK.split(t)]
    return [c for c in map(_clean, texts) if c]


def _beat_text(beat) -> str:
    if isinstance(beat, Narration):
        return beat.text
    if isinstance(beat, Dialogue):
        return "\n\n".join(text for _, text in beat.turns)
    return ""  # a segment beat is other audio, not narration to time


def _cue_pieces(text: str, max_chars: int) -> list[str]:
    pieces = [s for s in _SENTENCE_END.split(text) if s]
    if max_chars > 0:
        pieces = [p for piece in pieces for p in _wrap(piece, max_chars)]
    return pieces


def _chunk_times(chunks, spoken, *, normalize):
    """``([(start, end)] per chunk, [unmatched chunk text])``.

    The authored tokens are walked against the recognised ones in order
    (:class:`difflib.SequenceMatcher`); a chunk takes the times of the words that
    matched it, and a chunk nothing matched is placed between its neighbours.
    """
    heard, heard_times = [], []
    for text, start, end in spoken:
        for tok in normalize(text):
            heard.append(tok)
            heard_times.append((start, end))
    authored, owner = [], []
    for ci, chunk in enumerate(chunks):
        for tok in normalize(chunk):
            authored.append(tok)
            owner.append(ci)
    matcher = difflib.SequenceMatcher(a=authored, b=heard, autojunk=False)
    times: dict[int, tuple[float, float]] = {}
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            lo, hi = heard_times[b + k]
            cur = times.get(owner[a + k])
            times[owner[a + k]] = (
                (lo, hi) if cur is None else (min(cur[0], lo), max(cur[1], hi))
            )
    unmatched = []
    for ci, chunk in enumerate(chunks):
        if ci in times:
            continue
        unmatched.append(chunk)
        previous_end = times.get(ci - 1, (0.0, 0.0))[1]
        next_start = next(
            (times[k][0] for k in range(ci + 1, len(chunks)) if k in times),
            previous_end + _MISSED_MAX_S,
        )
        start = previous_end + _MISSED_GAP_S
        end = max(
            previous_end + _MISSED_MIN_S,
            min(next_start - _MISSED_GAP_S, previous_end + _MISSED_MAX_S),
        )
        times[ci] = (round(start, 3), round(max(end, start), 3))
    return [times[ci] for ci in range(len(chunks))], unmatched


def _non_overlapping(cues: Sequence[Cue]) -> list[Cue]:
    """Each cue starts no earlier than the one before it ended."""
    out: list[Cue] = []
    for cue in cues:
        start = max(cue.start_s, out[-1].end_s if out else 0.0)
        out.append(Cue(start, max(cue.end_s, start), cue.text))
    return out


def _timeline(units, owners, cues, *, total, title) -> TimelineBreakdown:
    """One beat per unit: from its first cue to the next unit's first cue."""
    first = {}
    for owner, cue in zip(owners, cues):
        first.setdefault(owner, cue.start_s)
    present = [i for i in range(len(units)) if i in first]
    bounds = [0.0] + [first[i] for i in present[1:]] + [max(total, cues[-1].end_s)]
    durations = [max(b - a, 0.0) for a, b in zip(bounds, bounds[1:])]
    return build_timeline(
        kinds=[_KIND] * len(present),
        durations=durations,
        labels=[units[i][:40] for i in present],
        narration_crossfade_s=0.0,
        title=title,
    )
