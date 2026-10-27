# braidio.render

Render a [`Script`](braidio.script.html.md#braidio.script.Script) into an audio file.

Walks the beats (filtered by the rights [`Profile`](braidio.rights.html.md#braidio.rights.Profile)):
narration beats are synthesized ([`braidio.tts`](braidio.tts.html.md#module-braidio.tts)) — as one call, or as paced
turns when the [`WeaveConfig`](braidio.weave_config.html.md#braidio.weave_config.WeaveConfig) asks for a smaller
`segmentation_unit` ([`braidio.pacing`](braidio.pacing.html.md#module-braidio.pacing)); segment beats are resolved
via a [`SegmentSource`](braidio.sources.html.md#braidio.sources.SegmentSource) and extracted (padded + faded);
scene breaks become a sting or a pause ([`braidio.structure`](braidio.structure.html.md#module-braidio.structure)). Every spoken
or clipped part is loudness-normalized, then either woven on a timeline (clips
tuck under narration — [`braidio.weave.weave_timeline()`](braidio.weave.html.md#braidio.weave.weave_timeline)) when a
[`WeaveConfig`](braidio.weave_config.html.md#braidio.weave_config.WeaveConfig) enables it, or concatenated.

This is the no-graph fast path; the same core is reused by the nw-app
transforms (which add provenance + partial re-render).

### Functions

| [`narrate_turns`](#braidio.render.narrate_turns)(turns, \*, stem, api_key, ...)   | Synthesize a paced narration beat: one TTS call per turn, real silence between.   |
|-------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------|
| [`plan_beat_turns`](#braidio.render.plan_beat_turns)(text, \*, config, ...)         | Plan one narration beat's turns, or `[]` for the unpaced single call.             |
| [`render_production`](#braidio.render.render_production)(script, \*, source[, ...])   | Render `script` under `profile` → a single audio file.                            |

### braidio.render.narrate_turns(turns, , stem, api_key, voice_id, model_id, voice_settings, narrate_fn=None)

Synthesize a paced narration beat: one TTS call per turn, real silence between.

This is the point of [`braidio.pacing`](braidio.pacing.html.md#module-braidio.pacing) — within one beat the tempo now
varies (a fresh prosodic arc per turn, a per-turn speed where the model has
one) and the silence between turns is proportional to how strongly the
boundary closes, instead of one flat arc with nothing inside it.

`narrate_fn` is the synthesis call (default: this module’s `narrate`);
a caller that wants the cache status, or a stub, passes its own.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### braidio.render.plan_beat_turns(text, , config, supports_speed, settings, seed_offset)

Plan one narration beat’s turns, or `[]` for the unpaced single call.

The speed each turn is jittered around is the beat’s own `speed` setting
when it sets one (so a grave `v2-narrator`’s 0.94 survives being paced),
falling back to `WeaveConfig.speed_base`. A model with no speed control
(eleven v3 — [`braidio.delivery.Delivery.supports_speed`](braidio.delivery.html.md#braidio.delivery.Delivery.supports_speed),
[`braidio.delivery.model_supports_speed()`](braidio.delivery.html.md#braidio.delivery.model_supports_speed)) plans none.

Shared by the fast path and the graph’s `narration_render.tts`, which is
what keeps their per-beat durations (and so their timelines) equal.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`NarrationTurn`](braidio.pacing.html.md#braidio.pacing.NarrationTurn)]

### braidio.render.render_production(script, , source, api_key=None, speak=None, config=None, profile=Profile.PERSONAL, rights=None, delivery=None, cast=ConversationCast(roles={'A': 'cgSgspJ2msm6clMCkdW9', 'B': 'iP95p4xoKVk53GoZ742B'}, model_id='eleven_v3', settings={'stability': 0.45}), out_path=None, voice_id=None, crossfade_s=0.12, normalize=True, music_bed=None, structure=None, end_fade_s=0.35, end_silence_s=0.7, return_timeline=False, tts_dir='data/tts', clips_dir='data/clips', episodes_dir='data/episodes')

Render `script` under `profile` → a single audio file. Returns the path.

With `return_timeline=True` returns `(path, TimelineBreakdown)` instead —
the render records what it spent time on (per-beat kind, source interval,
duration, and offset) rather than leaving it to be reconstructed afterward,
plus, in `TimelineBreakdown.settings`, the settings that produced those
timings (see [`braidio.timeline.render_settings()`](braidio.timeline.html.md#braidio.timeline.render_settings)). Recording them is
what keeps a render reproducible: the pacing knobs below mean one script has
many possible cuts, so a persisted breakdown has to say which one it is.

Segment beats are resolved through `source` (a `SegmentSource`).
When `config` has `clip_edge_overlap_s > 0`, a clip is `placement="under"`,
or a `music_bed` is given, the parts are woven on a timeline; otherwise they
are concatenated. `rights` (if given) sets which segment rights are
publishable. `music_bed` lays an instrumental underscore under the whole
production (see [`braidio.music.MusicBed`](braidio.music.html.md#braidio.music.MusicBed)).

`structure` (a [`braidio.structure.MusicStructure`](braidio.structure.html.md#braidio.structure.MusicStructure)) is how the
production marks its structure with music: a scene-break beat plays its
`sting` (or a pause when there is none / the break is marked `"none"`),
and a spotlit segment beat drops the bed out for its duration. `None`
uses the inert defaults — no sting asset, no clip spotlit by default — so a
script without scene breaks or spotlight flags renders exactly as before.

**Pacing inside a narration beat** is a `config` choice. By default
(`WeaveConfig.segmentation_unit == "beat"`) a beat is one TTS call — one
prosodic arc, no silence anywhere in it. Set a smaller
`segmentation_unit` and the beat is cut into turns of
`min_turn..max_turn` units, each synthesized separately with a jittered
`speed_base ± speed_jitter` (where the model has a speed knob) and
followed by `gap_turn_s` scaled to how strongly the boundary closes —
see [`braidio.pacing`](braidio.pacing.html.md#module-braidio.pacing). Those four knobs do **nothing** here at the
default unit, which is the historical behavior kept byte-identical.

`api_key` is an optional per-request ElevenLabs key threaded to every
synthesized beat — both narration ([`braidio.tts.narrate()`](braidio.tts.html.md#braidio.tts.narrate)) and dialogue
([`braidio.conversation.render_dialogue()`](braidio.conversation.html.md#braidio.conversation.render_dialogue)). When `None` (default) each
synthesizer falls back to `$ELEVENLABS_API_KEY` (unchanged behavior); an
explicit key lets a caller (e.g. a per-user BYO-key request) override the
environment without mutating it. Segment beats never call ElevenLabs, so the
key does not touch them.

`speak` swaps the voice backend (a [`braidio.tts.Speak`](braidio.tts.html.md#braidio.tts.Speak), e.g. a local
model through `voxy`): every narration beat and turn is spoken through it,
and a dialogue beat is spoken turn by turn (only ElevenLabs has the one-pass
Text-to-Dialogue path). Formats, pacing and weaving are unchanged. It is
handed the voice ids this render resolves (the format’s, the cast’s), so
pass `voice_id=` / `cast=` in its vocabulary, or map them inside it.
`None` (default) is the ElevenLabs path, exactly as before.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path), [`object`](https://docs.python.org/3/builtins/functions.html#object)]
