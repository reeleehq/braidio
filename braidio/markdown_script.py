"""Markdown → narration beats, for reading a written document aloud.

``ingest_document`` returns plain text and no structure; reading an essay aloud
needs the structure (a title beat, longer pauses at headings, footnotes read at the
end) and the *speakable* text (no URLs, no emphasis markers, no image syntax). This
module owns that conversion so a read-aloud is one call:

>>> script = script_from_markdown("---\\ntitle: On Pace\\n---\\n# Part one\\n\\nSee [this](http://x.y) **now**.")
>>> [(b.style, b.text) for b in script.beats]
[('title', 'On Pace.'), ('heading', 'Part one'), ('para', 'See this now.')]

Parsing is delegated to ``markdown-it-py`` (CommonMark, plus the footnote and
front-matter plugins of ``mdit-py-plugins``), an optional dependency
(``pip install 'braidio[markdown]'``) imported only when a document is converted,
so ``import braidio`` never needs it. This module only maps the token stream to
beats.

Tagging (``[pause]``, ``[dryly]``) stays the author's job: the beats are plain
text an agent can tag, and :func:`untagged_differences` verifies that tagging
changed nothing but tags. The conversion is pure — no network, no TTS, no cost.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from braidio.script import Narration, Script
from braidio.style import audio_tags, strip_audio_tags

#: Seconds of silence before each kind of beat (``Narration.lead_gap_s``). Headings
#: breathe; the title starts the file; footnotes are set apart from the body.
#: Read-only: a partial ``gaps=`` is merged over it, never mutates it.
DEFAULT_GAPS: Mapping[str, float] = MappingProxyType(
    {
        "title": 0.0,
        "heading": 1.3,
        "para": 0.35,
        "list_item": 0.2,
        "quote": 0.5,
        "footnote": 1.0,
    }
)
#: Spoken before each footnote (they are read at the end, out of their context).
DEFAULT_FOOTNOTE_LEAD_IN = "A footnote."
FOOTNOTES_MODES = ("end", "drop")
IMAGES_MODES = ("drop", "caption")
#: The pip extra that carries the parser.
MARKDOWN_EXTRA = "braidio[markdown]"

#: Block containers whose paragraphs make ONE beat (an item, a footnote), vs.
#: containers whose paragraphs each make their own beat (a quote).
_CONTAINER_KIND = {
    "list_item_open": "list_item",
    "blockquote_open": "quote",
    "footnote_open": "footnote",
}
_CONTAINER_CLOSE = {
    "list_item_close",
    "blockquote_close",
    "footnote_close",
}
_ACCUMULATING = frozenset({"list_item", "footnote"})
_AUTOLINK_MARKUPS = frozenset({"autolink", "linkify"})
_TASK_MARKER = re.compile(r"^\[[ xX]\]\s+")
_WS = re.compile(r"\s+")
_NON_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class _Block:
    kind: str  # "heading" | "para" | "list_item" | "quote" | "footnote"
    text: str


def _markdown_parser():
    """A CommonMark parser with footnotes, front matter, tables and strikethrough."""
    try:
        from markdown_it import MarkdownIt
        from mdit_py_plugins.footnote import footnote_plugin
        from mdit_py_plugins.front_matter import front_matter_plugin
    except ImportError as exc:
        raise ImportError(
            "script_from_markdown needs markdown-it-py and mdit-py-plugins: "
            f"pip install '{MARKDOWN_EXTRA}'"
        ) from exc
    return (
        MarkdownIt("commonmark")
        .use(front_matter_plugin)
        .use(footnote_plugin)
        .enable(["table", "strikethrough"])
    )


def _front_matter(tokens) -> dict[str, str]:
    """Flat ``key: value`` pairs from a leading front-matter token, if any."""
    fm = next((t.content for t in tokens if t.type == "front_matter"), "")
    pairs = (line.split(":", 1) for line in fm.splitlines() if ":" in line)
    return {k.strip().lower(): v.strip().strip("\"'") for k, v in pairs}


def _inline_text(children: Iterable, *, keep_image_alt: bool) -> str:
    """The words of an inline token run: markup, URLs, HTML and refs dropped."""
    parts: list[str] = []
    in_autolink = False
    for t in children or ():
        if t.type in ("link_open", "link_close") and t.markup in _AUTOLINK_MARKUPS:
            in_autolink = t.type == "link_open"
        elif in_autolink:
            continue
        elif t.type in ("text", "code_inline"):
            parts.append(t.content)
        elif t.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif t.type == "image" and keep_image_alt:
            parts.append(" " + _inline_text(t.children, keep_image_alt=True) + " ")
    return _WS.sub(" ", "".join(parts)).strip()


def _blocks(tokens, *, keep_image_alt: bool) -> Iterator[_Block]:
    """Map the block token stream to beats-to-be, in document order.

    A paragraph is a ``para``, or takes the kind of its innermost container: every
    paragraph of a list item (or footnote) joins into one block; each paragraph of
    a quote is its own. Tables, code, rules and HTML blocks are not read.
    """
    stack: list[tuple[str, list[str]]] = []  # (kind, accumulated texts)
    in_heading = in_table = False

    def flush_top() -> Iterator[_Block]:
        if stack and stack[-1][1]:
            kind, parts = stack[-1]
            text = " ".join(parts)
            parts.clear()
            if kind == "list_item":
                text = _TASK_MARKER.sub("", text)
            yield _Block(kind, text)

    for t in tokens:
        if t.type in _CONTAINER_KIND:
            yield from flush_top()
            stack.append((_CONTAINER_KIND[t.type], []))
        elif t.type in _CONTAINER_CLOSE:
            yield from flush_top()
            stack.pop()
        elif t.type in ("table_open", "table_close"):
            in_table = t.type == "table_open"
        elif t.type in ("heading_open", "heading_close"):
            in_heading = t.type == "heading_open"
        elif t.type == "inline" and not in_table:
            text = _inline_text(t.children, keep_image_alt=keep_image_alt)
            if not text:
                continue
            kind = "heading" if in_heading else (stack[-1][0] if stack else "para")
            if kind in _ACCUMULATING:
                stack[-1][1].append(text)
            else:
                yield from flush_top()
                yield _Block(kind, text)


def _respeller(respell: Mapping[str, str] | None):
    """A function applying ``respell`` in one pass, longest key first.

    One pass (a regex alternation), so a replacement is never itself re-matched by
    a shorter key (``AGENTS.md`` → ``AGENTS dot M D`` must not then hit ``AGENTS``).
    A key that starts (ends) with a word character only matches at a word boundary
    there, so ``{"AI": "A I"}`` leaves ``SAID`` alone.
    """
    if not respell:
        return lambda text: text

    def bounded(key: str) -> str:
        head = r"\b" if re.match(r"\w", key[:1]) else ""
        tail = r"\b" if re.match(r"\w", key[-1:]) else ""
        return head + re.escape(key) + tail

    keys = sorted(respell, key=len, reverse=True)
    pattern = re.compile("|".join(bounded(k) for k in keys))
    return lambda text: pattern.sub(lambda m: respell[m.group(0)], text)


def _slug(text: str) -> str:
    return _NON_SLUG.sub("-", text.lower()).strip("-") or "document"


def _check_modes(footnotes: str, images: str) -> None:
    if footnotes not in FOOTNOTES_MODES:
        raise ValueError(
            f"footnotes must be one of {FOOTNOTES_MODES}, got {footnotes!r}"
        )
    if images not in IMAGES_MODES:
        raise ValueError(f"images must be one of {IMAGES_MODES}, got {images!r}")


def _title_entry(meta: Mapping[str, str]) -> list[tuple[str, str]]:
    """The spoken title beat (``title. subtitle.``), if front matter has a title."""
    if not meta.get("title"):
        return []
    parts = (meta["title"], meta.get("subtitle"))
    return [("title", ". ".join(p.rstrip(".") for p in parts if p) + ".")]


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

    One beat per paragraph, heading, list item or quote paragraph.
    ``Narration.style`` carries the kind (``title``/``heading``/``para``/
    ``list_item``/``quote``/``footnote``), so the timeline reports headings and
    footnotes separately, and ``lead_gap_s`` comes from ``gaps[kind]`` (a partial
    ``gaps`` is merged over :data:`DEFAULT_GAPS`).

    - Front matter ``title``/``subtitle`` become a title beat (``title=`` and the
      first heading, as plain text, are the fallbacks for the script's name).
    - ``images``: ``"drop"`` (default) or ``"caption"`` (read the alt text inline).
    - ``footnotes``: ``"end"`` (default; each read after the body, in reference
      order, behind ``footnote_lead_in``) or ``"drop"``. Markers are always removed.
    - ``respell`` maps text as written to text as spoken (``{"BDUF": "B-DUF"}``),
      applied to the beats only; the source markdown is untouched.
    - Links keep their text (autolinks, being only a URL, are dropped); emphasis
      and code markers are stripped; HTML, code blocks, tables and rules are not
      read; entities are decoded.

    Needs ``markdown-it-py`` + ``mdit-py-plugins`` (``braidio[markdown]``).
    """
    _check_modes(footnotes, images)
    gap_of = {**DEFAULT_GAPS, **gaps}
    tokens = _markdown_parser().parse(md.replace("\r\n", "\n").replace("\r", "\n"))
    meta = _front_matter(tokens)
    blocks = list(_blocks(tokens, keep_image_alt=images == "caption"))
    first_heading = next((b.text for b in blocks if b.kind == "heading"), None)
    name = title or meta.get("title") or first_heading or "Untitled"
    entries = _title_entry(meta)
    entries += [(b.kind, b.text) for b in blocks if b.kind != "footnote"]
    if footnotes == "end":
        entries += [
            ("footnote", f"{footnote_lead_in} {b.text}")
            for b in blocks
            if b.kind == "footnote"
        ]
    respelled = _respeller(respell)
    beats = [
        Narration(respelled(text), voice=voice, lead_gap_s=gap_of[kind], style=kind)
        for kind, text in entries
    ]
    return Script(title=name, id_slug=id_slug or _slug(name), beats=beats)


@dataclass(frozen=True)
class TagMismatch:
    """A beat whose text changed by more than audio tags.

    ``index`` is the narration beat's position; ``-1`` means the non-narration
    beats (segments, dialogue, scene breaks) differ, with their counts as
    ``before``/``after``.
    """

    index: int
    before: str
    after: str


def _narration_texts(script: Script) -> list[str]:
    return [b.text for b in script.beats if isinstance(b, Narration)]


def _tag_only_change(old: str, new: str) -> bool:
    """``new`` is ``old`` plus tags: same words, and no bracket token of ``old`` lost.

    Without the second clause, a bracketed word already in the source
    (``[note]``) would be stripped from both sides and its deletion missed.
    """
    lost = Counter(audio_tags(old)) - Counter(audio_tags(new))

    def plain(text: str) -> str:
        return _WS.sub(" ", strip_audio_tags(text)).strip()

    return not lost and plain(old) == plain(new)


def untagged_differences(before: Script, after: Script) -> list[TagMismatch]:
    """Where ``after`` differs from ``before`` by more than ``[audio tags]``.

    Compares narration beat by beat (whitespace-insensitive, added tags ignored;
    a bracket token already in ``before`` must survive). A different narration
    count is reported as one mismatch at the first unmatched index; a different
    count of non-narration beats as one mismatch at index ``-1``. Empty list ⇒
    tagging changed nothing but tags.

    >>> a = Script("t", "t", [Narration("Hello there.")])
    >>> b = Script("t", "t", [Narration("[warmly] Hello there.")])
    >>> untagged_differences(a, b)
    []
    """
    old, new = _narration_texts(before), _narration_texts(after)
    mismatches = [
        TagMismatch(i, o, n)
        for i, (o, n) in enumerate(zip(old, new))
        if not _tag_only_change(o, n)
    ]
    if len(old) != len(new):
        i = min(len(old), len(new))
        mismatches.append(
            TagMismatch(
                i, old[i] if i < len(old) else "", new[i] if i < len(new) else ""
            )
        )
    n_old = len(before.beats) - len(old)
    n_new = len(after.beats) - len(new)
    if n_old != n_new:
        mismatches.append(TagMismatch(-1, str(n_old), str(n_new)))
    return mismatches
