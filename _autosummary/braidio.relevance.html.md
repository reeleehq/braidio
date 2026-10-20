# braidio.relevance

How well a still relates to the words spoken over it — the scorer seam.

A picture track used to be placed with no recorded reason and no check, so an
off-topic still could not be explained, caught or tuned (thorwhalen/braidio#85:
a Dylan portrait over a sentence that no longer mentioned Dylan, a Beach Boys
frame under a tag naming a different concert). `video_panels.plan` now scores
every placement against its `anchor_text` through a **scorer**, and this
module is where scorers live.

A scorer has the shape of `illustration`’s reranking `Scorer` —
`(query, items) -> scores` — with the anchor text as the query and still
*bodies* (plain dicts) as the items, one score in `[0, 1]` per still. Batch
by design, so a model-backed scorer (illustration’s SigLIP rerank, its VLM
judge) embeds the text once per span, not once per candidate.

The default, [`lexical_relevance()`](#braidio.relevance.lexical_relevance), needs nothing installed: the share of a
still’s naming words (`subject`, `title`, `key`) that the anchor text
actually says. It is deliberately literal — it cannot see that a studio console
suits “Tom Wilson booked a rhythm section” — so a low score means \*the words do
not name this picture\*, which is the question a disclaimer or a decorative role
has to answer, not a verdict that the picture is bad.

### Examples

```pycon
>>> dylan = {"key": "dylan-1965", "subject": "Bob Dylan, 1965"}
>>> said = "the same day as the sessions for Bob Dylan's Like a Rolling Stone"
>>> round(lexical_relevance(said, [dylan])[0], 2)
0.67
>>> lexical_relevance("overdubbed electric guitar onto the tape", [dylan])
[0.0]
>>> resolve_scorer("lexical") is lexical_relevance
True
```

### Module Attributes

| [`RelevanceScorer`](#braidio.relevance.RelevanceScorer)   | `(anchor_text, still_bodies) -> one score in [0, 1] per still`.   |
|--------------------------------------------------------------------|-------------------------------------------------------------------|
| [`RELEVANCE_SCORERS`](#braidio.relevance.RELEVANCE_SCORERS) | Registered scorers by id.                                         |

### Functions

| [`lexical_relevance`](#braidio.relevance.lexical_relevance)(anchor_text, stills)   | Per still: the best share of a naming field's words that `anchor_text` says.   |
|-------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| [`register_relevance_scorer`](#braidio.relevance.register_relevance_scorer)(name, scorer)  | Make `scorer` addressable by `name` (e.g. an illustration rerank adapter).     |
| [`resolve_scorer`](#braidio.relevance.resolve_scorer)(spec)                     | A scorer from a registered name, a callable, or `None` (the default).          |
| [`scorer_id`](#braidio.relevance.scorer_id)(spec)                          | The id a panel records for `spec`: its registered name, else its qualname.     |

### braidio.relevance.RELEVANCE_SCORERS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Callable](https://docs.python.org/3/library/typing.html#typing.Callable)[[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Sequence](https://docs.python.org/3/library/typing.html#typing.Sequence)[[Mapping](https://docs.python.org/3/library/typing.html#typing.Mapping)]], [Sequence](https://docs.python.org/3/library/typing.html#typing.Sequence)[[float](https://docs.python.org/3/builtins/functions.html#float)]]]* *= {'lexical': <function lexical_relevance>}*

Registered scorers by id. The id is what a panel records in `scorer`.

### braidio.relevance.RelevanceScorer

`(anchor_text, still_bodies) -> one score in [0, 1] per still`.

alias of `Callable`[[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[[`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)]], [`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]]

### braidio.relevance.lexical_relevance(anchor_text, stills)

Per still: the best share of a naming field’s words that `anchor_text` says.

The maximum over `NAMING_FIELDS`, so a long Commons title does not
dilute a short, exact subject.

```pycon
>>> beach = {"key": "central-park-decay",
...          "subject": "The Beach Boys in Central Park, 1971",
...          "title": "Beach Boys Good Vibrations from Central Park 1971"}
>>> lexical_relevance("half a million people all getting the joke", [beach])
[0.0]
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]

### braidio.relevance.register_relevance_scorer(name, scorer)

Make `scorer` addressable by `name` (e.g. an illustration rerank adapter).

```pycon
>>> register_relevance_scorer("lexical", lexical_relevance)  # idempotent
```

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### braidio.relevance.resolve_scorer(spec)

A scorer from a registered name, a callable, or `None` (the default).

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[[`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)]], [`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]]

### braidio.relevance.scorer_id(spec)

The id a panel records for `spec`: its registered name, else its qualname.

```pycon
>>> scorer_id(None), scorer_id(lexical_relevance)
('lexical', 'lexical')
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
