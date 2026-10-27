# braidio.weave_config

WeaveConfig — every editing choice for weaving narration + segments (#20).

One frozen config object that exposes **all** the knobs (casting, turns, pacing,
timing, clip weaving, loudness) so a composer picks combinations rather than
inheriting one hardcoded style (the “enable all choices” mandate of #18/#28).

Defaults come from `docs/research/podcast-audio-weaving-editing.md`. This
module is deliberately **Hamilton-agnostic** — no Genius/LRCLIB/episode imports —
so it can move to the reusable weave package unchanged (#19). It references the
generic voice pools/ids only.

The config is designed to serialize cleanly ([`WeaveConfig.to_dict()`](#braidio.weave_config.WeaveConfig.to_dict)) into a
`render-config` provenance node (#26), so a render is reproducible from its
recorded choices.

### Functions

| [`pick_voice`](#braidio.weave_config.pick_voice)(voices, \*, seed, ordinal)   | Deterministic pool pick for beat `ordinal` (`seed` rotates the pool).                                              |
|------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| [`speaker_key`](#braidio.weave_config.speaker_key)(voice)                      | The identity two parts are compared on for interruption: unset → default.                                          |
| [`turn_overlap_of`](#braidio.weave_config.turn_overlap_of)(config)                 | [`WeaveConfig.turn_overlap()`](#braidio.weave_config.WeaveConfig.turn_overlap) for a serialized config (`to_dict()`). |

### Classes

| [`WeaveConfig`](#braidio.weave_config.WeaveConfig)([voices, pool_label, ...])   | All editing choices for a narration+segment weave.   |
|-------------------------------------------------------------------------------------------|------------------------------------------------------|

### *class* braidio.weave_config.WeaveConfig(voices=('JBFqnCBsd6RMkjVDRZzb', ), pool_label='single', voice_seed=7, avoid_immediate_repeat=True, narration_voice=None, tts=None, model_id='eleven_multilingual_v2', voice_settings=<factory>, segmentation_unit='beat', min_turn=2, max_turn=4, speed_base=1.0, speed_jitter=0.04, crossfade_s=0.12, gap_turn_s=0.0, overlap_turn_s=0.0, overlap_jitter=0.3, overlap_duck_db=0.0, clip_pre_roll_s=0.4, clip_post_roll_s=0.3, clip_fade_in_s=0.5, clip_fade_out_s=0.8, clip_min_len_s=2.2, clip_edge_overlap_s=0.5, duck_db=-15.0, target_lufs=-16.0, true_peak_dbtp=-1.0, sample_rate=44100)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

All editing choices for a narration+segment weave. Frozen + serializable.

#### cast_voice(ordinal, , override=None)

The voice beat `ordinal` speaks with: `override`, else the
production’s `narration_voice`, else the pool pick.

`None` means “the renderer’s default voice”. One definition for both
render paths, so a script is cast (and so interrupts) the same on each.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### *property* casts_voices *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

Whether `voices` is a real casting choice, not the inert default.

The default single voice means “whatever the renderer’s default is” (the
env override included), so it must not pin a voice onto a beat.

#### *classmethod* from_snapshot(snapshot)

Rebuild a config from a serialized one ([`to_dict()`](#braidio.weave_config.WeaveConfig.to_dict)).

A knob the snapshot omits takes the default (one written before the
knob existed); a key that is not a field is ignored.

* **Return type:**
  [`WeaveConfig`](#braidio.weave_config.WeaveConfig)

#### *property* paces_narration *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

Whether a render cuts each narration beat into separately-spoken turns.

`False` (the default, `segmentation_unit="beat"`) is one TTS call per
beat — no intra-beat gaps, no per-turn speed, so `gap_turn_s` and
`speed_jitter` do nothing on the [`braidio.render.render_production()`](braidio.render.html.md#braidio.render.render_production)
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

#### turn_overlap()

The speaker-interruption knobs as the value the weave consumes.

Its `seed` is `voice_seed`: one seed fixes casting and interruptions.

* **Return type:**
  [`TurnOverlap`](braidio.weave.html.md#braidio.weave.TurnOverlap)

#### with_(\*\*changes)

Return a copy with fields overridden (e.g. `cfg.with_(min_turn=1)`).

* **Return type:**
  [`WeaveConfig`](#braidio.weave_config.WeaveConfig)

### braidio.weave_config.pick_voice(voices, , seed, ordinal)

Deterministic pool pick for beat `ordinal` (`seed` rotates the pool).

```pycon
>>> pick_voice(["a", "b", "c"], seed=1, ordinal=0)
'b'
>>> pick_voice([], seed=1, ordinal=0) == DEFAULT_VOICE_ID
True
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.weave_config.speaker_key(voice)

The identity two parts are compared on for interruption: unset → default.

```pycon
>>> speaker_key(None) == speaker_key("") == DEFAULT_VOICE_ID
True
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.weave_config.turn_overlap_of(config)

[`WeaveConfig.turn_overlap()`](#braidio.weave_config.WeaveConfig.turn_overlap) for a serialized config (`to_dict()`).

A knob the mapping omits takes the [`WeaveConfig`](#braidio.weave_config.WeaveConfig) default, so a
snapshot written before the knob existed means “no interruption”.

```pycon
>>> turn_overlap_of({}).is_on
False
>>> turn_overlap_of({"overlap_turn_s": 0.2, "voice_seed": 3}).seed
3
```

* **Return type:**
  [`TurnOverlap`](braidio.weave.html.md#braidio.weave.TurnOverlap)
