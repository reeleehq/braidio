# braidio.formats

Ready-made **format templates** — high-quality presets under standard names.

braidio parametrizes *any* commentary style (a [`Script`](braidio.script.html.md#braidio.script.Script) of
`Narration` / `Dialogue` / `SegmentBeat` beats, cast via
[`ConversationCast`](braidio.conversation.html.md#braidio.conversation.ConversationCast), tuned by
[`WeaveConfig`](braidio.weave_config.html.md#braidio.weave_config.WeaveConfig) and [`Delivery`](braidio.delivery.html.md#braidio.delivery.Delivery)).
This module ships the *ready-made* end of that: a small set of named
[`Format`](#braidio.formats.Format) presets that bundle good defaults for the recurring, industry-named
ways people comment on an artifact — so `render_format(DEEP_DIVE, script, …)`
just works, and advanced users still compose the primitives directly.

The taxonomy + recipes are in
`misc/docs/research/commentary-formats-and-styles.md`. The organizing rule:
\*\*the talk is the spine; narration bridges and source clips are optional
“illustration” layers\*\* attached to it.

What a [`Format`](#braidio.formats.Format) drives at render time: the dialogue **cast**
(role→voice), the **narration voice + delivery**, the \*\*clip weave/duck +
loudness\*\* (`WeaveConfig`), and the **structural music**
([`MusicStructure`](braidio.structure.html.md#braidio.structure.MusicStructure)) — whether a
`SceneBreak` beat plays a sting, and whether the bed drops out under exhibits
(fade-to-spotlight). Per-clip placement renders too — set it on each
`SegmentBeat(placement=…)`; `Format.clip_placement` is the recommended
*default* for the format. Music is app-supplied: a **music bed** renders when you
pass a `bed_asset` to [`render_format()`](#braidio.formats.render_format) (the format’s `music_bed`
*intensity* picks the gain) and scene **stings** play when you pass a
`sting_asset` (without one a scene break is a beat of silence). Fields tagged
 *(authoring)* — `roles`, `scripting` — are conventions for whoever writes
(or generates) the `Script`. Preset ids mirror the standard names so a UI can
label them (“Deep Dive”, “Song Exploder-style”). The structural-music design
history is braidio#25.

### Functions

| [`describe_asset_application`](#braidio.formats.describe_asset_application)(fmt, script, \*[, ...])   | Which of the supplied `bed_asset` / `sting_asset` this format will actually render, and why not otherwise (braidio#43).           |
|-------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| [`render_format`](#braidio.formats.render_format)(fmt, script, \*, source[, ...])        | Render `script` under `fmt`'s defaults; `overrides` win over them.                                                                |
| [`sting_would_play`](#braidio.formats.sting_would_play)(structure, script, sting_asset)     | Whether some `SceneBreak` in `script` resolves to marker `"sting"` under `structure` once `sting_asset` is wired in as the sting. |

### Classes

| [`Format`](#braidio.formats.Format)(id, name, summary[, aka, cast, ...])   | A named commentary-format preset: a bundle of high-quality defaults.   |
|------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|

### *class* braidio.formats.Format(id, name, summary, aka=(), cast=None, narration_voice=None, narration_delivery=Delivery(name='v2-presenter', model_id='eleven_multilingual_v2', voice_settings={'stability': 0.35, 'similarity_boost': 0.75, 'style': 0.35, 'use_speaker_boost': True, 'speed': 0.98}, supports_audio_tags=False, supports_speed=True, note='Host/presenter commentary — lively (== v2-tuned), for the spine voice.'), weave=WeaveConfig(), structure=MusicStructure(), roles=dict(), clip_placement='before', music_bed='light', scripting='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A named commentary-format preset: a bundle of high-quality defaults.

Rendered fields drive [`render_format()`](#braidio.formats.render_format) → [`braidio.render.render_production()`](braidio.render.html.md#braidio.render.render_production).
Authoring fields document how to write a `Script` for this format;
`clip_placement` is the recommended per-beat default and `music_bed` the
bed intensity applied when a `bed_asset` is supplied.

#### render(script, , source, out_path=None, profile=Profile.PERSONAL, \*\*overrides)

Render `script` with this format’s defaults (see [`render_format()`](#braidio.formats.render_format)).

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### braidio.formats.describe_asset_application(fmt, script, , bed_asset=None, sting_asset=None)

Which of the supplied `bed_asset` / `sting_asset` this format will
actually render, and why not otherwise (braidio#43).

Pure and pre-render — safe to call before paying for anything. A key stays
`None` when its asset wasn’t supplied. `bed_asset`’s fate is fixed by
`fmt.music_bed` alone: `"none"` never renders a bed, and
[`render_format()`](#braidio.formats.render_format) refuses `bed_asset` there rather than spend on one
that would be dropped, so `bed_applied` is `False` only via that refusal
path, never in a result you got back from a successful render.
`sting_asset`’s fate additionally depends on `script`: a scene break’s
marker can override the format’s default, so a sting can still legitimately
go unused in one script and play in another under the same format — that
case is reported here, not refused.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`bool`](https://docs.python.org/3/builtins/functions.html#bool) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)]

### braidio.formats.render_format(fmt, script, , source, out_path=None, profile=Profile.PERSONAL, bed_asset=None, sting_asset=None, \*\*overrides)

Render `script` under `fmt`’s defaults; `overrides` win over them.

Wires the format’s `cast` / `narration_voice` / `narration_delivery` /
`weave` / `structure` into [`braidio.render.render_production()`](braidio.render.html.md#braidio.render.render_production). Any
beat may still override voice/settings per-beat (e.g. a graver book-narrator
inside an otherwise lively presenter piece — pass `V2_NARRATOR.voice_settings`
on that `Narration` beat).

`bed_asset` (a path to an app-supplied instrumental) adds a music bed at the
gain implied by `fmt.music_bed`; pass `music_bed=MusicBed(...)` in
`overrides` for full control. A format whose `music_bed` is `"none"`
(e.g. `SONG_EXPLODER`) never renders a bed at all, so `bed_asset` there
raises `ValueError` *before* any rendering — the caller would otherwise pay
for a bed the format silently drops (braidio#43) — unless `overrides`
itself supplies `music_bed=`, which always wins and makes the refusal moot.
`sting_asset` (a path to an app-supplied short marker) is what a
`SceneBreak` plays under the format’s `structure`; pass
`structure=MusicStructure(...)` in `overrides` for full control. Unlike
the bed, a format’s `scene_marker` is only the *default* — an individual
`SceneBreak.marker` override can still play the sting even under a
`"none"` default — so a sting that ends up unused is not refused, only
reported (see [`describe_asset_application()`](#braidio.formats.describe_asset_application)).

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### braidio.formats.sting_would_play(structure, script, sting_asset)

Whether some `SceneBreak` in `script` resolves to marker `"sting"`
under `structure` once `sting_asset` is wired in as the sting.

Takes a bare `MusicStructure` (not a [`Format`](#braidio.formats.Format)) so callers with
no format at all — e.g. the graph path’s `_graph_structure` with no
`format_id` — can still ask against the ad hoc default structure they
build in that case (braidio#53).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)
