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

### Classes

| [`Delivery`](#braidio.delivery.Delivery)(name, model_id[, voice_settings, ...])   | A named narration delivery: which model + voice settings to synthesize with.   |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|

### *class* braidio.delivery.Delivery(name, model_id, voice_settings=dict(), supports_audio_tags=False, supports_speed=True, note='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A named narration delivery: which model + voice settings to synthesize with.

#### supports_speed *: [bool](https://docs.python.org/3/builtins/functions.html#bool)* *= True*

Whether the model honors `voice_settings["speed"]`. \*\*False for eleven
v3\*\*, which has no speed control at all (“Speed is not available for the
Eleven v3 model”), so sending one there is undefined. The paced narration
path ([`braidio.pacing`](braidio.pacing.html.md#module-braidio.pacing)) reads this: on a speedless model it plans no
speed and varies tempo through real inter-turn silence, punctuation and
audio tags instead.
