"""Markdown → narration beats, for reading a written document aloud.

``ingest_document`` returns plain text and no structure; reading an essay aloud
needs the structure (a title beat, longer pauses at headings, footnotes read at the
end) and the *speakable* text (no URLs, no emphasis markers, no image syntax). This
module owns that conversion so a read-aloud is one call:

>>> script = script_from_markdown("---\\ntitle: On Pace\\n---\\n# Part one\\n\\nSee [this](http://x.y) **now**.")
>>> [(b.style, b.text) for b in script.beats]
[('title', 'On Pace.'), ('heading', 'Part one'), ('para', 'See this now.')]

Tagging (``[pause]``, ``[dryly]``) stays the author's job: the beats are plain
text an agent can tag, and :func:`untagged_differences` verifies that tagging
changed nothing but tags. The conversion is pure — no network, no TTS, no cost.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from braidio.script import Narration, Script
from braidio.style import strip_audio_tags

#: Seconds of silence before each kind of beat (``Narration.lead_gap_s``). Headings
#: breathe; the title starts the file; footnotes are set apart from the body.
DEFAULT_GAPS: Mapping[str, float] = {
    "title": 0.0,
    "heading": 1.3,
    "para": 0.35,
    "list_item": 0.2,
    "quote": 0.5,
    "footnote": 1.0,
}
#: Spoken before each footnote (they are read at the end, out of their context).
DEFAULT_FOOTNOTE_LEAD_IN = "A footnote."
FOOTNOTES_MODES = ("end", "drop")
IMAGES_MODES = ("drop", "caption")

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_FOOTNOTE_DEF = re.compile(r"^\[\^[^\]]+\]:\s*")
_FOOTNOTE_REF = re.compile(r"\[\^[^\]]+\]")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)(.+?)\1")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_FENCE = re.compile(r"^\s*(```|~~~)")
_WS = re.compile(r"\s+")
_NON_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class _Block:
    kind: str  # "heading" | "para" | "list_item" | "quote" | "footnote_def" | "image"
    text: str


def _front_matter(md: str) -> tuple[dict[str, str], str]:
    """Split flat ``key: value`` front matter from the body."""
    m = _FRONT_MATTER.match(md)
    if not m:
        return {}, md
    pairs = (line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    meta = {k.strip().lower(): v.strip().strip("\"'") for k, v in pairs}
    return meta, md[m.end() :]


def _blocks(body: str) -> Iterable[_Block]:
    """Classify the body into blocks; code fences, rules and tables are not read."""
    body = _HTML_COMMENT.sub("", body)
    in_fence = False
    paragraph: list[str] = []

    def flush():
        if paragraph:
            text = " ".join(paragraph)
            paragraph.clear()
            if _FOOTNOTE_DEF.match(text):
                yield _Block("footnote_def", _FOOTNOTE_DEF.sub("", text))
            elif text.startswith("!["):
                yield _Block("image", text)
            else:
                yield _Block("para", text)

    for raw in body.splitlines():
        line = raw.rstrip()
        if _FENCE.match(line):
            yield from flush()
            in_fence = not in_fence
        elif in_fence or _RULE.match(line) or line.lstrip().startswith("|"):
            yield from flush()
        elif not line.strip():
            yield from flush()
        elif line.startswith("#"):
            yield from flush()
            yield _Block("heading", line.lstrip("#").strip())
        elif _LIST_MARKER.match(line):
            yield from flush()
            yield _Block("list_item", _LIST_MARKER.sub("", line))
        elif line.lstrip().startswith(">"):
            yield from flush()
            yield _Block("quote", line.lstrip().lstrip(">").strip())
        else:
            paragraph.append(line.strip())
    yield from flush()


def _speakable(text: str, *, keep_image_alt: bool = False) -> str:
    """Strip markdown syntax down to the words a voice should say."""
    text = _IMAGE.sub(r"\1" if keep_image_alt else "", text)
    text = _FOOTNOTE_REF.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _EMPHASIS.sub(r"\2", text)
    return _WS.sub(" ", text).strip()


def _respeller(respell: Mapping[str, str] | None):
    """A function applying ``respell`` in one pass, longest key first.

    One pass (a regex alternation), so a replacement is never itself re-matched by
    a shorter key (``AGENTS.md`` → ``AGENTS dot M D`` must not then hit ``AGENTS``).
    """
    if not respell:
        return lambda text: text
    keys = sorted(respell, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(k) for k in keys))
    return lambda text: pattern.sub(lambda m: respell[m.group(0)], text)


def _slug(text: str) -> str:
    return _NON_SLUG.sub("-", text.lower()).strip("-") or "document"


def script_from_markdown(
    md: str,
    *,
    voice: str | None = None,
    title: str | None = None,
    id_slug: str | None = None,
    gaps: Mapping[str, float] = DEFAULT_GAPS,
    respell: Mapping[str, str] | None = None,
    footnotes: str = "end",
    footnote_lead_in: str = DEFAULT_FOOTNOTE_LEAD_IN,
    images: str = "drop",
) -> Script:
    """Turn a markdown document into a :class:`Script` of narration beats.

    One beat per paragraph, heading, list item or quote. ``Narration.style``
    carries the kind (``title``/``heading``/``para``/``list_item``/``quote``/
    ``footnote``), so the timeline reports headings and footnotes separately, and
    ``lead_gap_s`` comes from ``gaps[kind]``.

    - Front matter ``title``/``subtitle`` become a title beat (``title=`` and the
      first ``#`` heading are the fallbacks for the script's name).
    - ``images``: ``"drop"`` (default) or ``"caption"`` (read the alt text as an aside).
    - ``footnotes``: ``"end"`` (default; each read after the body behind
      ``footnote_lead_in``) or ``"drop"``. Footnote markers are always removed.
    - ``respell`` maps text as written to text as spoken (``{"BDUF": "B-DUF"}``),
      applied to the beats only; the source markdown is untouched.
    - Links keep their text; emphasis/code markers are stripped; code fences,
      tables and rules are not read.
    """
    if footnotes not in FOOTNOTES_MODES:
        raise ValueError(
            f"footnotes must be one of {FOOTNOTES_MODES}, got {footnotes!r}"
        )
    if images not in IMAGES_MODES:
        raise ValueError(f"images must be one of {IMAGES_MODES}, got {images!r}")
    meta, body = _front_matter(md)
    blocks = list(_blocks(body))
    name = (
        title
        or meta.get("title")
        or next((b.text for b in blocks if b.kind == "heading"), None)
        or "Untitled"
    )
    respelled = _respeller(respell)
    entries: list[tuple[str, str]] = []
    if meta.get("title"):
        spoken_title = ". ".join(
            part.rstrip(".") for part in (meta["title"], meta.get("subtitle")) if part
        )
        entries.append(("title", spoken_title + "."))
    for b in blocks:
        if b.kind == "image":
            text = (
                _speakable(b.text, keep_image_alt=True) if images == "caption" else ""
            )
            kind = "para"
        else:
            text, kind = _speakable(b.text), b.kind
        if kind == "footnote_def":
            continue
        if text:
            entries.append((kind, text))
    if footnotes == "end":
        for b in blocks:
            if b.kind == "footnote_def" and _speakable(b.text):
                entries.append(("footnote", f"{footnote_lead_in} {_speakable(b.text)}"))
    beats = [
        Narration(respelled(text), voice=voice, lead_gap_s=gaps[kind], style=kind)
        for kind, text in entries
    ]
    return Script(title=name, id_slug=id_slug or _slug(name), beats=beats)


@dataclass(frozen=True)
class TagMismatch:
    """A beat whose text changed by more than audio tags."""

    index: int
    before: str
    after: str


def untagged_differences(before: Script, after: Script) -> list[TagMismatch]:
    """Where ``after`` differs from ``before`` by more than ``[audio tags]``.

    Compares narration beat by beat (whitespace-insensitive, tags removed from
    both). A different beat count is reported as one mismatch at the first
    unmatched index. Empty list ⇒ tagging changed nothing but tags.

    >>> a = Script("t", "t", [Narration("Hello there.")])
    >>> b = Script("t", "t", [Narration("[warmly] Hello there.")])
    >>> untagged_differences(a, b)
    []
    """

    def plain(script: Script) -> list[str]:
        return [
            _WS.sub(" ", strip_audio_tags(b.text)).strip()
            for b in script.beats
            if isinstance(b, Narration)
        ]

    old, new = plain(before), plain(after)
    mismatches = [
        TagMismatch(i, o, n) for i, (o, n) in enumerate(zip(old, new)) if o != n
    ]
    if len(old) != len(new):
        i = min(len(old), len(new))
        mismatches.append(
            TagMismatch(
                i, old[i] if i < len(old) else "", new[i] if i < len(new) else ""
            )
        )
    return mismatches
