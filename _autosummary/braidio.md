# braidio

braidio — weave narration with extracted media segments into productions.

Braid two kinds of strand into one production: authored **narration** (TTS —
single voice or a cycled pool) and extracted **segments** of source media (song
clips, audiobook passages, news, SFX). The result renders to an audiovisual
object.

This is the **functional core** (pure Python over files + numbers; deps:
`mixing`, `elevenlabs`, and `ffmpeg` on PATH). An optional nw-app layer
(graph bodies, transforms, provenance / partial re-render) will be added on top
and imported only when `nw` is available — `import braidio` never requires
it.

Extracted from the Hamilton lyrics-podcast; the extraction is designed in
Hamilton#18 (the epic) and Hamilton#28 (placement + naming). Those numbers are
in the *Hamilton* repo — braidio has its own #18 about something else.

### Functions

| [`skills_dir`](#braidio.skills_dir)()                                       | Path to the agent skills that ship with braidio.                                                                        |
|-----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| [`captions_for`](#braidio.captions_for)(script, timeline, \*[, max_chars])    | The SRT document for `script` as laid out by `timeline`.                                                                |
| [`cues_for`](#braidio.cues_for)(script, timeline, \*[, max_chars])        | Subtitle cues for `script` as laid out by `timeline`.                                                                   |
| [`format_srt`](#braidio.format_srt)(cues)                                   | Render `cues` as an SRT document.                                                                                       |
| [`narration_segments`](#braidio.narration_segments)(script)                         | All narration (default text) of a script, as sentence-level segments.                                                   |
| [`plan_production`](#braidio.plan_production)(script, profile, \*[, ...])        | Filter `script` into the beats renderable under `profile`.                                                              |
| [`clip_plays_under`](#braidio.clip_plays_under)(profile, rights[, publishable])   | Whether a segment with `rights` plays as audio under `profile`.                                                         |
| [`rights_are_publishable`](#braidio.rights_are_publishable)(rights[, publishable])      | The publishable test at the level of a bare `rights` string.                                                            |
| [`find_verbatim_text`](#braidio.find_verbatim_text)(text, forbidden, \*[, ...])     | Forbidden lines that appear (near-)verbatim in `text`.                                                                  |
| [`content_violations`](#braidio.content_violations)(plan, forbidden, \*[, ...])     | Rights violations in a *published* plan (empty list = clean).                                                           |
| [`segment_is_publishable`](#braidio.segment_is_publishable)(beat[, publishable])        | Whether `beat`'s audio may play in the published cut.                                                                   |
| [`render_production`](#braidio.render_production)(script, \*, source[, ...])       | Render `script` under `profile` → a single audio file.                                                                  |
| [`find_segment`](#braidio.find_segment)(lines, quote, \*[, max_span, ...])    | Best contiguous run of timed lines matching `quote`, or `None`.                                                         |
| [`load_timing`](#braidio.load_timing)(path)                                  | Load a `{lines: [{index,start_s,end_s,text}]}` JSON into <br/><br/>```<br/>`<br/>```<br/><br/>TimedLine\`s.             |
| [`cut_quote`](#braidio.cut_quote)(audio_path, lines, quote, out_path, \*)  | Resolve `quote` → segment and cut it from `audio_path` (pad + fades).                                                   |
| [`narrate`](#braidio.narrate)(text, out_path, \*[, api_key, ...])        | Synthesize `text` to `out_path` (mp3).                                                                                  |
| [`resolve_voice_id`](#braidio.resolve_voice_id)([voice_id])                       | Voice id from arg → `VOICE_ENV_VAR` env → default.                                                                      |
| [`estimate_cost`](#braidio.estimate_cost)(source, \*[, model_id])              | Estimate ElevenLabs spend for a [`braidio.Script`](#braidio.Script) or a raw string.        |
| [`tts_cost_usd`](#braidio.tts_cost_usd)(text, \*[, model_id])                 | Estimated USD to synthesize `text`; `0.0` for empty, `None` if unpriced.                                                |
| [`billable_chars`](#braidio.billable_chars)(text)                               | Characters ElevenLabs bills for `text` (the whole submitted string).                                                    |
| [`usd_per_1k_chars`](#braidio.usd_per_1k_chars)([model_id])                       | Resolved USD-per-1000-characters rate (most specific source first).                                                     |
| `strip_markup`(text)                                                                                |                                                                                                                         |
| [`split_segments`](#braidio.split_segments)(text)                               | Split narration into sentence-level segments (markup removed).                                                          |
| [`assign_voices`](#braidio.assign_voices)(n, pool, \*[, seed, avoid_repeats])  | Assign a voice to each of `n` turns — random, seeded, no immediate repeats (so it isn't a rigid round-robin).           |
| [`group_turns`](#braidio.group_turns)(segments, \*[, min_turn, ...])         | Group consecutive segments into *turns* of `min_turn..max_turn` segments.                                               |
| [`render_multivoice`](#braidio.render_multivoice)(segments, pool, \*, out_path)    | Render `segments` cycling `pool` → `out_path`.                                                                          |
| [`bed_for_intensity`](#braidio.bed_for_intensity)(asset_path, intensity, ...)      | Build a [`MusicBed`](#braidio.MusicBed) at the gain for a Format `music_bed` intensity.       |
| [`render_format`](#braidio.render_format)(fmt, script, \*, source[, ...])      | Render `script` under `fmt`'s defaults; `overrides` win over them.                                                      |
| [`describe_asset_application`](#braidio.describe_asset_application)(fmt, script, \*[, ...]) | Which of the supplied `bed_asset` / `sting_asset` this format will actually render, and why not otherwise (braidio#43). |
| [`compose_narration`](#braidio.compose_narration)(segments, config, \*, out_path)  | Render `segments` under `config` → `out_path`.                                                                          |
| [`extract_padded`](#braidio.extract_padded)(asset_path, start_s, end_s, ...)    | Extract `[start_s-pre_roll, end_s+post_roll]` with in/out fades.                                                        |
| [`weave_timeline`](#braidio.weave_timeline)(items, out_path, \*[, ...])         | Place items on a timeline and mix.                                                                                      |
| [`layout_starts`](#braidio.layout_starts)(kinds, durs, \*, ...)                | Start offset (s) of each part (all sequential).                                                                         |
| `duration_s`(path)                                                                                  |                                                                                                                         |
| [`build_timeline`](#braidio.build_timeline)(\*, kinds, durations[, ...])        | Assemble a [`TimelineBreakdown`](#braidio.TimelineBreakdown) from per-beat render data (pure).         |
| [`render_settings`](#braidio.render_settings)(\*, config, crossfade_s, ...)      | The record of *how* a production was rendered, as plain JSON types.                                                     |
| [`clean_ocr`](#braidio.clean_ocr)(text, \*[, collapse_whitespace])         | Normalize OCR/PDF-extracted text for clean narration.                                                                   |
| [`strip_speaker_labels`](#braidio.strip_speaker_labels)(text)                         | Remove a leading speaker-label prefix (e.g. `"Chris: "`) if present.                                                    |
| [`config_path`](#braidio.config_path)()                                      | Where the user's persisted defaults live (the file need not exist).                                                     |
| [`default_delivery`](#braidio.default_delivery)([explicit])                       | Resolve the delivery to render with.                                                                                    |
| [`default_voice_id`](#braidio.default_voice_id)([explicit])                       | Resolve the narration voice id, or `None` to let the caller decide.                                                     |
| [`default_voice_settings`](#braidio.default_voice_settings)()                           | The resolved delivery's voice settings, as a fresh dict.                                                                |
| [`describe_defaults`](#braidio.describe_defaults)()                                | What is in force, and where each part came from.                                                                        |
| [`user_config`](#braidio.user_config)()                                      | The user's persisted defaults, or `{}`.                                                                                 |
| [`audit_platitudes`](#braidio.audit_platitudes)(text)                             | Return every [`Finding`](#braidio.Finding) in `text`, in document order.                     |
| [`audit_expressiveness`](#braidio.audit_expressiveness)(text)                         | Human-readable complaints about a script's written-in performance.                                                      |
| [`audio_tag_rate`](#braidio.audio_tag_rate)(text, \*[, per])                    | Inline audio tags per `per` words — the expressiveness dial.                                                            |
| [`audio_tags`](#braidio.audio_tags)(text)                                   | Every inline `[audio tag]` in `text`, in order.                                                                         |
| [`platitude_rate`](#braidio.platitude_rate)(text, \*[, per])                    | Flagged hits per `per` words (default 1000).                                                                            |

### Classes

| [`WeaveKind`](#braidio.WeaveKind)(\*values)                               | A braidio production kind.                                                                                         |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| [`Cue`](#braidio.Cue)(start_s, end_s, text)                         | One subtitle: `[start_s, end_s)` and the text shown.                                                               |
| [`Script`](#braidio.Script)(title, id_slug[, beats])                   | An ordered production script.                                                                                      |
| [`Narration`](#braidio.Narration)(text[, style, published_text, ...])     | A spoken narration beat (authored, synthesized by TTS).                                                            |
| [`SegmentBeat`](#braidio.SegmentBeat)(reference[, label, rights, ...])      | A span of source media to weave in, addressed by an opaque `reference`.                                            |
| [`Dialogue`](#braidio.Dialogue)(turns[, label])                          | A multi-speaker commentary exchange (the conversational register).                                                 |
| [`SceneBreak`](#braidio.SceneBreak)([label, marker])                       | A structural boundary between sections — the "new scene" beat.                                                     |
| [`Sting`](#braidio.Sting)(asset_path[, gain_db, max_len_s, ...])      | A short musical marker played at a scene break.                                                                    |
| [`MusicStructure`](#braidio.MusicStructure)([sting, scene_marker, ...])        | How a production marks its structure with music — the render seam.                                                 |
| [`Profile`](#braidio.Profile)(\*values)                                 | Which projection of the production we render.                                                                      |
| [`RightsPolicy`](#braidio.RightsPolicy)([forbidden_texts, ...])              | Injected rights configuration for the published profile.                                                           |
| [`RenderPlan`](#braidio.RenderPlan)(profile[, beats, dropped, ...])        |                                                                                                                    |
| [`PlannedBeat`](#braidio.PlannedBeat)(kind, content, from_index[, ...])     | A beat resolved for a profile — what the renderer actually plays.                                                  |
| [`SegmentSource`](#braidio.SegmentSource)(\*args, \*\*kwargs)                 | Resolve an opaque `reference` to a [`ResolvedSegment`](#braidio.ResolvedSegment) (or None).     |
| [`ResolvedSegment`](#braidio.ResolvedSegment)(asset_path, start_s, end_s)       | A cuttable span of a source asset — what a [`SegmentSource`](#braidio.SegmentSource) returns. |
| [`Segment`](#braidio.Segment)(start_s, end_s, score, line_start, ...)   | A resolved window for a reference, with the matched line span.                                                     |
| [`TimedLine`](#braidio.TimedLine)(index, start_s, end_s, text)            | A source line with a `[start_s, end_s)` window (end may be None = tail).                                           |
| [`NamespacedSegmentSource`](#braidio.NamespacedSegmentSource)(sources, \*[, sep, ...])  | A [`SegmentSource`](#braidio.SegmentSource) that routes prefixed references to sub-sources.   |
| [`TimedLineSegmentSource`](#braidio.TimedLineSegmentSource)(\*, lines, asset_path)     | A [`SegmentSource`](#braidio.SegmentSource) over time-aligned lines + one source asset.       |
| [`CostRollup`](#braidio.CostRollup)(characters, usd, unpriced[, lines])    | A production's estimated TTS spend, exact on characters, honest on dollars.                                        |
| [`CostLine`](#braidio.CostLine)(label, kind, characters, usd, model_id)  | One billable line of an estimate (a narration beat or a dialogue beat).                                            |
| [`Delivery`](#braidio.Delivery)(name, model_id[, voice_settings, ...])   | A named narration delivery: which model + voice settings to synthesize with.                                       |
| [`Voice`](#braidio.Voice)(id, name, gender[, accent, note])           | A pooled narration voice.                                                                                          |
| [`WeaveConfig`](#braidio.WeaveConfig)([voices, pool_label, ...])            | All editing choices for a narration+segment weave.                                                                 |
| [`MusicBed`](#braidio.MusicBed)(asset_path[, gain_db, fade_in_s, ...])   | An instrumental underscore spanning the production, mixed under the talk.                                          |
| [`Format`](#braidio.Format)(id, name, summary[, aka, cast, ...])       | A named commentary-format preset: a bundle of high-quality defaults.                                               |
| [`TimelineItem`](#braidio.TimelineItem)(kind, path[, placement, ...])        | One part on the weave timeline.                                                                                    |
| [`BeatSpan`](#braidio.BeatSpan)(index, kind[, label, source_start, ...]) | One beat's place on the timeline.                                                                                  |
| [`TimelineBreakdown`](#braidio.TimelineBreakdown)([beats, title, settings])       | The ordered beats of a production, with per-kind totals and an HTML view.                                          |
| [`Finding`](#braidio.Finding)(pattern, match, start)                    | One flagged platitude.                                                                                             |

### Exceptions

| [`RightsViolation`](#braidio.RightsViolation)   | A render would play source audio the profile it claims forbids.   |
|--------------------------------------------------------------------|-------------------------------------------------------------------|

### *class* braidio.BeatSpan(index, kind, label='', source_start=None, source_end=None, duration=0.0, start=0.0, placement=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One beat’s place on the timeline.

### *class* braidio.CostLine(label, kind, characters, usd, model_id)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One billable line of an estimate (a narration beat or a dialogue beat).

Named to echo `falaw.CostLine` (a line within a rollup) rather than
`falaw.CostEstimate` (which means a per-call price spec) — so the vocabulary
is consistent across the federation.

### *class* braidio.CostRollup(characters, usd, unpriced, lines=())

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A production’s estimated TTS spend, exact on characters, honest on dollars.

`usd` is the sum of the *priced* lines; `unpriced` is `True` when some
billable text had no configured rate (so `usd` is a lower bound). No billable
lines (e.g. an all-segment script) gives `characters=0, usd=0.0,
unpriced=False`. Named `CostRollup` to match `falaw.CostRollup`.

#### *property* summary *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

One-line human summary (handy for a CLI/MCP preview).

### *class* braidio.Cue(start_s, end_s, text)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One subtitle: `[start_s, end_s)` and the text shown.

### *class* braidio.Delivery(name, model_id, voice_settings=<factory>, supports_audio_tags=False, supports_speed=True, note='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A named narration delivery: which model + voice settings to synthesize with.

#### supports_speed *: [bool](https://docs.python.org/3/builtins/functions.html#bool)* *= True*

Whether the model honors `voice_settings["speed"]`. \*\*False for eleven
v3\*\*, which has no speed control at all (“Speed is not available for the
Eleven v3 model”), so sending one there is undefined. The paced narration
path ([`braidio.pacing`](braidio.pacing.md#module-braidio.pacing)) reads this: on a speedless model it plans no
speed and varies tempo through real inter-turn silence, punctuation and
audio tags instead.

### *class* braidio.Dialogue(turns, label='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A multi-speaker commentary exchange (the conversational register).

`turns` is an ordered tuple of `(role, text)` pairs (roles like
`"A"`/`"B"` map to voices via a [`ConversationCast`](braidio.conversation.md#braidio.conversation.ConversationCast)
at render time). Rendered in ONE pass via Text-to-Dialogue so it sounds like
people talking to each other. This is our own commentary → always publishable
(its text is still scanned for forbidden verbatim quotes in the published cut).

### *class* braidio.Finding(pattern, match, start)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One flagged platitude.

### *class* braidio.Format(id, name, summary, aka=(), cast=None, narration_voice=None, narration_delivery=Delivery(name='v2-presenter', model_id='eleven_multilingual_v2', voice_settings={'stability': 0.35, 'similarity_boost': 0.75, 'style': 0.35, 'use_speaker_boost': True, 'speed': 0.98}, supports_audio_tags=False, supports_speed=True, note='Host/presenter commentary — lively (== v2-tuned), for the spine voice.'), weave=<factory>, structure=<factory>, roles=<factory>, clip_placement='before', music_bed='light', scripting='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A named commentary-format preset: a bundle of high-quality defaults.

Rendered fields drive [`render_format()`](#braidio.render_format) → [`braidio.render.render_production()`](braidio.render.md#braidio.render.render_production).
Authoring fields document how to write a `Script` for this format;
`clip_placement` is the recommended per-beat default and `music_bed` the
bed intensity applied when a `bed_asset` is supplied.

#### render(script, , source, out_path=None, profile=Profile.PERSONAL, \*\*overrides)

Render `script` with this format’s defaults (see [`render_format()`](#braidio.render_format)).

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### *class* braidio.MusicBed(asset_path, gain_db=-22.0, fade_in_s=1.5, fade_out_s=2.0, lead_in_s=2.0, start_s=0.0, loop=True, spotlight_fade_s=0.6)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

An instrumental underscore spanning the production, mixed under the talk.

`asset_path` is an app-supplied instrumental. `gain_db` sets how far under
the voice it sits; `lead_in_s` posts the bed *after* speech onset so its
entrance feels motivated; `start_s` is the in-point into the asset;
`loop` repeats the asset to cover a timeline longer than it.
`spotlight_fade_s` is how fast the bed drops out before a spotlit exhibit
(it resumes after the exhibit over `fade_in_s`).

### *class* braidio.MusicStructure(sting=None, scene_marker='sting', spotlight_clips=False, pause_s=0.8)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

How a production marks its structure with music — the render seam.

`scene_marker` is the default for a [`SceneBreak`](braidio.script.md#braidio.script.SceneBreak)
whose `marker` is `None` (`"sting"` / `"none"`). `spotlight_clips`
is the default for a [`SegmentBeat`](braidio.script.md#braidio.script.SegmentBeat) whose
`spotlight` is `None` — `True` drops the bed under every exhibit.
`sting` is the production’s sting asset; with none supplied a
`"sting"`-marked break falls back to `pause_s` of silence.

The all-defaults value is inert for a script with no scene breaks and no
spotlight-marked clips: a format that declares no structure renders exactly
as it did without this layer.

#### marker_for(beat)

The marker a scene break renders with (its override, else the default).

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

#### marker_of(marker)

The marker a break with this per-beat override renders with.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

#### plays_sting(beat)

Whether `beat` plays the sting (marked `"sting"` *and* one is supplied).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

#### plays_sting_of(marker)

Whether this per-beat marker plays the sting (and one is supplied).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

#### spotlight_for(beat)

Whether `beat` is spotlit (its override, else the format default).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

#### spotlight_of(spotlight)

Whether this per-beat override is spotlit (`None` = the format default).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### *class* braidio.NamespacedSegmentSource(sources, , sep=':', default=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A [`SegmentSource`](#braidio.SegmentSource) that routes prefixed references to sub-sources.

A production that cuts between *several* recordings of the same work — two
takes of one song, a studio master and a live version, an audiobook read in
two languages — hits a wall: `render_production(..., source=...)` takes one
[`SegmentSource`](#braidio.SegmentSource), and a reference like a lyric line matches equally
well in every recording. Which asset a quote should come from is a
*production* decision, not something a token matcher can infer.

This routes on an explicit prefix, so the script says which recording it
means:

```default
source = NamespacedSegmentSource(
    {
        "1966": TimedLineSegmentSource(lines=studio, asset_path="studio.mp3"),
        "1981": TimedLineSegmentSource(lines=live, asset_path="live.mp3"),
    }
)
SegmentBeat("1981: and in the naked light I saw")  # → the live master
```

* **Parameters:**
  * **sources** ([`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`SegmentSource`](braidio.sources.md#braidio.sources.SegmentSource)]) – Namespace → sub-source. Namespaces are matched
    case-insensitively and with surrounding whitespace stripped.
  * **sep** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Separator between the namespace and the reference body.
  * **default** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Namespace used for an *unprefixed* reference. `None` (the
    default) makes an unprefixed reference an error.
* **Raises:**
  [**KeyError**](https://docs.python.org/3/builtins/exceptions.html#KeyError) – on an unknown namespace, or an unprefixed reference with no
      `default`. This is deliberate: silently falling through to the
      wrong recording would ship one performance under commentary that
      describes another — the failure a listener cannot detect and the
      author cannot see in a diff.

#### *property* namespaces *: [list](https://docs.python.org/3/builtins/stdtypes.html#list)[[str](https://docs.python.org/3/builtins/stdtypes.html#str)]*

The namespaces this source routes, in insertion order.

#### split(reference)

Split `reference` into `(namespace, body)`, applying the default.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### *class* braidio.Narration(text, style=None, published_text=None, lead_gap_s=0.0, voice=None, voice_settings=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A spoken narration beat (authored, synthesized by TTS).

`published_text` is an optional rights-safe rewrite the published profile
uses when the default `text` quotes forbidden (e.g. copyrighted) content;
leave it `None` when the narration is already clean.

`lead_gap_s` prepends a beat of silence — breathing room before a
register change (e.g. a book-read entering after a conversation), so it
doesn’t feel glued to the previous speaker.

`voice` and `voice_settings` are per-beat overrides so one timeline can
carry contrasting roles — e.g. a lively *presenter* and a graver \*book
narrator\* (different voice, and/or a different delivery preset’s
`voice_settings`). Both fall back to the render’s production-level defaults
when `None`.

### *class* braidio.PlannedBeat(kind, content, from_index, note='', turns=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A beat resolved for a profile — what the renderer actually plays.

`kind` is `"narration"` (synthesize `content`), `"clip"` (resolve
`content` as a segment reference and cut audio), `"dialogue"` (synthesize
`turns`) or `"scene_break"` (a structural boundary — no content; the
renderer marks it with music). `from_index` points at the source beat;
`note` records any substitution/drop reasoning.

### *class* braidio.Profile(\*values)

Bases: [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Enum`](https://docs.python.org/3/library/enum.html#enum.Enum)

Which projection of the production we render.

### *class* braidio.RenderPlan(profile, beats=<factory>, dropped=<factory>, substituted=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### *class* braidio.ResolvedSegment(asset_path, start_s, end_s, score=1.0, matched_text='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A cuttable span of a source asset — what a [`SegmentSource`](#braidio.SegmentSource) returns.

### *class* braidio.RightsPolicy(forbidden_texts=<function RightsPolicy.<lambda>>, publishable_clip_rights=frozenset({'public-domain'}))

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Injected rights configuration for the published profile.

`forbidden_texts` yields the strings that must not appear verbatim in
published narration (e.g. copyrighted lyric lines). `publishable_clip_rights`
is the set of segment `rights` values allowed in the published cut.

### *exception* braidio.RightsViolation

Bases: [`ValueError`](https://docs.python.org/3/builtins/exceptions.html#ValueError)

A render would play source audio the profile it claims forbids.

Raised where a rights decision is *verified* rather than made — today by the
graph path’s episode transform, which checks the members it is about to
weave against the profile it is about to stamp on them. A `ValueError`
subclass so existing callers keep catching it, typed so a caller that cares
can tell a rights refusal from a malformed input.

### *class* braidio.SceneBreak(label='', marker=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A structural boundary between sections — the “new scene” beat.

Audio has no visual white space, so a boundary has to be *heard*: the
renderer marks it with a sting (a short musical marker) when the production
supplies one and the resolved `marker` is `"sting"`, and otherwise with
a beat of silence. This is the beat the format templates’ structure — a
debate’s open / rebuttal / close, a panel’s rounds, a documentary’s acts —
is expressed with; the talk on either side is unchanged.

`marker` overrides the format default per break (`None` = defer to
`braidio.structure.MusicStructure.scene_marker`). `label` names the
section that starts here (e.g. `"rebuttal"`) for the timeline breakdown.
A scene break synthesizes nothing, so it costs nothing.

### *class* braidio.Script(title, id_slug, beats=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

An ordered production script.

### *class* braidio.Segment(start_s, end_s, score, line_start, line_end, matched_text)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A resolved window for a reference, with the matched line span.

### *class* braidio.SegmentBeat(reference, label='', rights='owned-local', published_substitute=None, placement='before', spotlight=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A span of source media to weave in, addressed by an opaque `reference`.

The renderer resolves `reference` → `[start,end)` via a
[`SegmentSource`](braidio.sources.md#braidio.sources.SegmentSource) and cuts it. `rights` (e.g.
`owned-local` / `copyrighted` / `public-domain`) drives the render
profile; `published_substitute` is a transformative narration the
published profile swaps in when the segment’s audio can’t be used.

`placement` is how the clip sits against the talk (the weaving grammar):
`"before"` (default) and `"after"` play the clip **clean** in its own
slot — the set-up→clip and clip→payoff patterns, distinguished by where you
order the beat relative to the talk. `"under"` plays the clip
**concurrently beneath the following talk beat**, ducked by `duck_db` —
the “host talks over the clip” technique (place the clip *immediately before*
the talk it should sit under).

`spotlight` is the fade-to-spotlight override: `True` drops the music
bed out before this clip so it lands in silence rather than competing with
underscore (the bed resumes after it); `False` keeps the bed under it.
`None` (default) defers to the format’s
`braidio.structure.MusicStructure.spotlight_clips`. Without a music
bed the flag is inert.

### *class* braidio.SegmentSource(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Resolve an opaque `reference` to a [`ResolvedSegment`](#braidio.ResolvedSegment) (or None).

### *class* braidio.Sting(asset_path, gain_db=-6.0, max_len_s=3.0, fade_out_s=0.5, gap_after_s=0.3)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A short musical marker played at a scene break.

`asset_path` is an app-supplied sound (a hit, a riser, a few notes of the
theme). It is trimmed to `max_len_s` with a `fade_out_s` tail, levelled
to the production’s loudness target, sat `gain_db` under the voice, and
followed by `gap_after_s` of breathing room before the talk resumes.

### *class* braidio.TimedLine(index, start_s, end_s, text)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A source line with a `[start_s, end_s)` window (end may be None = tail).

### *class* braidio.TimedLineSegmentSource(, lines, asset_path, song_end_s=None, min_score=0.5)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A [`SegmentSource`](#braidio.SegmentSource) over time-aligned lines + one source asset.

Binds the generic [`find_segment()`](#braidio.find_segment) matcher to a concrete asset so the
weave engine can `resolve(reference) -> ResolvedSegment`.

### *class* braidio.TimelineBreakdown(beats=<factory>, title='', settings=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The ordered beats of a production, with per-kind totals and an HTML view.

`settings` is the **render record**: the settings that actually produced
these timings (see [`render_settings()`](#braidio.render_settings)), or `None` for a breakdown
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
  [`TimelineBreakdown`](braidio.timeline.md#braidio.timeline.TimelineBreakdown)

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

### *class* braidio.TimelineItem(kind, path, placement='sequential', duck_db=0.0, spotlight=False)

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

### *class* braidio.Voice(id, name, gender, accent='', note='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A pooled narration voice.

### *class* braidio.WeaveConfig(voices=('JBFqnCBsd6RMkjVDRZzb', ), pool_label='single', voice_seed=7, avoid_immediate_repeat=True, model_id='eleven_multilingual_v2', voice_settings=<factory>, segmentation_unit='beat', min_turn=2, max_turn=4, speed_base=1.0, speed_jitter=0.04, crossfade_s=0.12, gap_turn_s=0.0, overlap_turn_s=0.0, clip_pre_roll_s=0.4, clip_post_roll_s=0.3, clip_fade_in_s=0.5, clip_fade_out_s=0.8, clip_min_len_s=2.2, clip_edge_overlap_s=0.5, duck_db=-15.0, target_lufs=-16.0, true_peak_dbtp=-1.0, sample_rate=44100)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

All editing choices for a narration+segment weave. Frozen + serializable.

#### *property* paces_narration *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

Whether a render cuts each narration beat into separately-spoken turns.

`False` (the default, `segmentation_unit="beat"`) is one TTS call per
beat — no intra-beat gaps, no per-turn speed, so `gap_turn_s` and
`speed_jitter` do nothing on the [`braidio.render.render_production()`](braidio.render.md#braidio.render.render_production)
path. Opt in by setting `segmentation_unit` to a smaller unit.

```pycon
>>> WeaveConfig().paces_narration
False
>>> WeaveConfig(segmentation_unit="sentence").paces_narration
True
```

#### to_dict()

Stable serialization for a `render-config` provenance node (#26).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

#### with_(\*\*changes)

Return a copy with fields overridden (e.g. `cfg.with_(min_turn=1)`).

* **Return type:**
  [`WeaveConfig`](braidio.weave_config.md#braidio.weave_config.WeaveConfig)

### *class* braidio.WeaveKind(\*values)

Bases: [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Enum`](https://docs.python.org/3/library/enum.html#enum.Enum)

A braidio production kind. `COMMENTARY_WEAVE` = narration woven with
extracted media segments (audio now, video later).

### braidio.assign_voices(n, pool, , seed=0, avoid_repeats=True)

Assign a voice to each of `n` turns — random, seeded, no immediate
repeats (so it isn’t a rigid round-robin).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Voice`](braidio.multivoice.md#braidio.multivoice.Voice)]

### braidio.audio_tag_rate(text, , per=100)

Inline audio tags per `per` words — the expressiveness dial.

Only meaningful on a delivery whose model renders tags at all
(`braidio.delivery.Delivery.supports_audio_tags`); on
`eleven_multilingual_v2` the tags are inert text and this number is a lie.

* **Return type:**
  [`float`](https://docs.python.org/3/builtins/functions.html#float)

### braidio.audio_tags(text)

Every inline `[audio tag]` in `text`, in order.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### braidio.audit_expressiveness(text)

Human-readable complaints about a script’s written-in performance.

Returns an empty list when the script sits in the target band. This is the
gate [`audit_platitudes()`](#braidio.audit_platitudes) is not: platitudes catch recycled *phrases*,
this catches a script that will be read flatly however good the words are.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### braidio.audit_platitudes(text)

Return every [`Finding`](#braidio.Finding) in `text`, in document order.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Finding`](braidio.style.md#braidio.style.Finding)]

### braidio.bed_for_intensity(asset_path, intensity, \*\*overrides)

Build a [`MusicBed`](#braidio.MusicBed) at the gain for a Format `music_bed` intensity.

Returns `None` for `"none"` (or an unknown intensity), so callers can do
`bed = bed_for_intensity(asset, fmt.music_bed)` and skip when falsy.

* **Return type:**
  [`MusicBed`](braidio.music.md#braidio.music.MusicBed) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### braidio.billable_chars(text)

Characters ElevenLabs bills for `text` (the whole submitted string).

ElevenLabs charges for everything sent — including `eleven_v3` audio tags
like `[excited]` — so this is just `len(text)`. A named function keeps the
billing definition in one place if it ever needs to change.

* **Return type:**
  [`int`](https://docs.python.org/3/builtins/functions.html#int)

```pycon
>>> billable_chars("hello")
5
>>> billable_chars(None)
0
```

### braidio.build_timeline(, kinds, durations, placements=None, labels=None, source_spans=None, clip_edge_overlap_s=0.5, narration_crossfade_s=0.12, title='', settings=None)

Assemble a [`TimelineBreakdown`](#braidio.TimelineBreakdown) from per-beat render data (pure).

`kinds` are the aggregation labels (any string; `"clip"` is the only one
the layout treats specially — as an overlapping segment). `placements` is
the per-beat `"sequential"`/`"under"` used by the weave; offsets are
computed with the same [`layout_placed()`](braidio.weave.md#braidio.weave.layout_placed) the renderer uses.

`settings` is the optional render record ([`render_settings()`](#braidio.render_settings)) — what
produced these durations. It stays optional so building a timeline by hand
(tests, a hand-cut episode, the captions/video doctests) needs nothing
extra; a real render always passes one.

* **Return type:**
  [`TimelineBreakdown`](braidio.timeline.md#braidio.timeline.TimelineBreakdown)

### braidio.captions_for(script, timeline, , max_chars=0)

The SRT document for `script` as laid out by `timeline`.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.clean_ocr(text, , collapse_whitespace=True)

Normalize OCR/PDF-extracted text for clean narration.

Expands ligatures, removes soft-hyphens (used at scan line-breaks), turns a
doubled hyphen `--` into an em-dash (so TTS phrases it as a pause), and
(by default) collapses runs of whitespace to single spaces.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.clip_plays_under(profile, rights, publishable=frozenset({'public-domain'}))

Whether a segment with `rights` plays as audio under `profile`.

The whole clip-routing rule, in one place: `PERSONAL` plays everything,
`PUBLISHED` plays only publishable rights. [`plan_production()`](#braidio.plan_production) asks it
when it filters a script, and the graph’s episode transform re-asks it at
weave time to verify that the members it inherited still match the profile
the production now declares — one rule, asked twice, never copied.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### braidio.compose_narration(segments, config, , out_path, api_key=None, work_dir='data/tts/compose')

Render `segments` under `config` → `out_path`.

Single-voice and multi-voice go through the same turn-based path (a single
voice is just a one-voice pool). Returns the `(voice, turn_text)`
assignment for reporting / provenance.

`api_key` is an optional per-request ElevenLabs key threaded to
[`braidio.multivoice.render_multivoice()`](braidio.multivoice.md#braidio.multivoice.render_multivoice) (and thence every synthesized
turn); `None` (default) keeps the `$ELEVENLABS_API_KEY` fallback.

Note `config.segmentation_unit` is **not** read here: this function is
handed `segments` already cut, so the unit was chosen upstream (usually
[`braidio.script.narration_segments()`](braidio.script.md#braidio.script.narration_segments)). It is the
[`braidio.render.render_production()`](braidio.render.md#braidio.render.render_production) path that cuts a beat itself, and
there the field is the switch that turns pacing on — see
[`braidio.pacing`](braidio.pacing.md#module-braidio.pacing).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Voice`](braidio.multivoice.md#braidio.multivoice.Voice), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

### braidio.config_path()

Where the user’s persisted defaults live (the file need not exist).

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### braidio.content_violations(plan, forbidden, , min_words=5)

Rights violations in a *published* plan (empty list = clean).

Fails if any planned beat plays non-publishable segment audio, or any
narration beat contains forbidden verbatim text.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### braidio.cues_for(script, timeline, , max_chars=0)

Subtitle cues for `script` as laid out by `timeline`.

Beats are matched by index, so a timeline built from the same script lines up
even when some beats were dropped by a rights profile (a dropped beat simply
has no span and contributes no cue).

Cues never overlap. That matters because the weave *crossfades* consecutive
beats, so a beat’s `start` sits slightly before the previous beat’s end;
left alone that produces subtitles that fight each other. Each cue is clamped
to begin where the previous one finished.

* **Parameters:**
  * **script** – the [`Script`](braidio.script.md#braidio.script.Script) that was rendered.
  * **timeline** – the [`TimelineBreakdown`](braidio.timeline.md#braidio.timeline.TimelineBreakdown) from
    `render_production(..., return_timeline=True)`.
  * **max_chars** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – if > 0, split sentences longer than this at whitespace, so no
    single cue overflows a player’s two lines. 0 leaves sentences whole.
* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Cue`](braidio.captions.md#braidio.captions.Cue)]
* **Returns:**
  Cues in playback order.

### braidio.cut_quote(audio_path, lines, quote, out_path, , pad_pre_s=0.15, pad_post_s=0.35, fade_s=0.04, min_score=0.5, song_end_s=None)

Resolve `quote` → segment and cut it from `audio_path` (pad + fades).

Convenience combining [`find_segment()`](#braidio.find_segment) + an ffmpeg cut. Returns the
resolved [`Segment`](#braidio.Segment) (raises `LookupError` if unmatched). New code
should prefer a [`SegmentSource`](#braidio.SegmentSource) + [`braidio.weave.extract_padded()`](braidio.weave.md#braidio.weave.extract_padded).

* **Return type:**
  [`Segment`](braidio.sources.md#braidio.sources.Segment)

### braidio.default_delivery(explicit=None)

Resolve the delivery to render with.

* **Parameters:**
  **explicit** ([`Delivery`](braidio.delivery.md#braidio.delivery.Delivery) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – a [`Delivery`](braidio.delivery.md#braidio.delivery.Delivery), or the name of one
  (see `braidio.delivery.DELIVERIES`). Wins over everything.
* **Return type:**
  [`Delivery`](braidio.delivery.md#braidio.delivery.Delivery)

### Examples

```pycon
>>> default_delivery(V3_PRESENTER) is V3_PRESENTER
True
>>> default_delivery("v3-narrator").name
'v3-narrator'
```

### braidio.default_voice_id(explicit=None)

Resolve the narration voice id, or `None` to let the caller decide.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### braidio.default_voice_settings()

The resolved delivery’s voice settings, as a fresh dict.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### braidio.describe_asset_application(fmt, script, , bed_asset=None, sting_asset=None)

Which of the supplied `bed_asset` / `sting_asset` this format will
actually render, and why not otherwise (braidio#43).

Pure and pre-render — safe to call before paying for anything. A key stays
`None` when its asset wasn’t supplied. `bed_asset`’s fate is fixed by
`fmt.music_bed` alone: `"none"` never renders a bed, and
[`render_format()`](#braidio.render_format) refuses `bed_asset` there rather than spend on one
that would be dropped, so `bed_applied` is `False` only via that refusal
path, never in a result you got back from a successful render.
`sting_asset`’s fate additionally depends on `script`: a scene break’s
marker can override the format’s default, so a sting can still legitimately
go unused in one script and play in another under the same format — that
case is reported here, not refused.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`bool`](https://docs.python.org/3/builtins/functions.html#bool) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)]

### braidio.describe_defaults()

What is in force, and where each part came from.

Worth printing when a render does not sound the way somebody expected: the
commonest cause is a config file they forgot they wrote.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.estimate_cost(source, , model_id=None)

Estimate ElevenLabs spend for a [`braidio.Script`](#braidio.Script) or a raw string.

Free/local work (segment extraction, weaving) contributes nothing. The returned
[`CostRollup`](#braidio.CostRollup) reports exact characters and an honest dollar sum (a lower
bound when some text is unpriced; see [`usd_per_1k_chars()`](#braidio.usd_per_1k_chars)).

* **Return type:**
  [`CostRollup`](braidio.cost.md#braidio.cost.CostRollup)

```pycon
>>> from braidio import Script, Narration, SegmentBeat
>>> s = Script(title="x", id_slug="01", beats=[
...     Narration(text="a" * 500), SegmentBeat(reference="clip:1")])
>>> estimate_cost(s).characters  # only the narration counts; the clip is free
500
```

### braidio.extract_padded(asset_path, start_s, end_s, out_path, , pre_roll_s=0.4, post_roll_s=0.3, fade_in_s=0.5, fade_out_s=0.8, min_len_s=2.2)

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

### braidio.find_segment(lines, quote, , max_span=12, min_score=0.5, song_end_s=None)

Best contiguous run of timed lines matching `quote`, or `None`.

Scores every run `lines[i..j]` (up to `max_span` lines) by token F1
against the reference’s tokens and returns the highest-scoring run clearing
`min_score`. Handles single-line, sub-line, and multi-line references.

* **Return type:**
  [`Segment`](braidio.sources.md#braidio.sources.Segment) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### braidio.find_verbatim_text(text, forbidden, , min_words=5)

Forbidden lines that appear (near-)verbatim in `text`.

A line counts as leaked if `text` shares a run of `min_words` consecutive
words with it (case-insensitive, word-level). Single words and short common
phrases don’t trip it — only substantial verbatim quoting.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### braidio.format_srt(cues)

Render `cues` as an SRT document.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.group_turns(segments, , min_turn=1, max_turn=1, seed=0)

Group consecutive segments into *turns* of `min_turn..max_turn` segments.

A turn is what one voice speaks before the next takes over. Bigger turns =
each speaker talks longer (fewer switches). Each turn’s segments are joined
into one utterance so prosody is continuous within a speaker. Turn sizes are
seeded-random within the range.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### braidio.layout_starts(kinds, durs, , clip_edge_overlap_s, narration_crossfade_s)

Start offset (s) of each part (all sequential). Thin wrapper over
`layout_placed()` — kept for callers that don’t use placement.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]

### braidio.load_timing(path)

Load a `{lines: [{index,start_s,end_s,text}]}` JSON into 

```
`
```

TimedLine\`s.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`TimedLine`](braidio.sources.md#braidio.sources.TimedLine)]

### braidio.narrate(text, out_path, , api_key=None, voice_id=None, model_id='eleven_v3', voice_settings=None, output_format='mp3_44100_128', refresh=False, return_cache_status=False)

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

### braidio.narration_segments(script)

All narration (default text) of a script, as sentence-level segments.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### braidio.plan_production(script, profile, , publishable_clip_rights=frozenset({'public-domain'}))

Filter `script` into the beats renderable under `profile`.

* **Return type:**
  [`RenderPlan`](braidio.rights.md#braidio.rights.RenderPlan)

### braidio.platitude_rate(text, , per=1000)

Flagged hits per `per` words (default 1000). 0.0 for empty text.

* **Return type:**
  [`float`](https://docs.python.org/3/builtins/functions.html#float)

### braidio.render_format(fmt, script, , source, out_path=None, profile=Profile.PERSONAL, bed_asset=None, sting_asset=None, \*\*overrides)

Render `script` under `fmt`’s defaults; `overrides` win over them.

Wires the format’s `cast` / `narration_voice` / `narration_delivery` /
`weave` / `structure` into [`braidio.render.render_production()`](braidio.render.md#braidio.render.render_production). Any
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
reported (see [`describe_asset_application()`](#braidio.describe_asset_application)).

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### braidio.render_multivoice(segments, pool, , out_path, api_key=None, work_dir='data/tts/multivoice', seed=7, min_turn=2, max_turn=4, avoid_immediate_repeat=True, model_id='eleven_multilingual_v2', base_settings=None, speed_base=1.0, speed_jitter=0.04, crossfade_s=0.1, gap_s=0.0, target_lufs=-16.0)

Render `segments` cycling `pool` → `out_path`.

Segments are first grouped into *turns* of `min_turn..max_turn` segments
(bigger = each voice talks longer). One voice per turn, no immediate repeat,
with a jittered speed even within a speaker. `gap_s` inserts silence
between turns (0 = none). Returns `[(voice, turn_text), …]` for reporting.

`api_key` is an optional per-request ElevenLabs key threaded to every
[`braidio.tts.narrate()`](braidio.tts.md#braidio.tts.narrate) call; `None` (default) keeps the
`$ELEVENLABS_API_KEY` fallback.

#### NOTE
overlapping/interrupting speakers and clip ducking are separate,
upcoming parameters (tracked as issues) — this renders turns sequentially.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Voice`](braidio.multivoice.md#braidio.multivoice.Voice), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

### braidio.render_production(script, , source, api_key=None, config=None, profile=Profile.PERSONAL, rights=None, delivery=None, cast=ConversationCast(roles={'A': 'cgSgspJ2msm6clMCkdW9', 'B': 'iP95p4xoKVk53GoZ742B'}, model_id='eleven_v3', settings={'stability': 0.45}), out_path=None, voice_id=None, crossfade_s=0.12, normalize=True, music_bed=None, structure=None, end_fade_s=0.35, end_silence_s=0.7, return_timeline=False, tts_dir='data/tts', clips_dir='data/clips', episodes_dir='data/episodes')

Render `script` under `profile` → a single audio file. Returns the path.

With `return_timeline=True` returns `(path, TimelineBreakdown)` instead —
the render records what it spent time on (per-beat kind, source interval,
duration, and offset) rather than leaving it to be reconstructed afterward,
plus, in `TimelineBreakdown.settings`, the settings that produced those
timings (see [`braidio.timeline.render_settings()`](braidio.timeline.md#braidio.timeline.render_settings)). Recording them is
what keeps a render reproducible: the pacing knobs below mean one script has
many possible cuts, so a persisted breakdown has to say which one it is.

Segment beats are resolved through `source` (a [`SegmentSource`](#braidio.SegmentSource)).
When `config` has `clip_edge_overlap_s > 0`, a clip is `placement="under"`,
or a `music_bed` is given, the parts are woven on a timeline; otherwise they
are concatenated. `rights` (if given) sets which segment rights are
publishable. `music_bed` lays an instrumental underscore under the whole
production (see [`braidio.music.MusicBed`](braidio.music.md#braidio.music.MusicBed)).

`structure` (a [`braidio.structure.MusicStructure`](braidio.structure.md#braidio.structure.MusicStructure)) is how the
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
see [`braidio.pacing`](braidio.pacing.md#module-braidio.pacing). Those four knobs do **nothing** here at the
default unit, which is the historical behavior kept byte-identical.

`api_key` is an optional per-request ElevenLabs key threaded to every
synthesized beat — both narration ([`braidio.tts.narrate()`](braidio.tts.md#braidio.tts.narrate)) and dialogue
([`braidio.conversation.render_dialogue()`](braidio.conversation.md#braidio.conversation.render_dialogue)). When `None` (default) each
synthesizer falls back to `$ELEVENLABS_API_KEY` (unchanged behavior); an
explicit key lets a caller (e.g. a per-user BYO-key request) override the
environment without mutating it. Segment beats never call ElevenLabs, so the
key does not touch them.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path), [`object`](https://docs.python.org/3/builtins/functions.html#object)]

### braidio.render_settings(, config, crossfade_s, clip_edge_overlap_s, target_lufs, duck_db, delivery=None, profile=None, normalize=True)

The record of *how* a production was rendered, as plain JSON types.

Why it exists: since braidio#63 the pacing knobs are real, so two renders of
one script can differ by minutes — and until this, nothing on disk said which
settings produced which file. This is the cheap, no-graph answer: the
renderer hands it to the [`TimelineBreakdown`](#braidio.TimelineBreakdown) it already returns, so a
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

### braidio.resolve_voice_id(voice_id=None)

Voice id from arg → `VOICE_ENV_VAR` env → default.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.rights_are_publishable(rights, publishable=frozenset({'public-domain'}))

The publishable test at the level of a bare `rights` string.

The graph records rights on nodes, not on `SegmentBeat`s, so the check
has to be askable without a beat in hand — but it must stay the *same*
check. [`segment_is_publishable()`](#braidio.segment_is_publishable) is this function with a beat
unwrapped, never a parallel rule.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### braidio.segment_is_publishable(beat, publishable=frozenset({'public-domain'}))

Whether `beat`’s audio may play in the published cut.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### braidio.skills_dir()

Path to the agent skills that ship with braidio.

They install with the package, so an agent host can be pointed at them
without cloning the repo:

```default
ln -s "$(python -c 'import braidio; print(braidio.skills_dir())')/braidio" \
      ~/.claude/skills/braidio
```

### braidio.split_segments(text)

Split narration into sentence-level segments (markup removed).

Splits on sentence-final `.?!` (not the `…` used for in-thought pacing),
so connected clauses stay with one speaker.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### braidio.strip_speaker_labels(text)

Remove a leading speaker-label prefix (e.g. `"Chris: "`) if present.

Only strips a single leading `Word:` / `Host:` style label so it isn’t
read aloud; leaves colons that are part of the sentence untouched.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.tts_cost_usd(text, , model_id=None)

Estimated USD to synthesize `text`; `0.0` for empty, `None` if unpriced.

Empty text is genuinely free (`0.0`) regardless of the rate; non-empty text
is `None` only when [`usd_per_1k_chars()`](#braidio.usd_per_1k_chars) is unpriced.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]

```pycon
>>> tts_cost_usd("")
0.0
```

### braidio.usd_per_1k_chars(model_id=None)

Resolved USD-per-1000-characters rate (most specific source first).

Resolution: a confirmed per-model rate in `MODEL_USD_PER_1K_CHARS` →
the env override `RATE_ENV_VAR` → `DEFAULT_USD_PER_1K_CHARS`.
Returns `None` (*unpriced*, not free) when the env override is explicitly
disabled (one of `_UNPRICED_SENTINELS`) or is not a finite, non-negative
number — a bad rate must never silently become a dishonest negative/NaN spend.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]

### braidio.user_config()

The user’s persisted defaults, or `{}`.

A missing file is normal. A malformed one warns *once* per path and is then
treated as absent.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### braidio.weave_timeline(items, out_path, , clip_edge_overlap_s=0.5, narration_crossfade_s=0.12, target_lufs=-16.0, true_peak=-1.0, sample_rate=44100, bed=None)

Place items on a timeline and mix. Clips overlap neighbours by
`clip_edge_overlap_s` (their faded edges tuck under narration); narration
parts butt-join with a small crossfade. Returns `out_path`.

`bed` (a [`MusicBed`](braidio.music.md#braidio.music.MusicBed)) lays an instrumental underscore
under the whole production: it’s rendered to cover the timeline, attenuated,
and mixed in posted by `bed.lead_in_s`. Items marked `spotlight` open a
gap in it (the bed is rendered as the regions around them — see
[`braidio.music.bed_regions()`](braidio.music.md#braidio.music.bed_regions)); with none marked the bed is the single
whole-span file. Falls back to a plain concat feel when
`clip_edge_overlap_s == 0` and there’s nothing to overlay.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### Modules

| [`captions`](braidio.captions.md#module-braidio.captions)         | Subtitles for a rendered production, built from the script it was rendered from.                           |
|-------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|
| [`compose`](braidio.compose.md#module-braidio.compose)           | Config-driven narration composition (#20) — the reusable entrypoint.                                       |
| [`conversation`](braidio.conversation.md#module-braidio.conversation) | Conversational register: render an exchange as people *talking to each other*.                             |
| [`cost`](braidio.cost.md#module-braidio.cost)                 | Cost model for braidio's paid operations (ElevenLabs TTS).                                                 |
| [`defaults`](braidio.defaults.md#module-braidio.defaults)         | User-overridable, persisted defaults for how braidio renders a voice.                                      |
| [`delivery`](braidio.delivery.md#module-braidio.delivery)         | Narration *delivery* presets — model + voice settings (issue #10, expressiveness).                         |
| [`formats`](braidio.formats.md#module-braidio.formats)           | Ready-made **format templates** — high-quality presets under standard names.                               |
| [`kinds`](braidio.kinds.md#module-braidio.kinds)               | Production kinds braidio defines.                                                                          |
| [`multivoice`](braidio.multivoice.md#module-braidio.multivoice)     | Multi-voice narration: cycle a pool of voices across segments (issue #10).                                 |
| [`music`](braidio.music.md#module-braidio.music)               | Music bed — an instrumental underscore laid under the whole production, ducked.                            |
| [`pacing`](braidio.pacing.md#module-braidio.pacing)             | Intra-beat narration pacing — how one narration beat becomes spoken *turns*.                               |
| [`relevance`](braidio.relevance.md#module-braidio.relevance)       | How well a still relates to the words spoken over it — the scorer seam.                                    |
| [`render`](braidio.render.md#module-braidio.render)             | Render a [`Script`](braidio.script.md#braidio.script.Script) into an audio file. |
| [`rights`](braidio.rights.md#module-braidio.rights)             | Render profiles: enforce personal-vs-published rights as data.                                             |
| [`script`](braidio.script.md#module-braidio.script)             | Composition model — the ordered beats a render walks.                                                      |
| [`sources`](braidio.sources.md#module-braidio.sources)           | Segment sources: resolve a *reference* to a cuttable `[start, end]` window.                                |
| [`structure`](braidio.structure.md#module-braidio.structure)       | Structural music — stings at scene breaks, fade-to-spotlight on exhibits.                                  |
| [`style`](braidio.style.md#module-braidio.style)               | Style audit — flag the recycled rhetorical tics in commentary text.                                        |
| [`textprep`](braidio.textprep.md#module-braidio.textprep)         | Text preparation for scripts — clean OCR'd source, tidy authored lines.                                    |
| [`timeline`](braidio.timeline.md#module-braidio.timeline)         | Timeline breakdown — what a production spends its time on, and in what order.                              |
| [`tts`](braidio.tts.md#module-braidio.tts)                   | ElevenLabs narration synthesis.                                                                            |
| [`video`](braidio.video.md#module-braidio.video)               | Turn a rendered production into a Ken Burns film over still images.                                        |
| [`weave`](braidio.weave.md#module-braidio.weave)               | Weave narration + audio clips on a timeline (#21) — the reusable mix engine.                               |
| [`weave_config`](braidio.weave_config.md#module-braidio.weave_config) | WeaveConfig — every editing choice for weaving narration + segments (#20).                                 |
