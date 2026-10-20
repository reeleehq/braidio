# braidio.pacing

Intra-beat narration pacing — how one narration beat becomes spoken *turns*.

A narration beat used to be exactly one TTS call: one prosodic arc, one speed,
and no silence anywhere inside it. That is the mechanical read — a human varies
tempo within a paragraph and pauses \*proportionally to how strongly a boundary
closes\* (a paragraph break is not a comma).

This module is the pure, deterministic planner for that. It takes the beat’s
text and the pacing knobs off a [`WeaveConfig`](braidio.weave_config.html.md#braidio.weave_config.WeaveConfig) and
returns a list of [`NarrationTurn`](#braidio.pacing.NarrationTurn) — what to synthesize, how fast, and how
much silence to leave after it. No audio, no API, no I/O: the renderer
([`braidio.render.render_production()`](braidio.render.html.md#braidio.render.render_production)) executes the plan.

Two research findings drive the design
(`misc/docs/research/notebooklm-and-conversational-pacing.md`):

- **Boundary-proportional pauses.** Punctuation is the engine’s pause
  instruction, and a pause is only human when the boundary that earns it is
  strong. [`BOUNDARIES`](#braidio.pacing.BOUNDARIES) is that table.
- **Final lengthening.** The robotic signature is *pause without lengthening* —
  a clipped last word followed by dead air. So each boundary carries a
  `speed_scale` as well as a `gap_scale`, and they stay correlated.

The `unit="beat"` plan is empty by construction: that is the historical
one-call-per-beat behavior, and it is the default, so nothing paces unless a
`WeaveConfig` asks for it.

```pycon
>>> turns = plan_turns("One. Two. Three.", unit="sentence", min_turn=1, max_turn=1)
>>> [t.text for t in turns]
['One.', 'Two.', 'Three.']
>>> plan_turns("One. Two.", unit="beat")
[]
```

### Module Attributes

| [`SEGMENTATION_UNITS`](#braidio.pacing.SEGMENTATION_UNITS)   | How a beat's text is cut into the units a turn is built from.                                                                               |
|-----------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|
| [`SPEED_RANGE`](#braidio.pacing.SPEED_RANGE)          | ElevenLabs clamps `voice_settings.speed` to this range in practice; a jittered speed outside it is silently useless, so the planner clamps. |
| [`BOUNDARIES`](#braidio.pacing.BOUNDARIES)           | The boundary table.                                                                                                                         |

### Functions

| [`classify_boundary`](#braidio.pacing.classify_boundary)(text)                     | Name the boundary implied by `text`'s trailing punctuation.           |
|----------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| [`plan_turns`](#braidio.pacing.plan_turns)(text, \*[, unit, min_turn, ...]) | Plan how to speak one narration beat.                                 |
| [`planned_gap_total_s`](#braidio.pacing.planned_gap_total_s)(turns)                  | Total silence a plan inserts inside the beat (for reporting / tests). |
| [`split_paragraphs`](#braidio.pacing.split_paragraphs)(text)                      | Split `text` on blank lines, collapsing runs of spaces within each.   |
| [`split_units`](#braidio.pacing.split_units)(text, \*[, unit])               | Cut `text` into `unit`-sized pieces, grouped by paragraph.            |

### Classes

| [`Boundary`](#braidio.pacing.Boundary)(name, gap_scale, speed_scale)         | How strongly a unit's trailing punctuation closes.            |
|-------------------------------------------------------------------------------------------------|---------------------------------------------------------------|
| [`NarrationTurn`](#braidio.pacing.NarrationTurn)(text[, gap_after_s, speed, ...]) | One synthesizable chunk of a narration beat, with its pacing. |

### braidio.pacing.BOUNDARIES *: [Mapping](https://docs.python.org/3/library/typing.html#typing.Mapping)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Boundary](#braidio.pacing.Boundary)]* *= {'clause': Boundary(name='clause', gap_scale=0.5, speed_scale=0.99), 'ellipsis': Boundary(name='ellipsis', gap_scale=1.4, speed_scale=0.97), 'flowing': Boundary(name='flowing', gap_scale=0.35, speed_scale=1.0), 'none': Boundary(name='none', gap_scale=0.25, speed_scale=1.0), 'paragraph': Boundary(name='paragraph', gap_scale=2.2, speed_scale=0.96), 'sentence': Boundary(name='sentence', gap_scale=1.0, speed_scale=0.98)}*

The boundary table. Scales are relative to `"sentence"` (== the configured
`gap_turn_s`), from the punctuation→break-strength table in
`misc/docs/research/notebooklm-and-conversational-pacing.md`.

### *class* braidio.pacing.Boundary(name, gap_scale, speed_scale)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

How strongly a unit’s trailing punctuation closes.

`gap_scale` multiplies the base gap (`WeaveConfig.gap_turn_s`) and
`speed_scale` multiplies the turn’s speed — *final lengthening*, the thing
that keeps a pause from sounding like a dropout. The two are deliberately
correlated: a stronger boundary gets both a longer silence and a slower
approach to it.

### *class* braidio.pacing.NarrationTurn(text, gap_after_s=0.0, speed=None, boundary='sentence')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One synthesizable chunk of a narration beat, with its pacing.

`speed` is `None` when the delivery’s model has no speed control (eleven
v3) — the renderer then leaves `voice_settings["speed"]` alone rather than
sending a parameter the model is documented not to honor.

### braidio.pacing.SEGMENTATION_UNITS *= ('beat', 'paragraph', 'sentence', 'clause')*

How a beat’s text is cut into the units a turn is built from. `"beat"`
means “don’t cut at all” — one TTS call per beat, the historical behavior.

### braidio.pacing.SPEED_RANGE *= (0.7, 1.2)*

ElevenLabs clamps `voice_settings.speed` to this range in practice; a
jittered speed outside it is silently useless, so the planner clamps.

### braidio.pacing.classify_boundary(text)

Name the boundary implied by `text`’s trailing punctuation.

```pycon
>>> classify_boundary("So that's the charge sheet.")
'sentence'
>>> classify_boundary('He said "no," and left,')
'clause'
>>> classify_boundary("and then —")
'flowing'
>>> classify_boundary("well…")
'ellipsis'
>>> classify_boundary("no punctuation here")
'none'
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.pacing.plan_turns(text, , unit='sentence', min_turn=1, max_turn=3, seed=7, gap_s=0.0, speed_base=1.0, speed_jitter=0.0, gap_scale_cap_s=3.0)

Plan how to speak one narration beat. Pure and deterministic in `seed`.

Returns `[]` for `unit="beat"` (and for empty text) — the caller’s signal
to keep the historical single-call path, byte for byte. Otherwise each
returned [`NarrationTurn`](#braidio.pacing.NarrationTurn) is one TTS call, with the silence to append
after it and the speed to read it at.

`gap_s` is the *sentence-boundary* gap; every other boundary scales off it
via [`BOUNDARIES`](#braidio.pacing.BOUNDARIES), capped at `gap_scale_cap_s` (long silences read as
a dropout, and the engines are unstable past ~3 s). The final turn of the
beat never gets a trailing gap — the beat boundary belongs to the renderer’s
crossfade and to the next beat’s `Narration.lead_gap_s`.

`speed_base=None` plans no speed at all (eleven v3, which has no speed
control); a float centers a per-turn jitter of `±speed_jitter` on it,
scaled by the boundary’s final lengthening and clamped to [`SPEED_RANGE`](#braidio.pacing.SPEED_RANGE).

```pycon
>>> turns = plan_turns("One. Two. Three. Four.", min_turn=2, max_turn=2, gap_s=0.3)
>>> [(t.text, t.gap_after_s) for t in turns]
[('One. Two.', 0.3), ('Three. Four.', 0.0)]
>>> plan_turns("A.\n\nB.", gap_s=0.2)[0].gap_after_s  # paragraph > sentence
0.44
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`NarrationTurn`](#braidio.pacing.NarrationTurn)]

### braidio.pacing.planned_gap_total_s(turns)

Total silence a plan inserts inside the beat (for reporting / tests).

```pycon
>>> planned_gap_total_s(plan_turns("A. B. C.", min_turn=1, max_turn=1, gap_s=0.3))
0.6
```

* **Return type:**
  [`float`](https://docs.python.org/3/builtins/functions.html#float)

### braidio.pacing.split_paragraphs(text)

Split `text` on blank lines, collapsing runs of spaces within each.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### braidio.pacing.split_units(text, , unit='sentence')

Cut `text` into `unit`-sized pieces, grouped by paragraph.

Returns a list of paragraphs, each a list of units, so a caller can keep
turns from straddling a paragraph break (which would swallow the strongest
pause in the beat). `unit="beat"` returns one paragraph of one unit.

```pycon
>>> split_units("A one. A two.\n\nB one.", unit="sentence")
[['A one.', 'A two.'], ['B one.']]
>>> split_units("Yes, really. No.", unit="clause")
[['Yes,', 'really.', 'No.']]
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]
