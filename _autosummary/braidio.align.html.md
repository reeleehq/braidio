# braidio.align

Time an authored script on narration braidio did not render (braidio#212).

[`captions_for()`](braidio.captions.html.md#braidio.captions.captions_for) and [`plan_spans()`](braidio.video.html.md#braidio.video.plan_spans) both
need the [`TimelineBreakdown`](braidio.timeline.html.md#braidio.timeline.TimelineBreakdown) of braidio’s *own* render.
Narration made elsewhere has none: a long script voiced track by track through
another TTS, or a recording somebody made by hand. This module builds that
timeline from the audio’s **measured word times** (any word-level transcript of
it: ElevenLabs Scribe, Whisper, a forced aligner), while the words that appear in
a caption stay the **authored** ones. Only the *times* are measured; a recogniser
that heard “mate” for «Matte.» never reaches a subtitle.

The one entry point is [`align_script()`](#braidio.align.align_script):

```default
alignment = align_script(script_or_text, words)
alignment.timeline          # plugs into braidio.video.plan_spans
captions_from_cues(alignment.cues)   # the SRT
```

Free, offline, pure and dependency-free (stdlib `difflib`): part of the
functional core.

Seams (keyword-only, each defaulting to the strongest built-in):

- `normalize`: how a text is reduced to comparable tokens (default: lowercase,
  accents folded, letters and digits of any alphabet).
- `unit`: what a timeline beat is, one per `"paragraph"` or one per `"beat"`.
- `max_chars`: the longest caption *cue* before it is cut at whitespace (the
  SRT’s line width is [`captions_from_cues()`](braidio.captions.html.md#braidio.captions.captions_from_cues)’s own
  `max_chars`).

A dialogue beat is timed as its turns’ text, one unit per turn; the speaker labels
are not spoken, so they appear in no cue (unlike [`cues_for()`](braidio.captions.html.md#braidio.captions.cues_for)).

This is the in-package stand-in for a shared word aligner (authored tokens against
timed words), a kind of code several packages of the fleet carry; see braidio#212.

### Examples

```pycon
>>> words = [
...     {"text": "Hello", "start": 0.5, "end": 0.9},
...     {"text": "there.", "start": 0.9, "end": 1.4},
...     {"text": "Goodbye", "start": 3.0, "end": 3.4},
... ]
>>> a = align_script("Hello there.\n\nGoodbye.", words, duration_s=4.0)
>>> [(c.text, c.start_s, c.end_s) for c in a.cues]
[('Hello there.', 0.5, 1.4), ('Goodbye.', 3.0, 3.4)]
>>> [(b.start, b.duration) for b in a.timeline.beats]
[(0.0, 3.0), (3.0, 1.0)]
>>> a.unmatched, a.matched, a.total
((), 3, 3)
```

### Functions

| [`align_script`](#braidio.align.align_script)(script_or_text, words, \*[, ...])   | Align the authored `script_or_text` to the measured `words` of its audio.   |
|---------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| [`tokens`](#braidio.align.tokens)(text)                                     | Comparable tokens of `text`: lowercase, accents folded, letters and digits. |

### Classes

| [`Alignment`](#braidio.align.Alignment)(timeline, cues, unmatched, ...)   | What [`align_script()`](#braidio.align.align_script) measured.   |
|----------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|

### *class* braidio.align.Alignment(timeline, cues, unmatched, matched, total)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What [`align_script()`](#braidio.align.align_script) measured.

`timeline`: one sequential beat per unit, contiguous from 0 to the end of
the audio (a beat is its text plus the silence after it, the first also
holds the lead-in), so `plan_spans` and `captions` take it as they take a
render’s. `cues`: caption cues carrying the authored text at measured
times, never overlapping. `unmatched`: the chunks no heard word matched,
whose times were interpolated. `matched` / `total`: authored tokens a
heard word matched, over all of them: gate on `matched / total` before
trusting the times of audio you are unsure belongs to this text.

### braidio.align.align_script(script_or_text, words, \*, duration_s=None, unit='paragraph', max_chars=84, normalize=<function tokens>, title='')

Align the authored `script_or_text` to the measured `words` of its audio.

* **Parameters:**
  * **script_or_text** ([`Script`](braidio.script.html.md#braidio.script.Script) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – a [`Script`](braidio.script.html.md#braidio.script.Script) (its narration and
    dialogue beats; segment beats are other audio and are skipped) or
    plain text, paragraphs separated by a blank line. `[tags]` and
    `<break/>` markup is not spoken and is dropped.
  * **words** ([`Iterable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Iterable)[[`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]) – `{"text" (or "word"), "start", "end"}` mappings in playback
    order, times in seconds on the audio’s own clock. Entries whose
    `type` is `spacing` / `audio_event` are ignored. For audio
    made in several tracks, offset each track’s words and concatenate.
  * **duration_s** ([`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – length of the audio; the last beat runs to it. Default: the
    end of the last cue.
  * **unit** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – `"paragraph"` (a timeline beat per paragraph) or `"beat"` (one
    per script beat; for plain text, the whole text).
  * **max_chars** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – longest caption cue, cut at whitespace.
  * **normalize** ([`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]) – text to comparable tokens, see [`tokens()`](#braidio.align.tokens).
  * **title** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – the timeline’s title.
* **Return type:**
  [`Alignment`](#braidio.align.Alignment)
* **Returns:**
  An [`Alignment`](#braidio.align.Alignment).
* **Raises:**
  [**ValueError**](https://docs.python.org/3/builtins/exceptions.html#ValueError) – `unit` unknown; no text or no usable words; `words` out
      of order (an un-offset track) or running past `duration_s`; or no
      authored word matched a heard one (wrong audio, or `normalize`
      drops everything).

### braidio.align.tokens(text)

Comparable tokens of `text`: lowercase, accents folded, letters and digits.

```pycon
>>> tokens("Été, déjà-vu 2!")
['ete', 'deja', 'vu', '2']
>>> tokens("Привет, мир")
['привет', 'мир']
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]
