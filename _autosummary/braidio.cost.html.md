# braidio.cost

Cost model for braidio’s paid operations (ElevenLabs TTS).

braidio’s only spend is ElevenLabs synthesis — [`braidio.narrate()`](braidio.html.md#braidio.narrate) /
`braidio.text_to_dialogue()` and the renderers built on them — billed by
ElevenLabs **per character** of submitted text. Everything else (ffmpeg
extraction, weaving, loudness normalization) is local and free.

This module turns text into a USD figure so renders can attribute a cost onto
`lacing.Artifact.cost_usd` and a free [`estimate_cost()`](#braidio.cost.estimate_cost) can preview a whole
[`braidio.Script`](braidio.html.md#braidio.Script) before any synthesis is paid for.

**The figure is an ESTIMATE**, priced at a configurable per-1000-character rate
(env [`RATE_ENV_VAR`](#braidio.cost.RATE_ENV_VAR), the per-model [`MODEL_USD_PER_1K_CHARS`](#braidio.cost.MODEL_USD_PER_1K_CHARS) table, or
[`DEFAULT_USD_PER_1K_CHARS`](#braidio.cost.DEFAULT_USD_PER_1K_CHARS)). It equals what ElevenLabs bills for a *live*
synthesis at that rate — not the provider’s exact invoice. braidio does not yet
see the lower-level `mixing` on-disk cache, so a render served from that cache
still reports its rate estimate rather than `$0` (a safe over-estimate for a
spend ledger; see thorwhalen/braidio#8 to make it exact).

This differs from `falaw` (which has no default rate and returns `None`
whenever a real price is unknown): braidio deliberately supplies a *default* so a
ledger has a number out of the box — while still returning `None` (*unpriced*,
never a fake `0.0` for real text) when the rate is explicitly disabled or
invalid. Characters are always counted exactly, so a dollar figure can be
recomputed if the rate changes.

### Module Attributes

| [`RATE_ENV_VAR`](#braidio.cost.RATE_ENV_VAR)             | Env var overriding the USD-per-1000-characters rate (global default for models not in [`MODEL_USD_PER_1K_CHARS`](#braidio.cost.MODEL_USD_PER_1K_CHARS)).   |
|---------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`DEFAULT_USD_PER_1K_CHARS`](#braidio.cost.DEFAULT_USD_PER_1K_CHARS) | Default USD per 1000 characters when nothing else is configured.                                                                                                  |
| [`MODEL_USD_PER_1K_CHARS`](#braidio.cost.MODEL_USD_PER_1K_CHARS)   | Confirmed per-model rate overrides (USD per 1000 chars).                                                                                                          |

### Functions

| [`backend_cost_usd`](#braidio.cost.backend_cost_usd)(text, \*, tts[, model_id])   | Estimated USD for `text` on the voice backend that speaks it.                                                                   |
|------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------|
| [`billable_chars`](#braidio.cost.billable_chars)(text)                          | Characters ElevenLabs bills for `text` (the whole submitted string).                                                            |
| [`estimate_cost`](#braidio.cost.estimate_cost)(source, \*[, model_id])         | Estimate ElevenLabs spend for a [`braidio.Script`](braidio.html.md#braidio.Script) or a raw string. |
| [`tts_cost_usd`](#braidio.cost.tts_cost_usd)(text, \*[, model_id])            | Estimated USD to synthesize `text`; `0.0` for empty, `None` if unpriced.                                                        |
| [`usd_per_1k_chars`](#braidio.cost.usd_per_1k_chars)([model_id])                  | Resolved USD-per-1000-characters rate (most specific source first).                                                             |

### Classes

| [`CostLine`](#braidio.cost.CostLine)(label, kind, characters, usd, model_id)   | One billable line of an estimate (a narration beat or a dialogue beat).     |
|-----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| [`CostRollup`](#braidio.cost.CostRollup)(characters, usd, unpriced[, lines])     | A production's estimated TTS spend, exact on characters, honest on dollars. |

### *class* braidio.cost.CostLine(label, kind, characters, usd, model_id)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One billable line of an estimate (a narration beat or a dialogue beat).

Named to echo `falaw.CostLine` (a line within a rollup) rather than
`falaw.CostEstimate` (which means a per-call price spec) — so the vocabulary
is consistent across the federation.

### *class* braidio.cost.CostRollup(characters, usd, unpriced, lines=())

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A production’s estimated TTS spend, exact on characters, honest on dollars.

`usd` is the sum of the *priced* lines; `unpriced` is `True` when some
billable text had no configured rate (so `usd` is a lower bound). No billable
lines (e.g. an all-segment script) gives `characters=0, usd=0.0,
unpriced=False`. Named `CostRollup` to match `falaw.CostRollup`.

#### *property* summary *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

One-line human summary (handy for a CLI/MCP preview).

### braidio.cost.DEFAULT_USD_PER_1K_CHARS *: [float](https://docs.python.org/3/builtins/functions.html#float)* *= 0.3*

Default USD per 1000 characters when nothing else is configured. Approximate:
ElevenLabs bills per credit and the $/credit depends on your plan, so set
[`RATE_ENV_VAR`](#braidio.cost.RATE_ENV_VAR) to your plan’s real rate. Kept slightly conservative so a
budget over- rather than under-estimates spend.

### braidio.cost.MODEL_USD_PER_1K_CHARS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [float](https://docs.python.org/3/builtins/functions.html#float)]* *= {}*

Confirmed per-model rate overrides (USD per 1000 chars). A model listed here
is priced at its own rate — winning over the env override and the default —
because a confirmed price is more accurate than a global guess. Empty until
ElevenLabs per-model rates are confirmed (e.g. `eleven_v3` dialogue vs
`eleven_multilingual_v2`).

### braidio.cost.RATE_ENV_VAR *= 'BRAIDIO_TTS_USD_PER_1K_CHARS'*

Env var overriding the USD-per-1000-characters rate (global default for models
not in [`MODEL_USD_PER_1K_CHARS`](#braidio.cost.MODEL_USD_PER_1K_CHARS)). Set it to your ElevenLabs plan’s real
rate for exact ledgers; set it to `none` to mark spend as *unpriced*.

### braidio.cost.backend_cost_usd(text, , tts, model_id=None)

Estimated USD for `text` on the voice backend that speaks it.

`tts=None` is ElevenLabs ([`tts_cost_usd()`](#braidio.cost.tts_cost_usd)); a registered backend
([`braidio.tts.register_tts()`](braidio.tts.html.md#braidio.tts.register_tts)) prices at its own rate. A name that is
not registered (a runner serving cached renders need not register it)
is unpriced, `None`, never an error and never `0.0`.

```pycon
>>> backend_cost_usd("", tts="anything")
0.0
>>> backend_cost_usd("hi", tts="not-registered") is None
True
```

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]

### braidio.cost.billable_chars(text)

Characters ElevenLabs bills for `text` (the whole submitted string).

ElevenLabs charges for everything sent — including `eleven_v3` audio tags
like `[excited]` — so this is just `len(text)`. A named function keeps the
billing definition in one place if it ever needs to change.

```pycon
>>> billable_chars("hello")
5
>>> billable_chars(None)
0
```

* **Return type:**
  [`int`](https://docs.python.org/3/builtins/functions.html#int)

### braidio.cost.estimate_cost(source, , model_id=None)

Estimate ElevenLabs spend for a [`braidio.Script`](braidio.html.md#braidio.Script) or a raw string.

Free/local work (segment extraction, weaving) contributes nothing. The returned
[`CostRollup`](#braidio.cost.CostRollup) reports exact characters and an honest dollar sum (a lower
bound when some text is unpriced; see [`usd_per_1k_chars()`](#braidio.cost.usd_per_1k_chars)).

```pycon
>>> from braidio import Script, Narration, SegmentBeat
>>> s = Script(title="x", id_slug="01", beats=[
...     Narration(text="a" * 500), SegmentBeat(reference="clip:1")])
>>> estimate_cost(s).characters  # only the narration counts; the clip is free
500
```

* **Return type:**
  [`CostRollup`](#braidio.cost.CostRollup)

### braidio.cost.tts_cost_usd(text, , model_id=None)

Estimated USD to synthesize `text`; `0.0` for empty, `None` if unpriced.

Empty text is genuinely free (`0.0`) regardless of the rate; non-empty text
is `None` only when [`usd_per_1k_chars()`](#braidio.cost.usd_per_1k_chars) is unpriced.

```pycon
>>> tts_cost_usd("")
0.0
```

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]

### braidio.cost.usd_per_1k_chars(model_id=None)

Resolved USD-per-1000-characters rate (most specific source first).

Resolution: a confirmed per-model rate in [`MODEL_USD_PER_1K_CHARS`](#braidio.cost.MODEL_USD_PER_1K_CHARS) →
the env override [`RATE_ENV_VAR`](#braidio.cost.RATE_ENV_VAR) → [`DEFAULT_USD_PER_1K_CHARS`](#braidio.cost.DEFAULT_USD_PER_1K_CHARS).
Returns `None` (*unpriced*, not free) when the env override is explicitly
disabled (one of `_UNPRICED_SENTINELS`) or is not a finite, non-negative
number — a bad rate must never silently become a dishonest negative/NaN spend.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]
