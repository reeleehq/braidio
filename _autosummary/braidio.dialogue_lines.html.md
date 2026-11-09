# braidio.dialogue_lines

Addressable dialogue lines (braidio#145): one take, every turn re-voiceable.

[`render_dialogue_lines()`](#braidio.dialogue_lines.render_dialogue_lines) is [`braidio.conversation.render_dialogue()`](braidio.conversation.html.md#braidio.conversation.render_dialogue)’s
one Text-to-Dialogue pass, timed: it returns a [`DialogueTake`](#braidio.dialogue_lines.DialogueTake) whose
[`DialogueLine`](#braidio.dialogue_lines.DialogueLine)s say where each turn sits in the take.
[`rerender_line()`](#braidio.dialogue_lines.rerender_line) re-synthesizes ONE turn and splices it in, leaving every
other line’s audio (and its cross-turn prosody) untouched and unbilled.
[`line_cost()`](#braidio.dialogue_lines.line_cost) quotes a line before it is rendered.

Three choices keep a take editable many times over:

- **A lossless master.** Beside the deliverable (`take.path`, in the take’s
  `output_format`) sits a FLAC master (`take.master`). Splices operate on
  the master; the deliverable is re-encoded from it after each edit. So an
  unedited line is always ONE lossy generation from the master, however many
  edits came before, instead of one more generation per edit.
- **Content-checked takes.** A take records the master’s SHA-256. A take whose
  master has since changed (another edit wrote over it in place) is refused
  with [`StaleTakeError`](#braidio.dialogue_lines.StaleTakeError), before any spend, rather than cut at offsets
  that no longer describe the file.
- **Cuts in the pauses, with fades.** A re-rendered line replaces the take from
  the middle of the pause before it to the middle of the pause after it (the
  file’s edges for the first and last line), and brings the same amount of its
  own lead-in and decay along, so neither the old line’s tail nor the new
  line’s is lost. Each join gets mixing’s join fade, out and in.

### Module Attributes

| [`MASTER_SUFFIX`](#braidio.dialogue_lines.MASTER_SUFFIX)   | `take.mp3` -> `take.mp3.master.flac`.   |
|------------------------------------------------------------------|-----------------------------------------|

### Functions

| [`line_cost`](#braidio.dialogue_lines.line_cost)(take, index, \*[, text])                 | What re-rendering line `index` (as `text`, default its own) would cost.                                                        |
|-----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------|
| [`render_dialogue_lines`](#braidio.dialogue_lines.render_dialogue_lines)(turns[, cast, ...])          | [`render_dialogue()`](braidio.conversation.html.md#braidio.conversation.render_dialogue), as addressable lines. |
| [`rerender_line`](#braidio.dialogue_lines.rerender_line)(take, index, \*[, text, ...])        | Re-voice line `index` of `take` and splice it in; the others stay.                                                             |
| [`synthesize_line`](#braidio.dialogue_lines.synthesize_line)(voice_id, text, \*, model_id, ...) | The default [`LineSynthesizer`](#braidio.dialogue_lines.LineSynthesizer): a single-input timed Text-to-Dialogue call.      |

### Classes

| [`DialogueLine`](#braidio.dialogue_lines.DialogueLine)(index, role, text, offset_s, ...)   | One turn of a [`DialogueTake`](#braidio.dialogue_lines.DialogueTake): who says what, and where it sits.   |
|---------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|
| [`DialogueTake`](#braidio.dialogue_lines.DialogueTake)(path, lines, master, master_sha256) | A rendered exchange whose turns are addressable lines.                                                           |
| [`LineSynthesizer`](#braidio.dialogue_lines.LineSynthesizer)(\*args, \*\*kwargs)              | Voices ONE line: `(audio, segments, was_cached)`.                                                                |

### Exceptions

| [`DialogueTimingError`](#braidio.dialogue_lines.DialogueTimingError)   | A timed take came back without the timings a line needs.      |
|------------------------------------------------------------------------|---------------------------------------------------------------|
| [`StaleTakeError`](#braidio.dialogue_lines.StaleTakeError)        | A take's master no longer holds the audio the take describes. |

### *class* braidio.dialogue_lines.DialogueLine(index, role, text, offset_s, duration_s)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One turn of a [`DialogueTake`](#braidio.dialogue_lines.DialogueTake): who says what, and where it sits.

`offset_s`/`duration_s` are seconds into the take. They span the
turn’s voice segments (earliest start to latest end), so the pause
*between* two turns belongs to neither line.

#### *property* end_s *: [float](https://docs.python.org/3/builtins/functions.html#float)*

Where the line ends in the take.

### *class* braidio.dialogue_lines.DialogueTake(path, lines, master, master_sha256, cast=ConversationCast(roles={'A': 'cgSgspJ2msm6clMCkdW9', 'B': 'iP95p4xoKVk53GoZ742B'}, model_id='eleven_v3', settings={'stability': 0.45}), output_format='mp3_44100_128')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A rendered exchange whose turns are addressable lines.

`path` is the deliverable (in `output_format`); `master` the lossless
FLAC every splice operates on, and `master_sha256` its content hash, so
a take whose master has changed since is refused instead of mis-cut.
`lines` are the turns in order, timed (`turns` is their `(role, text)`
view); `cast` and `output_format` are what [`rerender_line()`](#braidio.dialogue_lines.rerender_line) needs to
re-voice one.

A take is checked when built: one line per turn, indexed `0..n-1` in
order, each with its turn’s role and text, every role in the cast, and the
lines in time order without overlap.

[`to_dict()`](#braidio.dialogue_lines.DialogueTake.to_dict)/[`from_dict()`](#braidio.dialogue_lines.DialogueTake.from_dict) round-trip it through JSON, so a host can
store a take and re-render a line of it in a later request. A stored dict
is data the host wrote: `from_dict` checks its consistency, NOT where
`path` and `master` point. A host that keeps takes for several users
must check both against its own root before trusting them, since
[`rerender_line()`](#braidio.dialogue_lines.rerender_line) writes to them.

#### *classmethod* from_dict(d)

The take [`to_dict()`](#braidio.dialogue_lines.DialogueTake.to_dict) recorded; raises `ValueError` if inconsistent.

* **Return type:**
  [`DialogueTake`](#braidio.dialogue_lines.DialogueTake)

#### to_dict()

A JSON-able record of the take (no secret ever lives on a take).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

#### *property* turns *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)], ...]*

what was sent to the dialogue endpoint.

* **Type:**
  `(role, text)` per line

### *exception* braidio.dialogue_lines.DialogueTimingError

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

A timed take came back without the timings a line needs.

### *class* braidio.dialogue_lines.LineSynthesizer(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Voices ONE line: `(audio, segments, was_cached)`.

`audio` is in `output_format`; `segments` must time input `0` (the
line) in it; `was_cached` says whether no paid call was made. A
synthesizer without native timings returns one segment spanning its
voiced audio.

### braidio.dialogue_lines.MASTER_SUFFIX *= '.master.flac'*

`take.mp3` ->
`take.mp3.master.flac`.

* **Type:**
  The lossless master’s name beside the deliverable

### *exception* braidio.dialogue_lines.StaleTakeError

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

A take’s master no longer holds the audio the take describes.

### braidio.dialogue_lines.line_cost(take, index, , text=None)

What re-rendering line `index` (as `text`, default its own) would cost.

Free and offline: a [`rerender_line()`](#braidio.dialogue_lines.rerender_line) submits that one line’s text and
nothing else, priced at the cast’s model like any other estimate
([`braidio.cost.tts_cost_usd()`](braidio.cost.html.md#braidio.cost.tts_cost_usd); `usd` is `None` when unpriced).

* **Return type:**
  [`CostRollup`](braidio.cost.html.md#braidio.cost.CostRollup)

### braidio.dialogue_lines.render_dialogue_lines(turns, cast=ConversationCast(roles={'A': 'cgSgspJ2msm6clMCkdW9', 'B': 'iP95p4xoKVk53GoZ742B'}, model_id='eleven_v3', settings={'stability': 0.45}), , out_path, api_key=None, output_format='mp3_44100_128', seed=None, cache=True, refresh=False, return_cache_status=False)

[`render_dialogue()`](braidio.conversation.html.md#braidio.conversation.render_dialogue), as addressable lines.

ONE Text-to-Dialogue pass ([`braidio.tts.text_to_dialogue_with_timestamps()`](braidio.tts.html.md#braidio.tts.text_to_dialogue_with_timestamps)),
so the cross-turn prosody of `render_dialogue` is kept, and each turn’s
place in the take comes from the API’s own voice segments (turns timed as
overlapping are split at the overlap’s midpoint; see [`DialogueLine`](#braidio.dialogue_lines.DialogueLine)).
Returns a [`DialogueTake`](#braidio.dialogue_lines.DialogueTake); [`rerender_line()`](#braidio.dialogue_lines.rerender_line) re-voices one of its
lines.

`out_path` gets the API’s audio as is; beside it, the lossless master
(`MASTER_SUFFIX`) later edits splice. Unlike `render_dialogue` there is
**no R-tight pass**: trimming the dead gaps would move every line after the
first trim away from the timings the API reported.

Billing, caching and the key are `render_dialogue`’s: the whole
exchange’s characters, once; a cache hit (audio *and* timings, stored
together) costs $0 and makes no API call, and `return_cache_status=True`
returns `(take, was_cached)` to say which. `api_key` reaches the
ElevenLabs client only — not the cache key, not the take. An empty turn,
an unknown role or an unspliceable `output_format` is refused before any
spend; a response lacking some turn’s timings raises
[`DialogueTimingError`](#braidio.dialogue_lines.DialogueTimingError) naming them (re-roll with `refresh=True`).

* **Return type:**
  [`DialogueTake`](#braidio.dialogue_lines.DialogueTake) | [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`DialogueTake`](#braidio.dialogue_lines.DialogueTake), [`bool`](https://docs.python.org/3/builtins/functions.html#bool)]

### braidio.dialogue_lines.rerender_line(take, index, \*, text=None, api_key=None, out_path=None, seed=None, cache=True, refresh=False, return_cache_status=False, synthesize=<function synthesize_line>)

Re-voice line `index` of `take` and splice it in; the others stay.

The line (`text`, default its current text — put eleven_v3 `[audio
tags]` in it to change the delivery) is voiced ALONE by `synthesize`
(default [`synthesize_line()`](#braidio.dialogue_lines.synthesize_line): a single-input timed Text-to-Dialogue
call with its role’s voice and the cast’s model and settings). Only its
characters are billed ([`line_cost()`](#braidio.dialogue_lines.line_cost) quotes them first).

The take is cut from the middle of the pause before the line to the
middle of the pause after it (the file’s edges for the first and last
line), and the new voice goes in with as much of its own lead-in and
decay as the cut removed, so the pauses keep their length and every later
line moves by the change in the line’s duration. Joins are faded
(mixing’s join fade). The splice runs on the lossless master; the
deliverable is then encoded once from it in the take’s `output_format`.

Refused before any spend: empty text, a role the cast lacks, an
unspliceable format, and a stale take ([`StaleTakeError`](#braidio.dialogue_lines.StaleTakeError): its master
has changed since, typically because an in-place edit returned a newer
take — use that one). A response without timings for the line raises
[`DialogueTimingError`](#braidio.dialogue_lines.DialogueTimingError); it is cached, so re-roll with
`refresh=True`.

Cached like any dialogue call: re-rendering the same text with the same
`seed` is a $0 cache hit, so pass `refresh=True` (or a new `seed`)
to re-roll a line. `return_cache_status=True` returns
`(take, was_cached)`.

Written to `out_path` (default: over `take.path`, in place) with its
master beside it. Returns the new [`DialogueTake`](#braidio.dialogue_lines.DialogueTake); `take` itself
is a value and is not changed, but after an in-place edit it is stale.

* **Return type:**
  [`DialogueTake`](#braidio.dialogue_lines.DialogueTake) | [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`DialogueTake`](#braidio.dialogue_lines.DialogueTake), [`bool`](https://docs.python.org/3/builtins/functions.html#bool)]

### braidio.dialogue_lines.synthesize_line(voice_id, text, , model_id, settings, seed, output_format, api_key, cache, refresh)

The default [`LineSynthesizer`](#braidio.dialogue_lines.LineSynthesizer): a single-input timed Text-to-Dialogue call.

Same voice, model and settings as the take, so the line matches it; billed
for this line’s characters only, and cached like any dialogue call.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes), [`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[[`DialogueSegment`](braidio.tts.html.md#braidio.tts.DialogueSegment)], [`bool`](https://docs.python.org/3/builtins/functions.html#bool)]
