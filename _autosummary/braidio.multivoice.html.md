# braidio.multivoice

Multi-voice narration: cycle a pool of voices across segments (issue #10).

When one voice reads flat, variety across voices is a stronger lever than any
single-voice setting. This renders narration by splitting it into segments
(sentences) and assigning each a voice drawn from a pool — randomly (seeded, so
it’s reproducible) and avoiding immediate repeats so it doesn’t feel like a
rigid round-robin. Speed is jittered slightly per segment, even within one
speaker, to break regularity.

Two curated pools of **premade** ElevenLabs voices (all production quality,
deliberately less familiar than “George”, 2M/2F in `POOL_4`, wider in
`POOL_MANY`). For a truly large “many people / interviews” pool, the
shared Voice Library (thousands) can be tapped, but quality varies there, so
those need curation — the premade pools below are the safe default.

### Functions

| [`assign_voices`](#braidio.multivoice.assign_voices)(n, pool, \*[, seed, avoid_repeats])   | Assign a voice to each of `n` turns — random, seeded, no immediate repeats (so it isn't a rigid round-robin).   |
|------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------|
| [`group_turns`](#braidio.multivoice.group_turns)(segments, \*[, min_turn, ...])          | Group consecutive segments into *turns* of `min_turn..max_turn` segments.                                       |
| [`render_multivoice`](#braidio.multivoice.render_multivoice)(segments, pool, \*, out_path)     | Render `segments` cycling `pool` → `out_path`.                                                                  |
| [`split_segments`](#braidio.multivoice.split_segments)(text)                                | Split narration into sentence-level segments (markup removed).                                                  |
| `strip_markup`(text)                                                                                 |                                                                                                                 |

### Classes

| [`Voice`](#braidio.multivoice.Voice)(id, name, gender[, accent, note])   | A pooled narration voice.   |
|--------------------------------------------------------------------------------------------|-----------------------------|

### *class* braidio.multivoice.Voice(id, name, gender, accent='', note='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A pooled narration voice.

### braidio.multivoice.assign_voices(n, pool, , seed=0, avoid_repeats=True)

Assign a voice to each of `n` turns — random, seeded, no immediate
repeats (so it isn’t a rigid round-robin).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Voice`](#braidio.multivoice.Voice)]

### braidio.multivoice.group_turns(segments, , min_turn=1, max_turn=1, seed=0)

Group consecutive segments into *turns* of `min_turn..max_turn` segments.

A turn is what one voice speaks before the next takes over. Bigger turns =
each speaker talks longer (fewer switches). Each turn’s segments are joined
into one utterance so prosody is continuous within a speaker. Turn sizes are
seeded-random within the range.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### braidio.multivoice.render_multivoice(segments, pool, , out_path, api_key=None, work_dir='data/tts/multivoice', seed=7, min_turn=2, max_turn=4, avoid_immediate_repeat=True, model_id='eleven_multilingual_v2', base_settings=None, speed_base=1.0, speed_jitter=0.04, crossfade_s=0.1, gap_s=0.0, target_lufs=-16.0, narrate_fn=None)

Render `segments` cycling `pool` → `out_path`.

Segments are first grouped into *turns* of `min_turn..max_turn` segments
(bigger = each voice talks longer). One voice per turn, no immediate repeat,
with a jittered speed even within a speaker. `gap_s` inserts silence
between turns (0 = none). Returns `[(voice, turn_text), …]` for reporting.

`api_key` is an optional per-request ElevenLabs key threaded to every
[`braidio.tts.narrate()`](braidio.tts.html.md#braidio.tts.narrate) call; `None` (default) keeps the
`$ELEVENLABS_API_KEY` fallback. `narrate_fn` is the per-turn
synthesizer (default: the ElevenLabs voice, [`braidio.tts.voice_for()`](braidio.tts.html.md#braidio.tts.voice_for),
so replacing `braidio.narrate` reaches this path too, braidio#199).

#### NOTE
overlapping/interrupting speakers and clip ducking are separate,
upcoming parameters (tracked as issues) — this renders turns sequentially.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Voice`](#braidio.multivoice.Voice), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

### braidio.multivoice.split_segments(text)

Split narration into sentence-level segments (markup removed).

Splits on sentence-final `.?!` (not the `…` used for in-thought pacing),
so connected clauses stay with one speaker.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]
