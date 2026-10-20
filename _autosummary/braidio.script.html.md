# braidio.script

Composition model — the ordered beats a render walks.

A [`Script`](#braidio.script.Script) is an ordered list of beats: a [`Narration`](#braidio.script.Narration) (spoken,
synthesized), a [`Dialogue`](#braidio.script.Dialogue) (a multi-voice exchange), a [`SegmentBeat`](#braidio.script.SegmentBeat)
(a *reference* to a span of source media to resolve and weave in), or a
[`SceneBreak`](#braidio.script.SceneBreak) (a structural boundary — “new section” — that the renderer
marks with music, see [`braidio.structure`](braidio.structure.html.md#module-braidio.structure)). This is the generic,
media-agnostic projection a renderer consumes; how a reference maps to audio is a
[`braidio.sources.SegmentSource`](braidio.sources.html.md#braidio.sources.SegmentSource) concern, and what the beats are backed by
(lyrics, a graph, hand-authoring) is the consumer’s concern.

### Functions

| [`narration_segments`](#braidio.script.narration_segments)(script)   | All narration (default text) of a script, as sentence-level segments.   |
|-------------------------------------------------------------------------------|-------------------------------------------------------------------------|

### Classes

| [`Dialogue`](#braidio.script.Dialogue)(turns[, label])                      | A multi-speaker commentary exchange (the conversational register).      |
|------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| [`Narration`](#braidio.script.Narration)(text[, style, published_text, ...]) | A spoken narration beat (authored, synthesized by TTS).                 |
| [`SceneBreak`](#braidio.script.SceneBreak)([label, marker])                   | A structural boundary between sections — the "new scene" beat.          |
| [`Script`](#braidio.script.Script)(title, id_slug[, beats])               | An ordered production script.                                           |
| [`SegmentBeat`](#braidio.script.SegmentBeat)(reference[, label, rights, ...])  | A span of source media to weave in, addressed by an opaque `reference`. |

### *class* braidio.script.Dialogue(turns, label='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A multi-speaker commentary exchange (the conversational register).

`turns` is an ordered tuple of `(role, text)` pairs (roles like
`"A"`/`"B"` map to voices via a [`ConversationCast`](braidio.conversation.html.md#braidio.conversation.ConversationCast)
at render time). Rendered in ONE pass via Text-to-Dialogue so it sounds like
people talking to each other. This is our own commentary → always publishable
(its text is still scanned for forbidden verbatim quotes in the published cut).

### *class* braidio.script.Narration(text, style=None, published_text=None, lead_gap_s=0.0, voice=None, voice_settings=None)

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

### *class* braidio.script.SceneBreak(label='', marker=None)

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

### *class* braidio.script.Script(title, id_slug, beats=list())

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

An ordered production script.

### *class* braidio.script.SegmentBeat(reference, label='', rights='owned-local', published_substitute=None, placement='before', spotlight=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A span of source media to weave in, addressed by an opaque `reference`.

The renderer resolves `reference` → `[start,end)` via a
[`SegmentSource`](braidio.sources.html.md#braidio.sources.SegmentSource) and cuts it. `rights` (e.g.
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

### braidio.script.narration_segments(script)

All narration (default text) of a script, as sentence-level segments.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]
