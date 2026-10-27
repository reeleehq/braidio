# braidio.conversation

Conversational register: render an exchange as people *talking to each other*.

The second delivery register beside narration (braidio#1). A
[`ConversationCast`](#braidio.conversation.ConversationCast) maps role labels (`"A"`/`"B"`) to contrasting
**conversational** ElevenLabs voices. [`render_dialogue()`](#braidio.conversation.render_dialogue) synthesizes the
whole exchange in **one pass** via [`braidio.tts.text_to_dialogue()`](braidio.tts.html.md#braidio.tts.text_to_dialogue) (eleven_v3)
so prosody is conditioned across turns — the key to not sounding narrated.
[`render_turns_sequential()`](#braidio.conversation.render_turns_sequential) is the per-line baseline (each turn synthesized
alone, then concatenated) used for A/B comparison.

Casting note: use conversational-labelled voices, not narrator voices; loosen
settings so v3 audio tags fire. The scripted exchange itself must carry the
disfluency (backchannels, interruptions, fragments) — the model won’t invent it.

### Functions

| [`render_dialogue`](#braidio.conversation.render_dialogue)(turns[, cast, api_key, ...])   | One-pass render of `turns` (`[(role, text), …]`) via Text-to-Dialogue.                              |
|-------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| [`render_turns_sequential`](#braidio.conversation.render_turns_sequential)(turns[, cast, ...])    | Per-line baseline: synthesize each turn alone (its role's voice) and concatenate with `gap_s` gaps. |

### Classes

| [`ConversationCast`](#braidio.conversation.ConversationCast)([roles, model_id, settings])   | Role → voice mapping + model/settings for a conversational exchange.   |
|--------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|

### *class* braidio.conversation.ConversationCast(roles=<factory>, model_id='eleven_v3', settings=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Role → voice mapping + model/settings for a conversational exchange.

Default cast: **Jessica** (playful/bright, F) + **Chris** (charming/
down-to-earth, M) — snappier than the “relaxed” voices. `stability=0.45`
is the research sweet spot for a lively but coherent read (never 1.0/Robust,
which mutes v3 tags; ~0.1 is too unstable). See braidio#1 + the pacing doc.

### braidio.conversation.render_dialogue(turns, cast=ConversationCast(roles={'A': 'cgSgspJ2msm6clMCkdW9', 'B': 'iP95p4xoKVk53GoZ742B'}, model_id='eleven_v3', settings={'stability': 0.45}), , api_key=None, out_path, output_format='mp3_44100_128', seed=None, cache=True, refresh=False, tighten_gaps_s=0.6, return_cache_status=False)

One-pass render of `turns` (`[(role, text), …]`) via Text-to-Dialogue.

Cached by default (see [`braidio.tts.text_to_dialogue()`](braidio.tts.html.md#braidio.tts.text_to_dialogue)): an unchanged
exchange renders instantly on re-run. `refresh=True` re-rolls the take.

`api_key` is an optional per-request ElevenLabs key threaded to
[`braidio.tts.text_to_dialogue()`](braidio.tts.html.md#braidio.tts.text_to_dialogue); `None` (default) keeps the
`$ELEVENLABS_API_KEY` fallback.

`tighten_gaps_s` (>0) applies the “R-tight” pass: ffmpeg trims only the
over-long dead gaps (silences ≥ this many seconds) that make v3 dialogue
feel draggy, while leaving natural short pauses. Set 0 to keep the raw take.

`return_cache_status`: when `True`, return `(path, was_cached)` where
`was_cached` is `True` iff the take came from the on-disk cache (no
ElevenLabs call = $0 real spend) — what lets the graph path attribute real
cost (braidio#8). Default `False` keeps the `Path` return.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path), [`bool`](https://docs.python.org/3/builtins/functions.html#bool)]

### braidio.conversation.render_turns_sequential(turns, cast=ConversationCast(roles={'A': 'cgSgspJ2msm6clMCkdW9', 'B': 'iP95p4xoKVk53GoZ742B'}, model_id='eleven_v3', settings={'stability': 0.45}), , api_key=None, out_path, work_dir='data/tts/conversation', voice_settings=None, gap_s=0.25, narrate_fn=None)

Per-line baseline: synthesize each turn alone (its role’s voice) and
concatenate with `gap_s` gaps. Prosody is NOT shared across turns — this is
what tends to sound like alternating monologues (the thing to beat).

`api_key` is threaded to [`braidio.tts.narrate()`](braidio.tts.html.md#braidio.tts.narrate); `None` (default)
keeps the `$ELEVENLABS_API_KEY` fallback. `narrate_fn` is the per-turn
synthesizer (default [`braidio.tts.narrate()`](braidio.tts.html.md#braidio.tts.narrate)): a renderer given another
TTS backend ([`braidio.tts.narrator_from_speak()`](braidio.tts.html.md#braidio.tts.narrator_from_speak)) renders dialogue here,
since only ElevenLabs has the one-pass path.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
