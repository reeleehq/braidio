# braidio.delivery

Narration *delivery* presets — model + voice settings (issue #10, expressiveness).

A [`Delivery`](#braidio.delivery.Delivery) bundles the ElevenLabs `model_id` and `voice_settings`
that shape how expressive vs flat a narration reads. The renderer takes one so
we can A/B the same script under different deliveries and pick what has the most
relief without editing the script.

Presets here are a starting point tuned from `docs/research/expressive-tts-
narration.md`; refine as we learn what sounds best. The key monotony levers:
lower `stability` and raise `style` add variation (at some cost in
consistency); `eleven_v3` adds inline audio tags for real expressive control.

### Module Attributes

| [`SPEEDLESS_MODELS`](#braidio.delivery.SPEEDLESS_MODELS)   | Models that ignore `voice_settings["speed"]` (eleven v3 and v4 have no speed control: "Style and Speed sliders are not available" on v4).   |
|---------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|

### Functions

| [`as_delivery`](#braidio.delivery.as_delivery)(delivery)          | `delivery` as a [`Delivery`](#braidio.delivery.Delivery): one passes through, a name is looked up in `DELIVERIES`.   |
|---------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------|
| [`model_supports_speed`](#braidio.delivery.model_supports_speed)(model_id) | Whether `model_id` honors `voice_settings["speed"]`.                                                                                  |

### Classes

| [`Delivery`](#braidio.delivery.Delivery)(name, model_id[, voice_settings, ...])   | A named narration delivery: which model + voice settings to synthesize with.   |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|

### *class* braidio.delivery.Delivery(name, model_id, voice_settings=dict(), supports_audio_tags=False, supports_speed=True, note='', language_code=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A named narration delivery: which model + voice settings to synthesize with.

#### language_code *: [str](https://docs.python.org/3/builtins/stdtypes.html#str) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

ISO 639-1 language the model is told to read in (`"fr"`, `"en"`), or
`None` to let it detect the language. Passed to ElevenLabs as
`language_code`, which also enforces that language’s text normalisation
(numbers, dates). Set it with `dataclasses.replace(V4_PRESENTER,
language_code="fr")`.

#### supports_speed *: [bool](https://docs.python.org/3/builtins/functions.html#bool)* *= True*

Whether the model honors `voice_settings["speed"]`. \*\*False for eleven
v3\*\*, which has no speed control at all (“Speed is not available for the
Eleven v3 model”), so sending one there is undefined. The paced narration
path ([`braidio.pacing`](braidio.pacing.html.md#module-braidio.pacing)) reads this: on a speedless model it plans no
speed and varies tempo through real inter-turn silence, punctuation and
audio tags instead.

### braidio.delivery.SPEEDLESS_MODELS *= frozenset({'eleven_v3', 'eleven_v4', 'eleven_v4_turbo'})*

Models that ignore `voice_settings["speed"]` (eleven v3 and v4 have no speed
control: “Style and Speed sliders are not available” on v4).

### braidio.delivery.as_delivery(delivery)

`delivery` as a [`Delivery`](#braidio.delivery.Delivery): one passes through, a name is looked
up in `DELIVERIES`. Strict: an unknown name raises `ValueError`
naming the known ones, anything else `TypeError`. (The env and user
config defaults are read leniently by [`braidio.defaults.default_delivery()`](braidio.defaults.html.md#braidio.defaults.default_delivery);
an explicit argument never is: a silent fallback would voice, and bill, a
whole production with a delivery nobody asked for.)

```pycon
>>> as_delivery("v3-narrator").model_id
'eleven_v3'
>>> as_delivery("v9")
Traceback (most recent call last):
    ...
ValueError: unknown delivery 'v9'; use one of [...]
```

* **Return type:**
  [`Delivery`](#braidio.delivery.Delivery)

### braidio.delivery.model_supports_speed(model_id)

Whether `model_id` honors `voice_settings["speed"]`.

```pycon
>>> model_supports_speed("eleven_v3"), model_supports_speed("eleven_multilingual_v2")
(False, True)
>>> model_supports_speed("eleven_v4")
False
```

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)
