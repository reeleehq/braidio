# braidio.sources

Segment sources: resolve a *reference* to a cuttable `[start, end]` window.

A production weaves in extracted segments of source media. *How* a reference
(a lyric quote, an audiobook passage, a news phrase, an SFX cue) maps to a
concrete `[start, end]` span of a source asset is pluggable via the
[`SegmentSource`](#braidio.sources.SegmentSource) protocol — the weave engine never needs to know it is
lyrics.

This module ships three generic implementations. [`TimedLineSegmentSource`](#braidio.sources.TimedLineSegmentSource),
a token-F1 matcher over time-aligned lines ([`TimedLine`](#braidio.sources.TimedLine)). It answers a
reference by the best contiguous run of lines — handling exact, sub-line, and
multi-line references. Consumers bind it to their own timed lines + asset (e.g.
Hamilton binds LRCLIB line timings + owned song audio). And
[`NamespacedSegmentSource`](#braidio.sources.NamespacedSegmentSource) routes a *prefixed* reference to one of several
sub-sources, which is how a production that cuts between two recordings of the
same work says which recording a given quote should come from. And
[`SpanSource`](#braidio.sources.SpanSource), for a caller that already knows each excerpt’s file and
times: the reference itself is `"<asset>@<start>-<end>"`.

Also provides the lower-level resolver ([`find_segment()`](#braidio.sources.find_segment), [`load_timing()`](#braidio.sources.load_timing))
and the resolve-and-cut convenience ([`cut_quote()`](#braidio.sources.cut_quote)) used directly by the
Hamilton pilot; new code should prefer the [`SegmentSource`](#braidio.sources.SegmentSource) protocol +
[`braidio.weave.extract_padded()`](braidio.weave.html.md#braidio.weave.extract_padded).

### Functions

| [`cut_quote`](#braidio.sources.cut_quote)(audio_path, lines, quote, out_path, \*)   | Resolve `quote` → segment and cut it from `audio_path` (pad + fades).                                       |
|------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| [`find_segment`](#braidio.sources.find_segment)(lines, quote, \*[, max_span, ...])     | Best contiguous run of timed lines matching `quote`, or `None`.                                             |
| [`load_timing`](#braidio.sources.load_timing)(path)                                   | Load a `{lines: [{index,start_s,end_s,text}]}` JSON into <br/><br/>```<br/>`<br/>```<br/><br/>TimedLine\`s. |

### Classes

| [`NamespacedSegmentSource`](#braidio.sources.NamespacedSegmentSource)(sources, \*[, sep, ...])   | A [`SegmentSource`](#braidio.sources.SegmentSource) that routes prefixed references to sub-sources.   |
|-----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| [`ResolvedSegment`](#braidio.sources.ResolvedSegment)(asset_path, start_s, end_s)        | A cuttable span of a source asset — what a [`SegmentSource`](#braidio.sources.SegmentSource) returns. |
| [`Segment`](#braidio.sources.Segment)(start_s, end_s, score, line_start, ...)    | A resolved window for a reference, with the matched line span.                                                     |
| [`SegmentSource`](#braidio.sources.SegmentSource)(\*args, \*\*kwargs)                  | Resolve an opaque `reference` to a [`ResolvedSegment`](#braidio.sources.ResolvedSegment) (or None).     |
| [`SpanSource`](#braidio.sources.SpanSource)(assets)                                 | A [`SegmentSource`](#braidio.sources.SegmentSource) for explicit spans: `"<asset>@<start>-<end>"`.    |
| [`TimedLine`](#braidio.sources.TimedLine)(index, start_s, end_s, text)             | A source line with a `[start_s, end_s)` window (end may be None = tail).                                           |
| [`TimedLineSegmentSource`](#braidio.sources.TimedLineSegmentSource)(\*, lines, asset_path)      | A [`SegmentSource`](#braidio.sources.SegmentSource) over time-aligned lines + one source asset.       |

### *class* braidio.sources.NamespacedSegmentSource(sources, , sep=':', default=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A [`SegmentSource`](#braidio.sources.SegmentSource) that routes prefixed references to sub-sources.

A production that cuts between *several* recordings of the same work — two
takes of one song, a studio master and a live version, an audiobook read in
two languages — hits a wall: `render_production(..., source=...)` takes one
[`SegmentSource`](#braidio.sources.SegmentSource), and a reference like a lyric line matches equally
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
  * **sources** ([`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`SegmentSource`](#braidio.sources.SegmentSource)]) – Namespace → sub-source. Namespaces are matched
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

### *class* braidio.sources.ResolvedSegment(asset_path, start_s, end_s, score=1.0, matched_text='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A cuttable span of a source asset — what a [`SegmentSource`](#braidio.sources.SegmentSource) returns.

### *class* braidio.sources.Segment(start_s, end_s, score, line_start, line_end, matched_text)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A resolved window for a reference, with the matched line span.

### *class* braidio.sources.SegmentSource(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Resolve an opaque `reference` to a [`ResolvedSegment`](#braidio.sources.ResolvedSegment) (or None).

### *class* braidio.sources.SpanSource(assets)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A [`SegmentSource`](#braidio.sources.SegmentSource) for explicit spans: `"<asset>@<start>-<end>"`.

For a caller that already knows which file and which seconds to cut (times
checked beforehand, say) and so needs no matcher:

```default
source = SpanSource({"ep1": "ep1.mp3", "ep2": "ep2.mp3"})
SegmentBeat("ep1@412.5-448.0")
```

The reference is a string, not an id into a mapping, on purpose: references
are persisted in a weave project, and a self-describing one still means the
same cut when the project is reloaded by a process that built its
`SpanSource` differently. Only the asset *name* is looked up.

An unknown asset, or a span that is malformed or empty (`end <= start`,
negative or non-numeric times), resolves to `None` — the caller’s
unresolved-reference handling reports it.

### *class* braidio.sources.TimedLine(index, start_s, end_s, text)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A source line with a `[start_s, end_s)` window (end may be None = tail).

### *class* braidio.sources.TimedLineSegmentSource(, lines, asset_path, song_end_s=None, min_score=0.5)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A [`SegmentSource`](#braidio.sources.SegmentSource) over time-aligned lines + one source asset.

Binds the generic [`find_segment()`](#braidio.sources.find_segment) matcher to a concrete asset so the
weave engine can `resolve(reference) -> ResolvedSegment`.

### braidio.sources.cut_quote(audio_path, lines, quote, out_path, , pad_pre_s=0.15, pad_post_s=0.35, fade_s=0.04, min_score=0.5, song_end_s=None)

Resolve `quote` → segment and cut it from `audio_path` (pad + fades).

Convenience combining [`find_segment()`](#braidio.sources.find_segment) + an ffmpeg cut. Returns the
resolved [`Segment`](#braidio.sources.Segment) (raises `LookupError` if unmatched). New code
should prefer a [`SegmentSource`](#braidio.sources.SegmentSource) + [`braidio.weave.extract_padded()`](braidio.weave.html.md#braidio.weave.extract_padded).

* **Return type:**
  [`Segment`](#braidio.sources.Segment)

### braidio.sources.find_segment(lines, quote, , max_span=12, min_score=0.5, song_end_s=None)

Best contiguous run of timed lines matching `quote`, or `None`.

Scores every run `lines[i..j]` (up to `max_span` lines) by token F1
against the reference’s tokens and returns the highest-scoring run clearing
`min_score`. Handles single-line, sub-line, and multi-line references.

* **Return type:**
  [`Segment`](#braidio.sources.Segment) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### braidio.sources.load_timing(path)

Load a `{lines: [{index,start_s,end_s,text}]}` JSON into 

```
`
```

TimedLine\`s.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`TimedLine`](#braidio.sources.TimedLine)]
