"""Sound design for a rendered production: foley chooses and places the effects (braidio#161).

braidio designs no sound of its own beyond the caller's music bed and the
structural stings. Sound effects are ``foley``'s job: :func:`foley.score` reads
the narration text, picks sounds under its fail-closed licence gate, aligns
them to the spoken words in the rendered audio, weaves them in and masters the
mix. This module is the one adapter from a braidio render to that call:

- :class:`SoundDesign` is the switch and its knobs. Its *absence* is "off": a
  render that is not given one is byte-for-byte what it was before.
- :func:`talk_segments` turns the render's talk spans (where each spoken beat
  sits on the episode, and what it says) into foley's segments: one per
  sentence, each with an estimated start, so foley's per-segment ``max_events``
  cap is per sentence (the taste ceiling) and its aligner searches the right
  stretch of audio rather than the whole file. Clips and stings contribute no
  segment, so no effect is anchored to a song excerpt.
- :func:`score_mix` runs the designer over the mix and writes the result.

foley is the optional ``braidio[sound]`` extra, imported only when a design
runs. The designer is a seam (``SoundDesign(designer=...)``), so a host can
bring its own and the tests run without foley.

>>> talk_segments([TalkSpan(start_s=2.0, duration_s=4.0, text="Rain fell. Door shut.")])
(['Rain fell.', 'Door shut.'], [2.0, 4.0])
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from braidio.pacing import split_units
from braidio.rights import Profile

#: The extra that installs the default designer.
SOUND_EXTRA = "sound"

#: foley's defaults, named so a render record can say what was asked for.
DFLT_MAX_EVENTS = 6
DFLT_MASTER = "podcast"

#: eleven_v3 audio tags (``[pause]``, ``[dryly]``) are performance directions, not
#: words: left in, foley would read ``[door creaks]`` as a cue nobody hears spoken.
_AUDIO_TAG = re.compile(r"\[[^\[\]]*\]")

#: The intermediate container foley's mix is written in before the MP3 encode.
_INTERMEDIATE_SUFFIX = ".flac"

#: Beside a sound-designed episode: what was designed, and the credits that must
#: ship with it (``episode.mp3`` → ``episode.sound.json``).
SOUND_SIDECAR_SUFFIX = ".sound.json"

#: What a design run spent is not known: foley reports no spend yet
#: (reeleehq/foley#280), though it may pay for an LLM rung or a generated
#: sound, capped by ``SoundDesign.max_usd``. Unknown, never ``0.0``.
COST_UNKNOWN = {"cost_usd": None, "cost_basis": "unknown"}

#: The designer: ``foley.score``'s signature (segments, then keyword options).
Designer = Callable[..., Any]


@dataclass(frozen=True)
class SoundDesign:
    """Sound design is on, with these knobs (foley's defaults throughout).

    ``max_events`` caps the sounds per *sentence*; ``master`` is foley's
    delivery target (it always masters the woven mix, and ``"podcast"`` is
    -16 LUFS / -1 dBTP, braidio's own default); ``max_usd`` caps what one run
    may spend (``None``: foley's default cap); ``allow_generate=False`` is
    retrieval only, with no paid generation fallback (offline and CI renders).
    The licence intent is not a knob here: it follows the render's rights
    profile (:meth:`commercial_ok`). ``designer`` is the strategy that does the
    work, called like :func:`foley.score` (default: foley itself); it is not
    part of the record.
    """

    max_events: int = DFLT_MAX_EVENTS
    master: str = DFLT_MASTER
    max_usd: float | None = None
    allow_generate: bool = True
    designer: Designer | None = field(default=None, compare=False, repr=False)

    @staticmethod
    def commercial_ok(profile: Profile) -> bool:
        """foley's licence intent for ``profile``: a published production admits
        only commercially-licensed sounds; a personal one also non-commercial.

        >>> SoundDesign.commercial_ok(Profile.PUBLISHED), SoundDesign.commercial_ok(Profile.PERSONAL)
        (True, False)
        """
        return Profile(profile) is Profile.PUBLISHED

    def to_dict(self) -> dict:
        """Plain JSON, for a render's settings record."""
        return {
            "max_events": self.max_events,
            "master": self.master,
            "max_usd": self.max_usd,
            "allow_generate": self.allow_generate,
        }

    def to_config(self, profile: Profile) -> dict:
        """The graph's ``sound-design-config/v1`` body (nw's schema) for a
        production rendered under ``profile``: these knobs, switched on, with
        the licence intent the profile implies.

        >>> SoundDesign(max_events=2).to_config(Profile.PERSONAL)["commercial_ok"]
        False
        """
        return {
            "enabled": True,
            **self.to_dict(),
            "commercial_ok": self.commercial_ok(profile),
        }


@dataclass(frozen=True)
class TalkSpan:
    """One spoken beat on the episode timeline: where it starts, how long it
    lasts, and what is said in it."""

    start_s: float
    duration_s: float
    text: str


def spoken_of(beat) -> str:
    """What a script beat says aloud: a narration's text, a dialogue's turns,
    ``""`` for anything else (a clip's words are the source's; a scene break
    says nothing).

    >>> from braidio.script import Dialogue, Narration
    >>> spoken_of(Narration("Hi.")), spoken_of(Dialogue(turns=[("a", "Yes."), ("b", "No.")]))
    ('Hi.', 'Yes. No.')
    """
    from braidio.script import Dialogue, Narration

    if isinstance(beat, Narration):
        return beat.text
    if isinstance(beat, Dialogue):
        return " ".join(text for _role, text in beat.turns)
    return ""


def talk_spans(beats, texts: Iterable[str]) -> list[TalkSpan]:
    """The spoken :class:`TalkSpan` of a timeline: its beat spans (anything with
    ``start`` and ``duration``) zipped with what each says, silent ones left out.
    """
    return [
        TalkSpan(start_s=beat.start, duration_s=beat.duration, text=text)
        for beat, text in zip(beats, texts)
        if text
    ]


def spoken_text(text: str) -> str:
    """``text`` without its audio tags, whitespace collapsed.

    >>> spoken_text("[dryly] Well. [pause] That went  well.")
    'Well. That went well.'
    """
    return " ".join(_AUDIO_TAG.sub(" ", text).split())


def talk_segments(spans: Iterable[TalkSpan]) -> tuple[list[str], list[float]]:
    """foley's ``(segments, segment_starts)`` for the talk on a timeline.

    Each span's sentences start where the span starts, then advance in
    proportion to the characters spoken before them. That is an estimate (the
    beat's start is exact, the sentence's is not), and it only has to put
    foley's aligner in the right stretch of audio.

    foley needs every worded segment to start strictly after the previous one.
    The weave crossfades consecutive beats, so a beat can start before the
    previous one ends: each span's sentences are therefore spread over its
    *window*, from its start to the next span's start (or its own end when
    that comes first), never over its full duration.

    >>> talk_segments([TalkSpan(0.0, 3.0, "One. Two two two."), TalkSpan(5.0, 1.0, "")])
    (['One.', 'Two two two.'], [0.0, 0.75])

    A long beat whose short last sentence would land after the crossfaded next
    beat's start stays inside its window:

    >>> talk_segments([TalkSpan(0.0, 12.0, "x" * 98 + ". No."), TalkSpan(11.88, 2.0, "Next.")])[1]
    [0.0, 11.531, 11.88]
    """
    worded = [
        (span, [s for para in split_units(spoken_text(span.text)) for s in para])
        for span in sorted(spans, key=lambda s: s.start_s)
    ]
    worded = [(span, sentences) for span, sentences in worded if sentences]
    segments: list[str] = []
    starts: list[float] = []
    for i, (span, sentences) in enumerate(worded):
        later = worded[i + 1][0].start_s if i + 1 < len(worded) else None
        window = span.duration_s
        if later is not None and later > span.start_s:
            window = min(window, later - span.start_s)
        total = sum(len(s) for s in sentences)
        offset = 0
        for sentence in sentences:
            segments.append(sentence)
            starts.append(round(span.start_s + window * offset / total, 3))
            offset += len(sentence)
    return segments, starts


@dataclass(frozen=True)
class ScoredMix:
    """What a sound-design pass made: the mix, and what must ship with it.

    ``credits`` must be published with the episode (licences of the sounds
    used); ``timeline`` is foley's editable sound-design timeline; ``captions_vtt``
    are SDH captions (sound cues only, no speech).
    """

    path: Path
    timeline: Mapping[str, Any] = field(default_factory=dict)
    credits: Mapping[str, Any] = field(default_factory=dict)
    captions_vtt: str = ""
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        """Plain JSON (everything but the path); foley's numpy scalars become
        Python numbers, so the record survives ``json.dumps`` as is."""
        return json.loads(
            json.dumps(
                {
                    "timeline": dict(self.timeline),
                    "credits": dict(self.credits),
                    "captions_vtt": self.captions_vtt,
                    "notes": list(self.notes),
                    **COST_UNKNOWN,
                },
                default=_jsonable,
            )
        )


def _jsonable(value):
    """``json.dumps`` fallback for numpy scalars and arrays (``item``/``tolist``)."""
    for attr in ("tolist", "item"):
        if hasattr(value, attr):
            return getattr(value, attr)()
    raise TypeError(f"not JSON-serializable: {type(value).__name__}")


def _foley_score(segments, **kwargs):
    """The default designer, :func:`foley.score`, importing foley on first use."""
    try:
        import foley
    except ModuleNotFoundError as exc:
        if exc.name != "foley":
            raise
        raise ModuleNotFoundError(
            f"Sound design needs foley: pip install 'braidio[{SOUND_EXTRA}]'.",
            name="foley",
        ) from exc
    if kwargs.get("max_usd") is not None:
        kwargs["budget"] = foley.Budget(max_usd=kwargs["max_usd"])
    kwargs.pop("max_usd", None)
    return foley.score(segments, **kwargs)


def _save_mix(woven, dst: Path) -> None:
    """Write a designer's mastered array (``woven.audio`` at ``woven.sr``) to
    ``dst``, an MP3 through ffmpeg. Needs soundfile, not foley, so a host's own
    designer works without foley installed."""
    try:
        import soundfile
    except ModuleNotFoundError as exc:
        if exc.name != "soundfile":
            raise
        raise ModuleNotFoundError(
            f"Writing a sound-designed mix needs soundfile: pip install "
            f"'braidio[{SOUND_EXTRA}]'.",
            name="soundfile",
        ) from exc
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH (brew install ffmpeg).")
    with tempfile.TemporaryDirectory() as tmp:
        lossless = Path(tmp) / f"mix{_INTERMEDIATE_SUFFIX}"
        soundfile.write(lossless, woven.audio, woven.sr)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(lossless), str(dst)],
            check=True,
            capture_output=True,
        )


def score_mix(
    audio: str | Path,
    spans: Sequence[TalkSpan],
    *,
    design: SoundDesign,
    profile: Profile,
    out_path: str | Path,
    save: Callable[[Any, Path], None] | None = None,
) -> ScoredMix:
    """Sound-design the mix at ``audio`` and write the result to ``out_path``.

    ``spans`` is the talk on the mix's timeline (:func:`talk_segments`). The
    design's ``designer`` (default :func:`foley.score`) is called with foley's
    keywords; ``save`` writes its ``weave`` result (default: an MP3 through
    ffmpeg). ``out_path`` may be ``audio`` itself: the mix is fully
    read before anything is written.

    Raises ``ValueError`` when there is no talk to design for, rather than
    hand foley nothing and report an undesigned mix as designed.
    """
    segments, starts = talk_segments(spans)
    if not segments:
        raise ValueError(
            "sound design: the production has no spoken text to place sounds on "
            "(only clips or stings); render it without sound_design."
        )
    scored = (design.designer or _foley_score)(
        segments,
        audio=str(audio),
        segment_starts=starts,
        commercial_ok=design.commercial_ok(profile),
        max_events=design.max_events,
        master=design.master,
        allow_generate=design.allow_generate,
        max_usd=design.max_usd,
    )
    woven = scored.weave
    if woven is None:
        raise RuntimeError(f"sound design: the designer returned no mix for {audio}.")
    out = Path(out_path)
    (save or _save_mix)(woven, out)
    return ScoredMix(
        path=out,
        timeline=scored.timeline.to_dict(),
        credits=_as_dict(woven.credits),
        captions_vtt=getattr(woven, "captions_vtt", "") or "",
        notes=tuple(str(n) for n in getattr(woven, "notes", ()) or ()),
    )


def _as_dict(value) -> dict:
    """foley's credits object as plain data (it has ``to_dict``; a dict passes)."""
    return dict(value.to_dict() if hasattr(value, "to_dict") else value or {})
