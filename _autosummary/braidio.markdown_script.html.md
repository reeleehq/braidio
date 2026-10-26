# braidio.markdown_script

Markdown → narration beats, for reading a written document aloud.

`ingest_document` returns plain text and no structure; reading an essay aloud
needs the structure (a title beat, longer pauses at headings, footnotes read at the
end) and the *speakable* text (no URLs, no emphasis markers, no image syntax). This
module owns that conversion so a read-aloud is one call:

```pycon
>>> script = script_from_markdown("---\ntitle: On Pace\n---\n# Part one\n\nSee [this](http://x.y) **now**.")
>>> [(b.style, b.text) for b in script.beats]
[('title', 'On Pace.'), ('heading', 'Part one'), ('para', 'See this now.')]
```

Parsing is delegated to `markdown-it-py` (CommonMark, plus the footnote and
front-matter plugins of `mdit-py-plugins`), an optional dependency
(`pip install 'braidio[markdown]'`) imported only when a document is converted,
so `import braidio` never needs it. This module only maps the token stream to
beats.

Tagging (`[pause]`, `[dryly]`) stays the author’s job: the beats are plain
text an agent can tag, and [`untagged_differences()`](#braidio.markdown_script.untagged_differences) verifies that tagging
changed nothing but tags. The conversion is pure — no network, no TTS, no cost.

### Module Attributes

| [`DEFAULT_GAPS`](#braidio.markdown_script.DEFAULT_GAPS)             | Seconds of silence before each kind of beat (`Narration.lead_gap_s`).         |
|---------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| [`DEFAULT_FOOTNOTE_LEAD_IN`](#braidio.markdown_script.DEFAULT_FOOTNOTE_LEAD_IN) | Spoken before each footnote (they are read at the end, out of their context). |
| [`MARKDOWN_EXTRA`](#braidio.markdown_script.MARKDOWN_EXTRA)           | The pip extra that carries the parser.                                        |

### Functions

| [`script_from_markdown`](#braidio.markdown_script.script_from_markdown)(md, \*[, voice, title, ...])   | Turn a markdown document into a `Script` of narration beats.     |
|------------------------------------------------------------------------------------------------------|------------------------------------------------------------------|
| [`untagged_differences`](#braidio.markdown_script.untagged_differences)(before, after)                 | Where `after` differs from `before` by more than `[audio tags]`. |

### Classes

| [`TagMismatch`](#braidio.markdown_script.TagMismatch)(index, before, after)   | A beat whose text changed by more than audio tags.   |
|--------------------------------------------------------------------------------------|------------------------------------------------------|

### braidio.markdown_script.DEFAULT_FOOTNOTE_LEAD_IN *= 'A footnote.'*

Spoken before each footnote (they are read at the end, out of their context).

### braidio.markdown_script.DEFAULT_GAPS *: [Mapping](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [float](https://docs.python.org/3/builtins/functions.html#float)]* *= mappingproxy({'title': 0.0, 'heading': 1.3, 'para': 0.35, 'list_item': 0.2, 'quote': 0.5, 'footnote': 1.0})*

Seconds of silence before each kind of beat (`Narration.lead_gap_s`). Headings
breathe; the title starts the file; footnotes are set apart from the body.
Read-only: a partial `gaps=` is merged over it, never mutates it.

### braidio.markdown_script.MARKDOWN_EXTRA *= 'braidio[markdown]'*

The pip extra that carries the parser.

### *class* braidio.markdown_script.TagMismatch(index, before, after)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A beat whose text changed by more than audio tags.

`index` is the narration beat’s position; `-1` means the non-narration
beats (segments, dialogue, scene breaks) differ, with their counts as
`before`/`after`.

### braidio.markdown_script.script_from_markdown(md, , voice=None, title=None, id_slug=None, gaps=mappingproxy({'title': 0.0, 'heading': 1.3, 'para': 0.35, 'list_item': 0.2, 'quote': 0.5, 'footnote': 1.0}), respell=None, footnotes='end', footnote_lead_in='A footnote.', images='drop')

Turn a markdown document into a `Script` of narration beats.

One beat per paragraph, heading, list item or quote paragraph.
`Narration.style` carries the kind (`title`/`heading`/`para`/
`list_item`/`quote`/`footnote`), so the timeline reports headings and
footnotes separately, and `lead_gap_s` comes from `gaps[kind]` (a partial
`gaps` is merged over [`DEFAULT_GAPS`](#braidio.markdown_script.DEFAULT_GAPS)).

- Front matter `title`/`subtitle` become a title beat (`title=` and the
  first heading, as plain text, are the fallbacks for the script’s name).
- `images`: `"drop"` (default) or `"caption"` (read the alt text inline).
- `footnotes`: `"end"` (default; each read after the body, in reference
  order, behind `footnote_lead_in`) or `"drop"`. Markers are always removed.
- `respell` maps text as written to text as spoken (`{"BDUF": "B-DUF"}`),
  applied to the beats only; the source markdown is untouched.
- Links keep their text; URLs are never read (autolinks and bare URLs are
  dropped); emphasis and code markers are stripped; HTML, code blocks, tables
  and rules are not read; entities are decoded.

Needs `markdown-it-py` + `mdit-py-plugins` (`braidio[markdown]`).

* **Return type:**
  [`Script`](braidio.script.html.md#braidio.script.Script)

### braidio.markdown_script.untagged_differences(before, after)

Where `after` differs from `before` by more than `[audio tags]`.

Compares narration beat by beat (whitespace-insensitive, added tags ignored;
a bracket token already in `before` must survive). A different narration
count is reported as one mismatch at the first unmatched index; a different
count of non-narration beats as one mismatch at index `-1`. Empty list ⇒
tagging changed nothing but tags.

```pycon
>>> a = Script("t", "t", [Narration("Hello there.")])
>>> b = Script("t", "t", [Narration("[warmly] Hello there.")])
>>> untagged_differences(a, b)
[]
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`TagMismatch`](#braidio.markdown_script.TagMismatch)]
