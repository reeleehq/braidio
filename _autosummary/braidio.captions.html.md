# braidio.captions

Subtitles for a rendered production, built from the script it was rendered from.

A braidio render is one of the rare cases where the caption track needs no speech
recognition: the narration text is *authored*, and
[`render_production()`](braidio.render.html.md#braidio.render.render_production) will hand back a
[`TimelineBreakdown`](braidio.timeline.html.md#braidio.timeline.TimelineBreakdown) saying exactly when each beat plays.
Transcribing the mix back into text would pay an ASR pass to recover something
that was never lost – and do it worse (a measured pass on braidio’s own output
turned “purse sets up hurts” into “Perse sets up herts”).

So [`captions_for()`](#braidio.captions.captions_for) joins the two: beat text from the `Script`, beat timing
from the `TimelineBreakdown`. Narration and dialogue beats are split into
sentence-sized cues timed *within* their beat in proportion to length; a
`SegmentBeat` becomes one cue carrying its reference, marked as lyric/quote.

Pure and dependency-free – part of the functional core, importable with nothing
installed beyond braidio itself.

### Examples

```pycon
>>> from braidio.script import Narration, Script
>>> from braidio.timeline import build_timeline
>>> script = Script(title="t", id_slug="01", beats=[Narration("One. Two.")])
>>> tl = build_timeline(kinds=["narration"], durations=[4.0])
>>> print(captions_for(script, tl))
1
00:00:00,000 --> 00:00:02,000
One.

2
00:00:02,000 --> 00:00:04,000
Two.
```

### Functions

| [`captions_for`](#braidio.captions.captions_for)(script, timeline, \*[, max_chars])   | The SRT document for `script` as laid out by `timeline`.            |
|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|
| [`captions_from_cues`](#braidio.captions.captions_from_cues)(cues, \*[, max_chars, ...])    | An SRT document for cues timed from *measured* words (braidio#212). |
| [`cues_for`](#braidio.captions.cues_for)(script, timeline, \*[, max_chars])       | Subtitle cues for `script` as laid out by `timeline`.               |
| [`format_srt`](#braidio.captions.format_srt)(cues)                                  | Render `cues` as an SRT document.                                   |

### Classes

| [`Cue`](#braidio.captions.Cue)(start_s, end_s, text)   | One subtitle: `[start_s, end_s)` and the text shown.   |
|------------------------------------------------------------------------------|--------------------------------------------------------|

### *class* braidio.captions.Cue(start_s, end_s, text)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One subtitle: `[start_s, end_s)` and the text shown.

### braidio.captions.captions_for(script, timeline, , max_chars=0)

The SRT document for `script` as laid out by `timeline`.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.captions.captions_from_cues(cues, , max_chars=42, hold_s=0.6, gap_s=0.05)

An SRT document for cues timed from *measured* words (braidio#212).

Each cue’s text is wrapped to *lines* of at most `max_chars` (not to be
confused with [`align_script()`](braidio.align.html.md#braidio.align.align_script)’s `max_chars`, the longest
*cue*) and held
`hold_s` past its last word, never into the next cue (`gap_s` before it).
Cues timed by [`align_script()`](braidio.align.html.md#braidio.align.align_script) are speech-tight, so
unlike [`captions_for()`](#braidio.captions.captions_for) they get this hold.

```pycon
>>> print(captions_from_cues([Cue(0.0, 1.0, "Hi there."), Cue(1.2, 2.0, "Bye.")]))
1
00:00:00,000 --> 00:00:01,150
Hi there.

2
00:00:01,200 --> 00:00:02,600
Bye.
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.captions.cues_for(script, timeline, , max_chars=0)

Subtitle cues for `script` as laid out by `timeline`.

Beats are matched by index, so a timeline built from the same script lines up
even when some beats were dropped by a rights profile (a dropped beat simply
has no span and contributes no cue).

Cues never overlap. That matters because the weave *crossfades* consecutive
beats, so a beat’s `start` sits slightly before the previous beat’s end;
left alone that produces subtitles that fight each other. Each cue is clamped
to begin where the previous one finished.

* **Parameters:**
  * **script** – the [`Script`](braidio.script.html.md#braidio.script.Script) that was rendered.
  * **timeline** – the [`TimelineBreakdown`](braidio.timeline.html.md#braidio.timeline.TimelineBreakdown) from
    `render_production(..., return_timeline=True)`.
  * **max_chars** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – if > 0, split sentences longer than this at whitespace, so no
    single cue overflows a player’s two lines. 0 leaves sentences whole.
* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Cue`](#braidio.captions.Cue)]
* **Returns:**
  Cues in playback order.

### braidio.captions.format_srt(cues)

Render `cues` as an SRT document.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
