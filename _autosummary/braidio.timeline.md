# braidio.timeline

Timeline breakdown — what a production spends its time on, and in what order.

A rendered production is a walk of beats; this module turns that walk into a
queryable object: for each beat, its **kind**, **source interval** (for clips),
**rendered duration**, and **offset on the episode timeline** — plus per-kind
**totals** and a self-contained **HTML view**. It’s built on the same layout math
([`braidio.weave.layout_placed()`](braidio.weave.md#braidio.weave.layout_placed)) the renderer uses, so the offsets match the
actual mix.

Get one straight from the renderer:

```default
out, tl = render_production(script, source=src, return_timeline=True)
tl.totals()          # {"clip": 78.6, "book-passage": 346.4, "narration": 616.5}
tl.shares()          # same, as fractions
open("anatomy.html", "w").write(tl.to_html("Episode anatomy"))
```

or build one from raw render data with [`build_timeline()`](#braidio.timeline.build_timeline). This is the
first-class, reusable form of what used to be reconstructed by ad-hoc scripts
(braidio#3): the render now *records* its timeline instead of anyone probing the
output after the fact.

### Functions

| [`build_timeline`](#braidio.timeline.build_timeline)(\*, kinds, durations[, ...])   | Assemble a [`TimelineBreakdown`](#braidio.timeline.TimelineBreakdown) from per-beat render data (pure).   |
|------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| [`render_settings`](#braidio.timeline.render_settings)(\*, config, crossfade_s, ...) | The record of *how* a production was rendered, as plain JSON types.                                               |

### Classes

| [`BeatSpan`](#braidio.timeline.BeatSpan)(index, kind[, label, source_start, ...])   | One beat's place on the timeline.                                         |
|------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`TimelineBreakdown`](#braidio.timeline.TimelineBreakdown)([beats, title, settings])         | The ordered beats of a production, with per-kind totals and an HTML view. |

### *class* braidio.timeline.BeatSpan(index, kind, label='', source_start=None, source_end=None, duration=0.0, start=0.0, placement=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One beat’s place on the timeline.

### *class* braidio.timeline.TimelineBreakdown(beats=<factory>, title='', settings=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The ordered beats of a production, with per-kind totals and an HTML view.

`settings` is the **render record**: the settings that actually produced
these timings (see [`render_settings()`](#braidio.timeline.render_settings)), or `None` for a breakdown
built by hand. It exists because a render stopped being reproducible from
the script alone the moment `WeaveConfig.segmentation_unit` became a real
switch (braidio#63): the same script now renders to different durations
under different pacing knobs, so a persisted breakdown whose settings are
unknown cannot be re-derived, and any downstream data keyed to its timings
(panel cues, captions) silently stops matching a later re-render. Recording
the settings next to the timings closes that hole on the no-graph fast path,
where the nw/lacing provenance layer is not in play at all.

It is always plain JSON types, so a consumer that already persists the
breakdown gets the record for free.

#### *property* duration *: [float](https://docs.python.org/3/builtins/functions.html#float)*

Total timeline length (s) — the max beat end, accounting for overlaps.

#### *classmethod* from_dict(d)

Rebuild a breakdown from `to_dict()` output (the persisted form).

`totals` and `duration` are derived, so they are recomputed rather
than read back — the beats are the record.

* **Return type:**
  [`TimelineBreakdown`](#braidio.timeline.TimelineBreakdown)

```pycon
>>> tl = build_timeline(kinds=["narration", "clip"], durations=[4.0, 2.0],
...                     labels=["a", "b"], source_spans=[None, (1.0, 3.0)])
>>> TimelineBreakdown.from_dict(tl.to_dict()) == tl
True
```

#### shares()

Fraction of spoken+clip time per `kind` (sums to 1).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`float`](https://docs.python.org/3/builtins/functions.html#float)]

#### to_html(title=None, subtitle='')

A self-contained HTML view: totals bar, walking-order timeline, table.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

#### totals()

Seconds spent per `kind` (insertion-ordered by first appearance).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`float`](https://docs.python.org/3/builtins/functions.html#float)]

### braidio.timeline.build_timeline(, kinds, durations, placements=None, labels=None, source_spans=None, clip_edge_overlap_s=0.5, narration_crossfade_s=0.12, title='', settings=None)

Assemble a [`TimelineBreakdown`](#braidio.timeline.TimelineBreakdown) from per-beat render data (pure).

`kinds` are the aggregation labels (any string; `"clip"` is the only one
the layout treats specially — as an overlapping segment). `placements` is
the per-beat `"sequential"`/`"under"` used by the weave; offsets are
computed with the same [`layout_placed()`](braidio.weave.md#braidio.weave.layout_placed) the renderer uses.

`settings` is the optional render record ([`render_settings()`](#braidio.timeline.render_settings)) — what
produced these durations. It stays optional so building a timeline by hand
(tests, a hand-cut episode, the captions/video doctests) needs nothing
extra; a real render always passes one.

* **Return type:**
  [`TimelineBreakdown`](#braidio.timeline.TimelineBreakdown)

### braidio.timeline.render_settings(, config, crossfade_s, clip_edge_overlap_s, target_lufs, duck_db, delivery=None, profile=None, normalize=True)

The record of *how* a production was rendered, as plain JSON types.

Why it exists: since braidio#63 the pacing knobs are real, so two renders of
one script can differ by minutes — and until this, nothing on disk said which
settings produced which file. This is the cheap, no-graph answer: the
renderer hands it to the [`TimelineBreakdown`](#braidio.timeline.TimelineBreakdown) it already returns, so a
consumer that persists the breakdown persists the settings with it.

The whole resolved [`WeaveConfig`](braidio.weave_config.md#braidio.weave_config.WeaveConfig) goes in under
`"weave"` rather than a hand-picked subset — adding a knob then keeps the
record correct for free, where a curated list would quietly drift.
`"resolved"` carries the values the render *actually used*, which are not
always the config’s: a caller may pass no config at all (`"weave"` is then
`None`, and the pacing knobs were never consulted — each beat was one TTS
call), the concat path resolves its crossfade from either the config or the
`crossfade_s` argument, and `normalize` is a render argument rather than
a weave choice.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

```pycon
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
```
