"""Style audit — flag the recycled rhetorical tics in commentary text.

The companion, in code, to ``misc/docs/style/anti-platitude-checklist.md``: scan
authored commentary for the empty structural platitudes a writing model falls
back on, so a production can self-check (and measure) instead of eyeballing.

    from braidio.style import audit_platitudes, platitude_rate
    findings = audit_platitudes(commentary_text)   # [{pattern, match, start}, …]
    rate = platitude_rate(commentary_text)          # flagged hits per 1000 words

Not every match is a crime — the guide says these moves are fine *sparingly*; the
value is the RATE and which patterns dominate. Chiasmus ("the X that did Y is the
X that does Z") isn't reliably regex-detectable and is intentionally omitted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: name → compiled pattern for the detectable overused moves.
PLATITUDE_PATTERNS: dict[str, re.Pattern] = {
    "director-cue": re.compile(
        r"\b(?:listen to|notice|watch|catch)\s+(?:how|what|the)\b", re.I
    ),
    "heres-the": re.compile(r"\bhere'?s the\b", re.I),
    "reduction": re.compile(
        r"\b(?:that'?s the whole|the whole \w+ in|in "
        r"(?:two|three|four|five|six|seven|eight|nine|ten|\d+) words)\b",
        re.I,
    ),
    "negation-just": re.compile(r"\bisn'?t just\b", re.I),
    "machinery-naming": re.compile(
        r"\bthe (?:turn|tell|button|trick|move|thesis)\b", re.I
    ),
}


@dataclass(frozen=True)
class Finding:
    """One flagged platitude."""

    pattern: str
    match: str
    start: int


def audit_platitudes(text: str) -> list[Finding]:
    """Return every :class:`Finding` in ``text``, in document order."""
    found: list[Finding] = []
    for name, rx in PLATITUDE_PATTERNS.items():
        for m in rx.finditer(text):
            found.append(Finding(name, m.group(0), m.start()))
    found.sort(key=lambda f: f.start)
    return found


def platitude_rate(text: str, *, per: int = 1000) -> float:
    """Flagged hits per ``per`` words (default 1000). 0.0 for empty text."""
    words = len(text.split())
    if not words:
        return 0.0
    return round(per * len(audit_platitudes(text)) / words, 2)


# --- expressiveness: how much performance is written into the text -----------
#
# ``[audio tags]`` are the single biggest lever on how alive a v3 read sounds.
# Measured on one sentence, one voice, pitch range (f0 p5-p95) as the proxy:
# plain text 70.8 Hz -> densely tagged 124.4 Hz, a 76% widening. By comparison
# ``voice_settings["stability"]`` moves it 85 Hz -> 72 Hz across its entire
# range. So "use tags sparingly" is advice that produces a flat, somniferous
# read; what you actually want is a *budget*, not abstinence.
#
# The failure mode at the other end is real but different: a tag on literally
# every sentence reads as kitsch, and it is the RELENTLESSNESS that gives it
# away, not any single tag. Hence a target band rather than a maximum.

_AUDIO_TAG_RE = re.compile(r"\[[a-z][a-z \-]{1,24}\]")

#: Tags per 100 words. Below the floor a v3 read goes flat; above the ceiling it
#: starts performing every clause and reads as camp. Derived from the measured
#: table above plus the "kitsch" complaint on a real episode.
TAG_RATE_FLOOR = 1.5
TAG_RATE_CEILING = 6.0


def strip_audio_tags(text: str) -> str:
    """``text`` with every inline ``[audio tag]`` removed (tags only; spacing kept)."""
    return _AUDIO_TAG_RE.sub("", text)


def audio_tags(text: str) -> list[str]:
    """Every inline ``[audio tag]`` in ``text``, in order."""
    return _AUDIO_TAG_RE.findall(text)


def audio_tag_rate(text: str, *, per: int = 100) -> float:
    """Inline audio tags per ``per`` words — the expressiveness dial.

    Only meaningful on a delivery whose model renders tags at all
    (:attr:`braidio.delivery.Delivery.supports_audio_tags`); on
    ``eleven_multilingual_v2`` the tags are inert text and this number is a lie.
    """
    words = len(text.split())
    if not words:
        return 0.0
    return len(audio_tags(text)) * per / words


def audit_expressiveness(text: str) -> list[str]:
    """Human-readable complaints about a script's written-in performance.

    Returns an empty list when the script sits in the target band. This is the
    gate :func:`audit_platitudes` is not: platitudes catch recycled *phrases*,
    this catches a script that will be read flatly however good the words are.
    """
    out: list[str] = []
    rate = audio_tag_rate(text)
    if rate < TAG_RATE_FLOOR:
        out.append(
            f"audio-tag rate {rate:.1f}/100 words is below {TAG_RATE_FLOOR} — "
            "this will read flat. Tags are the main expressiveness lever on v3; "
            "write the performance into the text."
        )
    elif rate > TAG_RATE_CEILING:
        out.append(
            f"audio-tag rate {rate:.1f}/100 words is above {TAG_RATE_CEILING} — "
            "a tag on every clause reads as kitsch. Cut to the moments that turn."
        )
    tags = audio_tags(text)
    if tags and len(set(tags)) < max(3, len(tags) // 6):
        out.append(
            f"only {len(set(tags))} distinct tags across {len(tags)} uses — "
            "the same gesture repeated is its own kind of monotony."
        )
    return out
