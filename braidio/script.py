"""Composition model — the ordered beats a render walks.

A :class:`Script` is an ordered list of beats: a :class:`Narration` (spoken,
synthesized), a :class:`Dialogue` (a multi-voice exchange), a :class:`SegmentBeat`
(a *reference* to a span of source media to resolve and weave in), or a
:class:`SceneBreak` (a structural boundary — "new section" — that the renderer
marks with music, see :mod:`braidio.structure`). This is the generic,
media-agnostic projection a renderer consumes; how a reference maps to audio is a
:class:`braidio.sources.SegmentSource` concern, and what the beats are backed by
(lyrics, a graph, hand-authoring) is the consumer's concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union


@dataclass(frozen=True)
class Narration:
    """A spoken narration beat (authored, synthesized by TTS).

    ``published_text`` is an optional rights-safe rewrite the published profile
    uses when the default ``text`` quotes forbidden (e.g. copyrighted) content;
    leave it ``None`` when the narration is already clean.

    ``written`` is the text as authored, when ``text`` has been respelled for the
    voice (``BDUF`` → ``bee-duff``): ``text`` is what the voice receives,
    ``written`` is what a viewer should read, so captions show it (braidio#237).
    ``None`` means the two are the same.

    ``lead_gap_s`` prepends a beat of silence — breathing room before a
    register change (e.g. a book-read entering after a conversation), so it
    doesn't feel glued to the previous speaker.

    ``voice`` and ``voice_settings`` are per-beat overrides so one timeline can
    carry contrasting roles — e.g. a lively *presenter* and a graver *book
    narrator* (different voice, and/or a different delivery preset's
    ``voice_settings``). Both fall back to the render's production-level defaults
    when ``None``.
    """

    text: str
    style: str | None = None  # optional delivery hint (e.g. "book-passage")
    published_text: str | None = None
    lead_gap_s: float = 0.0
    voice: str | None = (
        None  # per-beat voice override (e.g. presenter vs book-narrator)
    )
    voice_settings: dict | None = None  # per-beat delivery override (e.g. V2_NARRATOR)
    written: str | None = (
        None  # on-screen form when ``text`` is respelled (braidio#237)
    )


CLIP_PLACEMENTS = ("before", "under", "after")


@dataclass(frozen=True)
class SegmentBeat:
    """A span of source media to weave in, addressed by an opaque ``reference``.

    The renderer resolves ``reference`` → ``[start,end)`` via a
    :class:`~braidio.sources.SegmentSource` and cuts it. ``rights`` (e.g.
    ``owned-local`` / ``copyrighted`` / ``public-domain``) drives the render
    profile; ``published_substitute`` is a transformative narration the
    published profile swaps in when the segment's audio can't be used.

    ``placement`` is how the clip sits against the talk (the weaving grammar):
    ``"before"`` (default) and ``"after"`` play the clip **clean** in its own
    slot — the set-up→clip and clip→payoff patterns, distinguished by where you
    order the beat relative to the talk. ``"under"`` plays the clip
    **concurrently beneath the following talk beat**, ducked by ``duck_db`` —
    the "host talks over the clip" technique (place the clip *immediately before*
    the talk it should sit under).

    ``spotlight`` is the fade-to-spotlight override: ``True`` drops the music
    bed out before this clip so it lands in silence rather than competing with
    underscore (the bed resumes after it); ``False`` keeps the bed under it.
    ``None`` (default) defers to the format's
    :attr:`braidio.structure.MusicStructure.spotlight_clips`. Without a music
    bed the flag is inert.
    """

    reference: str
    label: str = ""
    rights: str = "owned-local"
    published_substitute: str | None = None
    placement: str = "before"
    spotlight: bool | None = None

    def __post_init__(self) -> None:
        if self.placement not in CLIP_PLACEMENTS:
            raise ValueError(
                f"SegmentBeat.placement must be one of {CLIP_PLACEMENTS}, "
                f"got {self.placement!r}"
            )


@dataclass(frozen=True)
class Dialogue:
    """A multi-speaker commentary exchange (the conversational register).

    ``turns`` is an ordered tuple of ``(role, text)`` pairs (roles like
    ``"A"``/``"B"`` map to voices via a :class:`~braidio.conversation.ConversationCast`
    at render time). Rendered in ONE pass via Text-to-Dialogue so it sounds like
    people talking to each other. This is our own commentary → always publishable
    (its text is still scanned for forbidden verbatim quotes in the published cut).
    """

    turns: tuple[tuple[str, str], ...]
    label: str = ""


# How a SceneBreak is marked. "sting" plays the production's sting (a short
# musical marker) when one is supplied; "none" leaves a beat of silence. A beat's
# ``marker=None`` defers to the format default (MusicStructure.scene_marker).
SCENE_MARKERS = ("sting", "none")


@dataclass(frozen=True)
class SceneBreak:
    """A structural boundary between sections — the "new scene" beat.

    Audio has no visual white space, so a boundary has to be *heard*: the
    renderer marks it with a sting (a short musical marker) when the production
    supplies one and the resolved ``marker`` is ``"sting"``, and otherwise with
    a beat of silence. This is the beat the format templates' structure — a
    debate's open / rebuttal / close, a panel's rounds, a documentary's acts —
    is expressed with; the talk on either side is unchanged.

    ``marker`` overrides the format default per break (``None`` = defer to
    :attr:`braidio.structure.MusicStructure.scene_marker`). ``label`` names the
    section that starts here (e.g. ``"rebuttal"``) for the timeline breakdown.
    A scene break synthesizes nothing, so it costs nothing.
    """

    label: str = ""
    marker: str | None = None

    def __post_init__(self) -> None:
        if self.marker is not None and self.marker not in SCENE_MARKERS:
            raise ValueError(
                f"SceneBreak.marker must be one of {SCENE_MARKERS} or None, "
                f"got {self.marker!r}"
            )


Beat = Union[Narration, SegmentBeat, Dialogue, SceneBreak]


@dataclass(frozen=True)
class Script:
    """An ordered production script."""

    title: str
    id_slug: str  # stable id for this production (e.g. "01"); no media semantics
    beats: list[Beat] = field(default_factory=list)


def narration_segments(script: Script) -> list[str]:
    """All narration (default text) of a script, as sentence-level segments."""
    from braidio.multivoice import split_segments

    segs: list[str] = []
    for beat in script.beats:
        if isinstance(beat, Narration):
            segs.extend(split_segments(beat.text))
    return segs
