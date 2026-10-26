# braidio.weave

Weave narration + audio clips on a timeline (#21) — the reusable mix engine.

Two generic, Hamilton-agnostic primitives:

- [`extract_padded()`](#braidio.weave.extract_padded) — extract `[start, end]` from a source asset but
  **padded** by pre/post-roll (so the words are captured cleanly) with in/out
  fades on the padded edges. The padded, faded edges are what we tuck under the
  neighbouring narration.
- [`weave_timeline()`](#braidio.weave.weave_timeline) — place ordered narration/clip parts on a timeline
  where each clip **overlaps its neighbours** by `clip_edge_overlap_s` and its
  faded padded edges duck under the speech, so **speech stays dominant**, then
  mix (`amix` sum) and loudness-normalize.

This is the audio counterpart of a video timeline; it consumes plain file paths

+ numbers, no Genius/lyrics knowledge (that resolution lives in
  `graph/align.py` and the Hamilton adapters). Ducking here is achieved by
  fade-shaped overlap; a dynamic sidechain duck (`duck_db`) is a documented
  refinement (see #21 / the research).

### Module Attributes

| [`MAX_OVERLAP_TURN_S`](#braidio.weave.MAX_OVERLAP_TURN_S)   | past it two voices talking over each other stop being understood (Hamilton#22).   |
|-----------------------------------------------------------------------|-----------------------------------------------------------------------------------|

### Functions

| [`duck_regions`](#braidio.weave.duck_regions)(starts, durs, kinds, ...)          | Per part, the `(from_s, to_s)` span *within its own audio* to duck.    |
|--------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|
| `duration_s`(path)                                                                               |                                                                        |
| [`extract_padded`](#braidio.weave.extract_padded)(asset_path, start_s, end_s, ...) | Extract `[start_s-pre_roll, end_s+post_roll]` with in/out fades.       |
| [`interrupting_boundaries`](#braidio.weave.interrupting_boundaries)(kinds, placements, ...) | The `(prev, cur)` sequential-part pairs where `cur` interrupts `prev`. |
| [`layout_placed`](#braidio.weave.layout_placed)(kinds, durs, placements, \*, ...) | Start offset (s) of each part, placement-aware.                        |
| [`layout_starts`](#braidio.weave.layout_starts)(kinds, durs, \*, ...)             | Start offset (s) of each part (all sequential).                        |
| [`weave_timeline`](#braidio.weave.weave_timeline)(items, out_path, \*[, ...])      | Place items on a timeline and mix.                                     |

### Classes

| [`TimelineItem`](#braidio.weave.TimelineItem)(kind, path[, placement, ...])   | One part on the weave timeline.                                       |
|-----------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| [`TurnOverlap`](#braidio.weave.TurnOverlap)([max_s, jitter, duck_db, seed])  | How much the next speaker may start before the previous one finishes. |

### braidio.weave.MAX_OVERLAP_TURN_S *= 0.4*

past it two
voices talking over each other stop being understood (Hamilton#22).

* **Type:**
  Upper bound of the intelligible range for a speaker interruption

### *class* braidio.weave.TimelineItem(kind, path, placement='sequential', duck_db=0.0, spotlight=False, speaker=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One part on the weave timeline.

`placement` `"sequential"` (default — narration, and clean `before` /
`after` clips) lays the part in its own slot. `"under"` overlays the part
beneath the *following* sequential part (it does not consume its own slot),
attenuated by `duck_db` — a ducked underlay (a clip talked over, or later a
music bed).

`spotlight` marks the part the music bed drops out for (fade-to-spotlight):
the bed is silent over this part’s span and resumes after it. Inert without
a bed.

### *class* braidio.weave.TurnOverlap(max_s=0.0, jitter=0.3, duck_db=0.0, seed=0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

How much the next speaker may start before the previous one finishes.

The pure, render-time view of [`WeaveConfig`](braidio.weave_config.html.md#braidio.weave_config.WeaveConfig)’s
`overlap_*` knobs, so the layout and the mixer take one value instead of
four loose floats. `max_s == 0` means no interruption (the historical
butt-join with a crossfade). It lives here, not in `weave_config`, so the
weave stays free of the TTS/mixing imports that module carries.

`jitter` shortens each overlap by a seeded random fraction up to that
amount (so it is not mechanical); `duck_db` (≤ 0) lowers the outgoing voice
over the overlap; `seed` makes both reproducible — `WeaveConfig` passes
its `voice_seed`, so one seed fixes the casting and the interruptions.

The overlap is measured between *file* boundaries: the layout sees each
part’s duration only, so a beat that opens with its own lead silence
(`Narration.lead_gap_s`) spends the overlap on that silence first — an
incoming beat with a lead gap ≥ the overlap is not heard to interrupt.

```pycon
>>> TurnOverlap(max_s=0.5)
Traceback (most recent call last):
    ...
ValueError: TurnOverlap.max_s (WeaveConfig.overlap_turn_s) must be in [0, 0.4], got 0.5
```

#### *property* is_on *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

Whether a change of speaker is laid out as an interruption.

### braidio.weave.duck_regions(starts, durs, kinds, placements, speakers, overlap)

Per part, the `(from_s, to_s)` span *within its own audio* to duck.

The tail of an outgoing voice that the next speaker talks over, at each of
[`interrupting_boundaries()`](#braidio.weave.interrupting_boundaries); empty when `overlap.duck_db` is 0. Pure,
from the laid-out starts.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`int`](https://docs.python.org/3/builtins/functions.html#int), [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`float`](https://docs.python.org/3/builtins/functions.html#float), [`float`](https://docs.python.org/3/builtins/functions.html#float)]]

### braidio.weave.extract_padded(asset_path, start_s, end_s, out_path, , pre_roll_s=0.4, post_roll_s=0.3, fade_in_s=0.5, fade_out_s=0.8, min_len_s=2.2)

Extract `[start_s-pre_roll, end_s+post_roll]` with in/out fades.

The target words sit in the middle; the padded, faded head/tail are the
parts that overlap (tuck under) neighbouring narration in the weave.

Two guards keep **short** clips from sounding like they just swell in and
out (no steady body):

- the tail is extended so the clip is at least `min_len_s` long, giving the
  fade somewhere to breathe;
- the fades are **adaptive** — capped to a fraction of the clip so they never
  swallow it (a 1.5 s clip gets ~0.3 s fades, not 0.5 s + 0.8 s).

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### braidio.weave.interrupting_boundaries(kinds, placements, speakers, overlap)

The `(prev, cur)` sequential-part pairs where `cur` interrupts `prev`.

The one definition [`layout_placed()`](#braidio.weave.layout_placed) (where to start) and
[`duck_regions()`](#braidio.weave.duck_regions) (what to duck) share: overlap is on, both parts are
narration (a clip or sting never interrupts nor is interrupted), both have a
speaker, and the speakers differ.

```pycon
>>> interrupting_boundaries(
...     ["narration", "narration", "clip", "narration", "narration"],
...     ["sequential"] * 5, ["a", "b", None, "b", "c"], TurnOverlap(0.3))
[(0, 1), (3, 4)]
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`int`](https://docs.python.org/3/builtins/functions.html#int), [`int`](https://docs.python.org/3/builtins/functions.html#int)]]

### braidio.weave.layout_placed(kinds, durs, placements, , clip_edge_overlap_s, narration_crossfade_s, speakers=None, overlap=None)

Start offset (s) of each part, placement-aware. Pure function.

Sequential parts advance a running cursor: a clip (or a part following a clip)
starts `clip_edge_overlap_s` before the cursor so its faded edges tuck under
the neighbour; narration-after-narration overlaps by `narration_crossfade_s`.
An `"under"` part starts *at* the cursor (concurrent with the next
sequential part) and does **not** advance it — so it overlays what follows.
Clamped ≥ 0.

With `speakers` (one id per part, `None` = no speaker) and an `overlap`
that is on, each boundary [`interrupting_boundaries()`](#braidio.weave.interrupting_boundaries) names starts up to
`max_s` early instead (an interruption), capped by both turns’ lengths and
shortened by a jitter drawn from `Random(f"{seed}:{i}")` — per part index,
so editing one boundary does not reshuffle the others. Overlap is measured
between file boundaries, lead silence included (see [`TurnOverlap`](#braidio.weave.TurnOverlap)).

While overlap is on, two floors keep it to two voices at once: a part laid
after a non-clip part never starts before the end of any earlier sequential
part but the last, and a narration part never starts before the end of any
earlier narration but the most recent. Neither applies with overlap off, so
the default layout is unchanged.

```pycon
>>> layout_placed(["narration"] * 3, [2.0, 0.3, 2.0], ["sequential"] * 3,
...     clip_edge_overlap_s=0.5, narration_crossfade_s=0.12,
...     speakers=["a", "b", "b"], overlap=TurnOverlap(0.4, 0.0))
[0.0, 1.7, 2.0]
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]

### braidio.weave.layout_starts(kinds, durs, , clip_edge_overlap_s, narration_crossfade_s)

Start offset (s) of each part (all sequential). Thin wrapper over
[`layout_placed()`](#braidio.weave.layout_placed) — kept for callers that don’t use placement.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]

### braidio.weave.weave_timeline(items, out_path, , clip_edge_overlap_s=0.5, narration_crossfade_s=0.12, target_lufs=-16.0, true_peak=-1.0, sample_rate=44100, bed=None, overlap=None)

Place items on a timeline and mix. Clips overlap neighbours by
`clip_edge_overlap_s` (their faded edges tuck under narration); narration
parts butt-join with a small crossfade. Returns `out_path`.

`bed` (a [`MusicBed`](braidio.music.html.md#braidio.music.MusicBed)) lays an instrumental underscore
under the whole production: it’s rendered to cover the timeline, attenuated,
and mixed in posted by `bed.lead_in_s`. Items marked `spotlight` open a
gap in it (the bed is rendered as the regions around them — see
[`braidio.music.bed_regions()`](braidio.music.html.md#braidio.music.bed_regions)); with none marked the bed is the single
whole-span file. Falls back to a plain concat feel when
`clip_edge_overlap_s == 0` and there’s nothing to overlay.

`overlap` (a [`TurnOverlap`](#braidio.weave.TurnOverlap), e.g. `WeaveConfig.turn_overlap()`)
lays a change of speaker as an interruption and, with `duck_db`, lowers
the outgoing voice’s talked-over tail (see [`layout_placed()`](#braidio.weave.layout_placed)).

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
