# braidio.tts

ElevenLabs narration synthesis.

Thin wrapper over `mixing.text_to_speech()` (the ElevenLabs entry point,
with on-disk caching) applying a voice preset: the `eleven_multilingual_v2`
quality model and a locked voice + settings so a whole production sounds like
one narrator.

Voice defaults to “George — Warm, Captivating Storyteller”; override with the
`BRAIDIO_TTS_VOICE` env var (`VOICE_ENV_VAR`) or the `voice_id` arg.

ElevenLabs is the default, not the only voice: a [`Speak`](#braidio.tts.Speak) callable
(`speak(text, *, voice) -> (audio_bytes, format)`) is the seam any other
backend plugs into — a local model through `voxy` for a free preview, a test,
CI. [`narrator_from_speak()`](#braidio.tts.narrator_from_speak) adapts one to [`narrate()`](#braidio.tts.narrate)’s shape, which is
what the renderers call.

### Module Attributes

| [`DEFAULT_MODEL_ID`](#braidio.tts.DEFAULT_MODEL_ID)   | The narration model.                                                                                                                                                                   |
|---------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`DEFAULT_TTS`](#braidio.tts.DEFAULT_TTS)        | How a render's settings record names the default voice backend (no `speak=`).                                                                                                          |
| [`Speak`](#braidio.tts.Speak)              | `speak(text, *, voice) -> (audio_bytes, format)`, `format` being the audio's file extension (`"wav"`, `"mp3"`).                                                                        |
| [`TTS_BACKENDS`](#braidio.tts.TTS_BACKENDS)       | The registered voice backends by name, read-only ([`register_tts()`](#braidio.tts.register_tts), [`unregister_tts()`](#braidio.tts.unregister_tts)). |

### Functions

| [`narrate`](#braidio.tts.narrate)(text, out_path, \*[, api_key, ...])       | Synthesize `text` to `out_path` (mp3).                                                                       |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------|
| [`narrator_from_speak`](#braidio.tts.narrator_from_speak)(speak, \*[, strip_tags])      | A [`narrate()`](#braidio.tts.narrate)-shaped synthesizer that speaks through `speak`. |
| [`register_tts`](#braidio.tts.register_tts)(name, speak, \*[, usd_per_1k_chars]) | Register `speak` as the voice backend `name` (replacing any before).                                         |
| [`resolve_voice_id`](#braidio.tts.resolve_voice_id)([voice_id])                      | Voice id from arg → `VOICE_ENV_VAR` env → default.                                                           |
| [`text_to_dialogue`](#braidio.tts.text_to_dialogue)(turns, \*[, model_id, ...])      | Synthesize a multi-speaker exchange in ONE pass (ElevenLabs Text-to-Dialogue).                               |
| [`tts_backend`](#braidio.tts.tts_backend)(name)                                 | The backend registered as `name`; `KeyError` naming the registered ones.                                     |
| [`tts_name`](#braidio.tts.tts_name)(speak)                                   | The voice backend a render used, as its settings record names it.                                            |
| [`unregister_tts`](#braidio.tts.unregister_tts)(name)                              | Remove the backend registered as `name`; returns it, or `None`.                                              |

### Classes

| [`TTSBackend`](#braidio.tts.TTSBackend)(speak[, usd_per_1k_chars])   | A named voice backend: its `speak` and what it costs.   |
|------------------------------------------------------------------------------------------|---------------------------------------------------------|

### braidio.tts.DEFAULT_MODEL_ID *= 'eleven_v3'*

The narration model. eleven_v3 rather than eleven_multilingual_v2 because
v2 cannot render inline [audio tags] AT ALL, and tag density is the main
lever on whether a read sounds alive (braidio.style.audio_tag_rate). A v2
default caps every production at the flat end before an author writes a
word. Override per-render with `delivery=`, or persist your own in
~/.config/braidio/config.json — see braidio.defaults.

### braidio.tts.DEFAULT_TTS *= 'elevenlabs'*

How a render’s settings record names the default voice backend (no `speak=`).

### braidio.tts.Speak

`speak(text, *, voice) -> (audio_bytes, format)`,
`format` being the audio’s file extension (`"wav"`, `"mp3"`). `voice`
is the voice braidio resolved for the line (`None`: the backend’s default).

* **Type:**
  A text-to-speech backend

alias of [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[…], [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

### *class* braidio.tts.TTSBackend(speak, usd_per_1k_chars=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A named voice backend: its `speak` and what it costs.

`usd_per_1k_chars` is the backend’s rate: `0.0` for a local model,
`None` (default) when unknown, which a cost report then reads as
*unpriced* rather than free (the same rule as [`braidio.cost`](braidio.cost.html.md#module-braidio.cost)).

#### cost_usd(text)

The spend for `text` at this backend’s rate (`None`: unpriced).

* **Return type:**
  [`float`](https://docs.python.org/3/builtins/functions.html#float) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### braidio.tts.TTS_BACKENDS *: [Mapping](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [TTSBackend](#braidio.tts.TTSBackend)]* *= mappingproxy({})*

The registered voice backends by name, read-only ([`register_tts()`](#braidio.tts.register_tts),
[`unregister_tts()`](#braidio.tts.unregister_tts)). A name, not a callable, is what a
[`WeaveConfig`](braidio.weave_config.html.md#braidio.weave_config.WeaveConfig) records (`tts=`), so the graph
can persist it, fold it into a cache key, and resolve it at render time.
Re-registering a name keeps the renders already cached under it: the key is
the name, so register a *different* name for a different voice.

### braidio.tts.narrate(text, out_path, , api_key=None, voice_id=None, model_id='eleven_v3', voice_settings=None, output_format='mp3_44100_128', refresh=False, return_cache_status=False)

Synthesize `text` to `out_path` (mp3). Returns the path.

Caching is handled by `mixing.text_to_speech` (keyed on text+voice+model);
pass `refresh=True` to regenerate.

`api_key` is an optional per-request ElevenLabs key: when given it wins
over the environment; when `None` (default) resolution falls back to
`$ELEVENLABS_API_KEY` (unchanged behavior). This is what lets a caller
thread a per-user BYO key without touching the process environment.

`return_cache_status`: when `True`, return `(path, was_cached)` where
`was_cached` is `True` iff mixing served the audio from its on-disk cache
(no ElevenLabs call = $0 real spend). Lets the caller attribute real cost
(braidio#8). Default `False` keeps the `Path` return.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path), [`bool`](https://docs.python.org/3/builtins/functions.html#bool)]

### braidio.tts.narrator_from_speak(speak, , strip_tags=True)

A [`narrate()`](#braidio.tts.narrate)-shaped synthesizer that speaks through `speak`.

`voice_id` is handed to `speak` as `voice`: braidio’s resolved id for
the line, which under the shipped formats and the default cast is an
ElevenLabs id (pass `voice_id=` / `cast=` in the backend’s vocabulary,
or map ids inside `speak`); `None` only when nothing named a voice.
Inline `[audio tags]` are eleven_v3 markup, so with `strip_tags` (the
default) they are removed before `speak` sees the text, rather than read
aloud. The ElevenLabs-only arguments (`api_key`, `model_id`,
`voice_settings`, so a paced turn’s `speed` too) are accepted and
ignored. Nothing is cached: every call reaches `speak`. The file is
written at `out_path` with the suffix `speak` reported.

```pycon
>>> import tempfile
>>> fake = lambda text, *, voice: (text.encode(), "wav")
>>> with tempfile.TemporaryDirectory() as d:
...     out = narrator_from_speak(fake)("[pause] hi", Path(d) / "a.mp3", voice_id="v")
...     out.suffix, out.read_bytes()
('.wav', b'hi')
```

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]

### braidio.tts.register_tts(name, speak, , usd_per_1k_chars=None)

Register `speak` as the voice backend `name` (replacing any before).

`name` is what `WeaveConfig(tts=name)` selects, on either render path.
[`DEFAULT_TTS`](#braidio.tts.DEFAULT_TTS) is ElevenLabs itself and cannot be taken.

```pycon
>>> b = register_tts("doc-tone", lambda text, *, voice: (b"", "wav"), usd_per_1k_chars=0.0)
>>> tts_backend("doc-tone") is b, b.cost_usd("hello")
(True, 0.0)
>>> unregister_tts("doc-tone") is b
True
```

* **Return type:**
  [`TTSBackend`](#braidio.tts.TTSBackend)

### braidio.tts.resolve_voice_id(voice_id=None)

Voice id from arg → `VOICE_ENV_VAR` env → default.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.tts.text_to_dialogue(turns, , model_id='eleven_v3', output_format='mp3_44100_128', settings=None, seed=None, api_key=None, cache=True, refresh=False, return_cache_status=False)

Synthesize a multi-speaker exchange in ONE pass (ElevenLabs Text-to-Dialogue).

Unlike per-line [`narrate()`](#braidio.tts.narrate), this renders the whole conversation together
so prosody is conditioned across turns — the key to sounding like people
*talking to each other* rather than alternating monologues (see braidio#1 /
`docs/research/conversational-vs-narration-tts.md`). `eleven_v3` only.

**Cached** (like [`narrate()`](#braidio.tts.narrate)/`mixing.text_to_speech`): the result is
keyed on the SHA-256 of every parameter that affects the audio (turns,
voices, model, settings, seed, format). A cache hit returns the *same* bytes
instantly — no API call, deterministic master, and the basis for partial
re-render (change one exchange → only its key changes). `cache=True`
(default) uses `DIALOGUE_CACHE_ENV_KEY` / the default cache dir;
`cache=False` disables it; a path uses that dir. `refresh=True` forces a
re-render (to re-roll a v3 take). Because v3 is nondeterministic, caching a
seedless call freezes one random take — pass a `seed` for reproducibility.

* **Parameters:**
  * **turns** – ordered `(voice_id, text)` pairs (or `{"voice_id", "text"}`).
    Keep each request under ~2000 chars total (API limit).
  * **settings** ([`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – optional model settings dict (e.g. `{"stability": 0.45}`).
  * **return_cache_status** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – when `True`, return `(audio, was_cached)` where
    `was_cached` is `True` iff the bytes came from the on-disk cache
    (no ElevenLabs call = $0 real spend) — the same attribution
    [`narrate()`](#braidio.tts.narrate) offers (braidio#8). Default `False` keeps the
    `bytes` return.
* **Return type:**
  [`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes) | [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`bytes`](https://docs.python.org/3/builtins/stdtypes.html#bytes), [`bool`](https://docs.python.org/3/builtins/functions.html#bool)]
* **Returns:**
  raw audio bytes in `output_format`.

### braidio.tts.tts_backend(name)

The backend registered as `name`; `KeyError` naming the registered ones.

* **Return type:**
  [`TTSBackend`](#braidio.tts.TTSBackend)

### braidio.tts.tts_name(speak)

The voice backend a render used, as its settings record names it.

`None` is the default backend; a callable is named `module:qualname`,
seeing through `functools.partial` (a `name` attribute wins).

```pycon
>>> import functools
>>> tts_name(None)
'elevenlabs'
>>> tts_name(functools.partial(narrator_from_speak, strip_tags=False))
'braidio.tts:narrator_from_speak'
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.tts.unregister_tts(name)

Remove the backend registered as `name`; returns it, or `None`.

* **Return type:**
  [`TTSBackend`](#braidio.tts.TTSBackend) | [`None`](https://docs.python.org/3/builtins/constants.html#None)
