"""Timeline breakdown — what a production spends its time on, and in what order.

A rendered production is a walk of beats; this module turns that walk into a
queryable object: for each beat, its **kind**, **source interval** (for clips),
**rendered duration**, and **offset on the episode timeline** — plus per-kind
**totals** and a self-contained **HTML view**. It's built on the same layout math
(:func:`braidio.weave.layout_placed`) the renderer uses, so the offsets match the
actual mix.

Get one straight from the renderer::

    out, tl = render_production(script, source=src, return_timeline=True)
    tl.totals()          # {"clip": 78.6, "book-passage": 346.4, "narration": 616.5}
    tl.shares()          # same, as fractions
    open("anatomy.html", "w").write(tl.to_html("Episode anatomy"))

or build one from raw render data with :func:`build_timeline`. This is the
first-class, reusable form of what used to be reconstructed by ad-hoc scripts
(braidio#3): the render now *records* its timeline instead of anyone probing the
output after the fact.
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from braidio.weave import layout_placed

if TYPE_CHECKING:  # pragma: no cover - typing only
    from braidio.delivery import Delivery
    from braidio.weave_config import TurnOverlap, WeaveConfig

# Neutral kind → accent colour for the HTML view (falls back to a grey).
KIND_COLORS: dict[str, str] = {
    "clip": "#cf9526",
    "segment": "#cf9526",
    "song": "#cf9526",
    "book-passage": "#3f5183",
    "book": "#3f5183",
    "narration": "#a98f68",
    "commentary": "#a98f68",
    "dialogue": "#4c8a6a",
    "sting": "#7a5c99",
    "scene-break": "#7a5c99",
}
_FALLBACK_COLOR = "#8a8577"

# What a render without a WeaveConfig does to a narration beat: one TTS call.
_UNPACED_UNIT = "beat"


def _mmss(s: float) -> str:
    return f"{int(s) // 60}:{int(round(s)) % 60:02d}"


@dataclass(frozen=True)
class BeatSpan:
    """One beat's place on the timeline."""

    index: int
    kind: (
        str  # aggregation label: "clip" | "narration" | "book-passage" | "dialogue" | …
    )
    label: str = ""
    source_start: float | None = None  # clips: [start, end) in the source media
    source_end: float | None = None
    duration: float = 0.0  # rendered length (s)
    start: float = 0.0  # offset on the episode timeline (s)
    # "sequential" | "under" (layout_placed's), or None for a breakdown written
    # before it was recorded. An "under" beat overlays what follows it, so where
    # it is heard cannot be read off the order of the starts alone.
    placement: str | None = None

    @property
    def end(self) -> float:
        return round(self.start + self.duration, 3)


@dataclass(frozen=True)
class TimelineBreakdown:
    """The ordered beats of a production, with per-kind totals and an HTML view.

    ``settings`` is the **render record**: the settings that actually produced
    these timings (see :func:`render_settings`), or ``None`` for a breakdown
    built by hand. It exists because a render stopped being reproducible from
    the script alone the moment ``WeaveConfig.segmentation_unit`` became a real
    switch (braidio#63): the same script now renders to different durations
    under different pacing knobs, so a persisted breakdown whose settings are
    unknown cannot be re-derived, and any downstream data keyed to its timings
    (panel cues, captions) silently stops matching a later re-render. Recording
    the settings next to the timings closes that hole on the no-graph fast path,
    where the nw/lacing provenance layer is not in play at all.

    It is always plain JSON types, so a consumer that already persists the
    breakdown gets the record for free.
    """

    beats: tuple[BeatSpan, ...] = field(default_factory=tuple)
    title: str = ""
    settings: Mapping[str, Any] | None = None

    def totals(self) -> dict[str, float]:
        """Seconds spent per ``kind`` (insertion-ordered by first appearance)."""
        t: dict[str, float] = {}
        for b in self.beats:
            t[b.kind] = round(t.get(b.kind, 0.0) + b.duration, 3)
        return t

    @property
    def duration(self) -> float:
        """Total timeline length (s) — the max beat end, accounting for overlaps."""
        return round(max((b.end for b in self.beats), default=0.0), 3)

    def shares(self) -> dict[str, float]:
        """Fraction of spoken+clip time per ``kind`` (sums to 1)."""
        t = self.totals()
        tot = sum(t.values()) or 1.0
        return {k: v / tot for k, v in t.items()}

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "duration": self.duration,
            "totals": self.totals(),
            "settings": dict(self.settings) if self.settings is not None else None,
            "beats": [
                {
                    "index": b.index,
                    "kind": b.kind,
                    "label": b.label,
                    "source": (
                        [b.source_start, b.source_end]
                        if b.source_start is not None
                        else None
                    ),
                    "duration": b.duration,
                    "start": b.start,
                    "end": b.end,
                    # only when recorded, so an older breakdown round-trips to
                    # the same bytes and stales nothing
                    **({"placement": b.placement} if b.placement is not None else {}),
                }
                for b in self.beats
            ],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "TimelineBreakdown":
        """Rebuild a breakdown from :meth:`to_dict` output (the persisted form).

        ``totals`` and ``duration`` are derived, so they are recomputed rather
        than read back — the beats are the record.

        >>> tl = build_timeline(kinds=["narration", "clip"], durations=[4.0, 2.0],
        ...                     labels=["a", "b"], source_spans=[None, (1.0, 3.0)])
        >>> TimelineBreakdown.from_dict(tl.to_dict()) == tl
        True
        """
        beats = tuple(
            BeatSpan(
                index=int(b["index"]),
                kind=str(b["kind"]),
                label=str(b.get("label", "")),
                source_start=(b["source"][0] if b.get("source") else None),
                source_end=(b["source"][1] if b.get("source") else None),
                duration=float(b["duration"]),
                start=float(b["start"]),
                placement=(
                    b.get("placement")
                    if b.get("placement") in ("sequential", "under")
                    else None
                ),
            )
            for b in d.get("beats", ())
        )
        settings = d.get("settings")
        return cls(
            beats,
            title=str(d.get("title", "")),
            settings=dict(settings) if settings is not None else None,
        )

    def to_html(self, title: str | None = None, subtitle: str = "") -> str:
        """A self-contained HTML view: totals bar, walking-order timeline, table."""
        return _render_html(
            self, title if title is not None else (self.title or "Timeline"), subtitle
        )


def build_timeline(
    *,
    kinds: list[str],
    durations: list[float],
    placements: list[str] | None = None,
    labels: list[str] | None = None,
    source_spans: list[tuple[float, float] | None] | None = None,
    clip_edge_overlap_s: float = 0.5,
    narration_crossfade_s: float = 0.12,
    title: str = "",
    settings: Mapping[str, Any] | None = None,
    speakers: list[str | None] | None = None,
    overlap: "TurnOverlap | None" = None,
) -> TimelineBreakdown:
    """Assemble a :class:`TimelineBreakdown` from per-beat render data (pure).

    ``kinds`` are the aggregation labels (any string; ``"clip"`` is the only one
    the layout treats specially — as an overlapping segment). ``placements`` is
    the per-beat ``"sequential"``/``"under"`` used by the weave; offsets are
    computed with the same :func:`~braidio.weave.layout_placed` the renderer uses.

    ``speakers`` (one voice id per beat, ``None`` for a clip/sting) and ``overlap``
    place a change of speaker as an interruption — pass what the render passed.

    ``settings`` is the optional render record (:func:`render_settings`) — what
    produced these durations. It stays optional so building a timeline by hand
    (tests, a hand-cut episode, the captions/video doctests) needs nothing
    extra; a real render always passes one.
    """
    n = len(kinds)
    placements = placements or ["sequential"] * n
    labels = labels or [""] * n
    source_spans = source_spans or [None] * n
    layout_kinds = ["clip" if k == "clip" else "narration" for k in kinds]
    starts = layout_placed(
        layout_kinds,
        durations,
        placements,
        clip_edge_overlap_s=clip_edge_overlap_s,
        narration_crossfade_s=narration_crossfade_s,
        speakers=speakers,
        overlap=overlap,
    )
    spans = tuple(
        BeatSpan(
            index=i,
            kind=kinds[i],
            label=labels[i],
            source_start=(source_spans[i][0] if source_spans[i] else None),
            source_end=(source_spans[i][1] if source_spans[i] else None),
            duration=round(float(durations[i]), 3),
            start=round(float(starts[i]), 3),
            placement=placements[i],
        )
        for i in range(n)
    )
    return TimelineBreakdown(spans, title=title, settings=settings)


def render_settings(
    *,
    config: "WeaveConfig | None",
    crossfade_s: float,
    clip_edge_overlap_s: float,
    target_lufs: float,
    duck_db: float,
    delivery: "Delivery | None" = None,
    profile: Any = None,
    normalize: bool = True,
) -> dict[str, Any]:
    """The record of *how* a production was rendered, as plain JSON types.

    Why it exists: since braidio#63 the pacing knobs are real, so two renders of
    one script can differ by minutes — and until this, nothing on disk said which
    settings produced which file. This is the cheap, no-graph answer: the
    renderer hands it to the :class:`TimelineBreakdown` it already returns, so a
    consumer that persists the breakdown persists the settings with it.

    The whole resolved :class:`~braidio.weave_config.WeaveConfig` goes in under
    ``"weave"`` rather than a hand-picked subset — adding a knob then keeps the
    record correct for free, where a curated list would quietly drift.
    ``"resolved"`` carries the values the render *actually used*, which are not
    always the config's: a caller may pass no config at all (``"weave"`` is then
    ``None``, and the pacing knobs were never consulted — each beat was one TTS
    call), the concat path resolves its crossfade from either the config or the
    ``crossfade_s`` argument, and ``normalize`` is a render argument rather than
    a weave choice.

    >>> from braidio.delivery import V2_TUNED
    >>> from braidio.weave_config import WeaveConfig
    >>> rec = render_settings(
    ...     config=WeaveConfig(segmentation_unit="sentence", gap_turn_s=0.28),
    ...     crossfade_s=0.12, clip_edge_overlap_s=0.5,
    ...     target_lufs=-16.0, duck_db=-15.0,
    ...     delivery=V2_TUNED, profile="published",
    ... )
    >>> rec["weave"]["gap_turn_s"], rec["resolved"]["paces_narration"]
    (0.28, True)
    >>> rec["delivery"]["model_id"], rec["profile"]
    ('eleven_multilingual_v2', 'published')
    """
    return {
        "weave": config.to_dict() if config is not None else None,
        "delivery": (
            None
            if delivery is None
            else {
                "name": delivery.name,
                "model_id": delivery.model_id,
                "voice_settings": dict(delivery.voice_settings),
            }
        ),
        # An enum in the renderer, a plain string anywhere else.
        "profile": (
            None if profile is None else str(getattr(profile, "value", profile))
        ),
        "resolved": {
            "segmentation_unit": (
                config.segmentation_unit if config is not None else _UNPACED_UNIT
            ),
            "paces_narration": bool(config is not None and config.paces_narration),
            "crossfade_s": float(crossfade_s),
            "clip_edge_overlap_s": float(clip_edge_overlap_s),
            "target_lufs": float(target_lufs),
            "duck_db": float(duck_db),
            "normalize": bool(normalize),
        },
    }


def _render_html(tl: TimelineBreakdown, title: str, subtitle: str) -> str:
    totals = tl.totals()
    grand = sum(totals.values()) or 1.0

    def color(k: str) -> str:
        return KIND_COLORS.get(k, _FALLBACK_COLOR)

    stack = "".join(
        f'<div style="flex:{v} 1 0;background:{color(k)}" title="{_html.escape(k)}: {_mmss(v)}"></div>'
        for k, v in totals.items()
        if v > 0
    )
    chips = "".join(
        f'<div class="chip"><span class="sw" style="background:{color(k)}"></span>'
        f"{_html.escape(k)} <b>{_mmss(v)}</b> · {round(100 * v / grand)}%</div>"
        for k, v in totals.items()
        if v > 0
    )
    total_dur = tl.duration
    segs = "".join(
        f'<div class="seg" style="flex:{max(b.duration, 0.01)} 1 0;background:{color(b.kind)};'
        f'{"min-width:6px;" if b.kind == "clip" else ""}" '
        f'title="{b.index}. {_html.escape(b.kind)} · {b.duration:.1f}s'
        f"{(' · song ' + _mmss(b.source_start) + chr(45) + _mmss(b.source_end)) if b.source_start is not None else ''}"
        f'{(chr(10) + _html.escape(b.label)) if b.label else ""}"></div>'
        for b in tl.beats
    )
    rows = "".join(
        f"<tr><td class=num>{b.index}</td>"
        f'<td><span class="sw" style="background:{color(b.kind)}"></span>{_html.escape(b.kind)}</td>'
        f"<td class=num>{_mmss(b.start)}</td><td class=num>{b.duration:.1f}s</td>"
        f"<td class=num>{(_mmss(b.source_start) + '–' + _mmss(b.source_end)) if b.source_start is not None else '—'}</td>"
        f"<td>{_html.escape(b.label)}</td></tr>"
        for b in tl.beats
    )
    sub = f'<p class="sub">{_html.escape(subtitle)}</p>' if subtitle else ""
    return f"""<!doctype html><meta charset=utf-8><title>{_html.escape(title)}</title>
<style>
:root{{--bg:#f4f2ec;--fg:#26231d;--soft:#6b6558;--hair:#dcd7cc;--card:#fbfaf6}}
@media(prefers-color-scheme:dark){{:root{{--bg:#161512;--fg:#ece7db;--soft:#a49c8b;--hair:#322f28;--card:#1c1a16}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
.wrap{{max-width:900px;margin:0 auto;padding:2.5rem 1.25rem 4rem}}
h1{{font:600 1.7rem/1.1 "Iowan Old Style",Palatino,Georgia,serif;margin:0 0 .3rem;letter-spacing:-.01em}}
.sub{{color:var(--soft);margin:0 0 1.5rem}}
.card{{background:var(--card);border:1px solid var(--hair);border-radius:12px;padding:1.2rem;margin-bottom:1.5rem}}
.stack{{display:flex;height:34px;border-radius:7px;overflow:hidden;gap:2px;background:var(--bg)}}
.chips{{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.9rem}}
.chip{{font-size:.82rem;color:var(--soft);display:flex;align-items:center;gap:.4rem}}
.chip b{{color:var(--fg)}}
.sw{{width:10px;height:10px;border-radius:3px;display:inline-block;flex:none}}
.lbl{{font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;color:var(--soft);margin:0 0 .5rem;font-weight:600}}
.track{{display:flex;gap:1.5px;height:32px;border-radius:6px;overflow:hidden;background:var(--bg);box-shadow:inset 0 0 0 1px var(--hair)}}
.seg{{min-width:2px}}.seg:hover{{filter:brightness(1.12)}}
.axis{{display:flex;justify-content:space-between;font:.68rem ui-monospace,Menlo,monospace;color:var(--soft);margin-top:.35rem}}
.scroll{{overflow-x:auto;border:1px solid var(--hair);border-radius:10px;margin-top:1.5rem}}
table{{border-collapse:collapse;width:100%;font-size:.86rem}}
th{{text-align:left;font-size:.68rem;letter-spacing:.08em;text-transform:uppercase;color:var(--soft);padding:.55rem .8rem;background:var(--bg);border-bottom:1px solid var(--hair);white-space:nowrap}}
td{{padding:.5rem .8rem;border-bottom:1px solid var(--hair);vertical-align:top}}
tr:last-child td{{border-bottom:0}}
.num{{text-align:right;font-variant-numeric:tabular-nums;font-family:ui-monospace,Menlo,monospace;white-space:nowrap}}
</style>
<div class=wrap>
<h1>{_html.escape(title)}</h1>{sub}
<div class=card><div class=lbl>Time by kind · {_mmss(total_dur)} total</div>
<div class=stack>{stack}</div><div class=chips>{chips}</div></div>
<div class=lbl>Walking order — width ∝ duration</div>
<div class=track>{segs}</div>
<div class=axis><span>0:00</span><span>{_mmss(total_dur)}</span></div>
<div class=scroll><table><thead><tr><th class=num>#</th><th>Kind</th><th class=num>At</th><th class=num>Length</th><th class=num>In source</th><th>Label</th></tr></thead>
<tbody>{rows}</tbody></table></div>
</div>
"""
