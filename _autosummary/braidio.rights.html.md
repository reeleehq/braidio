# braidio.rights

Render profiles: enforce personal-vs-published rights as data.

Rights are encoded on the beats (`SegmentBeat.rights`, and — mechanically —
the presence of forbidden verbatim text in narration) and a **render profile**
filters the script into the beats that may actually render:

- `PERSONAL`  — include everything, including owned/copyrighted segment audio.
- `PUBLISHED` — exclude any non-publishable segment audio and any beat
  carrying forbidden verbatim text; keep original narration, publishable
  segments, and short transformative substitutes.

The rule is enforced *mechanically*: [`plan_production()`](#braidio.rights.plan_production) routes beats by
their `rights` flag, and [`find_verbatim_text()`](#braidio.rights.find_verbatim_text) / [`content_violations()`](#braidio.rights.content_violations)
scan the resulting published beats against a **caller-supplied** set of
forbidden texts (via [`RightsPolicy`](#braidio.rights.RightsPolicy)) — so a leak is a test failure, not a
judgment call. braidio owns the scanner + profile filter; the consumer injects
*what* is forbidden (e.g. Hamilton injects the song’s lyric lines).

Not legal advice.

### Module Attributes

| [`DEFAULT_PROFILE`](#braidio.rights.DEFAULT_PROFILE)   | The profile a render uses when the caller declares none — the permissive one, so a production that never mentions rights renders everything it was given.   |
|--------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|

### Functions

| [`clip_plays_under`](#braidio.rights.clip_plays_under)(profile, rights[, publishable])   | Whether a segment with `rights` plays as audio under `profile`.   |
|-----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------|
| [`content_violations`](#braidio.rights.content_violations)(plan, forbidden, \*[, ...])     | Rights violations in a *published* plan (empty list = clean).     |
| [`find_verbatim_text`](#braidio.rights.find_verbatim_text)(text, forbidden, \*[, ...])     | Forbidden lines that appear (near-)verbatim in `text`.            |
| [`plan_production`](#braidio.rights.plan_production)(script, profile, \*[, ...])        | Filter `script` into the beats renderable under `profile`.        |
| [`rights_are_publishable`](#braidio.rights.rights_are_publishable)(rights[, publishable])      | The publishable test at the level of a bare `rights` string.      |
| [`segment_is_publishable`](#braidio.rights.segment_is_publishable)(beat[, publishable])        | Whether `beat`'s audio may play in the published cut.             |

### Classes

| [`PlannedBeat`](#braidio.rights.PlannedBeat)(kind, content, from_index[, ...])   | A beat resolved for a profile — what the renderer actually plays.   |
|--------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|
| [`Profile`](#braidio.rights.Profile)(\*values)                               | Which projection of the production we render.                       |
| [`RenderPlan`](#braidio.rights.RenderPlan)(profile[, beats, dropped, ...])      |                                                                     |
| [`RightsPolicy`](#braidio.rights.RightsPolicy)([forbidden_texts, ...])            | Injected rights configuration for the published profile.            |

### Exceptions

| [`RightsViolation`](#braidio.rights.RightsViolation)   | A render would play source audio the profile it claims forbids.   |
|--------------------------------------------------------------------|-------------------------------------------------------------------|

### braidio.rights.DEFAULT_PROFILE *: [Profile](#braidio.rights.Profile)* *= Profile.PERSONAL*

The profile a render uses when the caller declares none — the permissive
one, so a production that never mentions rights renders everything it was
given. **Every** entry point resolves its default from here (the no-graph
fast path [`braidio.render.render_production()`](braidio.render.html.md#braidio.render.render_production) /
[`braidio.formats.render_format()`](braidio.formats.html.md#braidio.formats.render_format), the graph path
`braidio.transforms.weave_project()`, and the MCP tools), so the two
render paths cannot drift into disagreeing about what “unspecified” means.

### *class* braidio.rights.PlannedBeat(kind, content, from_index, note='', turns=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A beat resolved for a profile — what the renderer actually plays.

`kind` is `"narration"` (synthesize `content`), `"clip"` (resolve
`content` as a segment reference and cut audio), `"dialogue"` (synthesize
`turns`) or `"scene_break"` (a structural boundary — no content; the
renderer marks it with music). `from_index` points at the source beat;
`note` records any substitution/drop reasoning.

### *class* braidio.rights.Profile(\*values)

Bases: [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Enum`](https://docs.python.org/3/library/enum.html#enum.Enum)

Which projection of the production we render.

### *class* braidio.rights.RenderPlan(profile, beats=list(), dropped=list(), substituted=list())

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### *class* braidio.rights.RightsPolicy(forbidden_texts=<function RightsPolicy.<lambda>>, publishable_clip_rights=frozenset({'public-domain'}))

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Injected rights configuration for the published profile.

`forbidden_texts` yields the strings that must not appear verbatim in
published narration (e.g. copyrighted lyric lines). `publishable_clip_rights`
is the set of segment `rights` values allowed in the published cut.

### *exception* braidio.rights.RightsViolation

Bases: [`ValueError`](https://docs.python.org/3/builtins/exceptions.html#ValueError)

A render would play source audio the profile it claims forbids.

Raised where a rights decision is *verified* rather than made — today by the
graph path’s episode transform, which checks the members it is about to
weave against the profile it is about to stamp on them. A `ValueError`
subclass so existing callers keep catching it, typed so a caller that cares
can tell a rights refusal from a malformed input.

### braidio.rights.clip_plays_under(profile, rights, publishable=frozenset({'public-domain'}))

Whether a segment with `rights` plays as audio under `profile`.

The whole clip-routing rule, in one place: `PERSONAL` plays everything,
`PUBLISHED` plays only publishable rights. [`plan_production()`](#braidio.rights.plan_production) asks it
when it filters a script, and the graph’s episode transform re-asks it at
weave time to verify that the members it inherited still match the profile
the production now declares — one rule, asked twice, never copied.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### braidio.rights.content_violations(plan, forbidden, , min_words=5)

Rights violations in a *published* plan (empty list = clean).

Fails if any planned beat plays non-publishable segment audio, or any
narration beat contains forbidden verbatim text.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### braidio.rights.find_verbatim_text(text, forbidden, , min_words=5)

Forbidden lines that appear (near-)verbatim in `text`.

A line counts as leaked if `text` shares a run of `min_words` consecutive
words with it (case-insensitive, word-level). Single words and short common
phrases don’t trip it — only substantial verbatim quoting.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### braidio.rights.plan_production(script, profile, , publishable_clip_rights=frozenset({'public-domain'}))

Filter `script` into the beats renderable under `profile`.

* **Return type:**
  [`RenderPlan`](#braidio.rights.RenderPlan)

### braidio.rights.rights_are_publishable(rights, publishable=frozenset({'public-domain'}))

The publishable test at the level of a bare `rights` string.

The graph records rights on nodes, not on `SegmentBeat`s, so the check
has to be askable without a beat in hand — but it must stay the *same*
check. [`segment_is_publishable()`](#braidio.rights.segment_is_publishable) is this function with a beat
unwrapped, never a parallel rule.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### braidio.rights.segment_is_publishable(beat, publishable=frozenset({'public-domain'}))

Whether `beat`’s audio may play in the published cut.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)
