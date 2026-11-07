# braidio.defaults

User-overridable, persisted defaults for how braidio renders a voice.

**Why this module exists.** The package default used to be
`eleven_multilingual_v2`, a model that cannot render inline `[audio tags]`
*at all*. Since tag density is the main lever on whether a read sounds alive
(measured: plain text 70.8 Hz of pitch range, densely tagged 124.4 Hz, while
`stability` across its whole range moves it only 85 -> 72 Hz), the old default
capped every production at the flat end before an author had written a word. One
listener’s verdict on such a render was “somniferous”.

So the package default is now the expressive one. But a default nobody can
change is just a different imposition, hence the resolution order:

> explicit argument  >  environment variable  >  user config file  >  package default

The config file is JSON at `$BRAIDIO_CONFIG`, else
`$XDG_CONFIG_HOME/braidio/config.json`, else `~/.config/braidio/config.json`:

```json
{
  "delivery": "v3-presenter",
  "voice_id": "iP95p4xoKVk53GoZ742B",
  "voice_settings": {"stability": 0.0, "use_speaker_boost": true}
}
```

Every key is optional; what you leave out keeps the package default. Nothing
here touches the network, and a malformed file is reported once and then ignored
rather than taking a render down with it.

### Module Attributes

| [`PACKAGE_DEFAULT_DELIVERY`](#braidio.defaults.PACKAGE_DEFAULT_DELIVERY)   | What braidio uses when nothing else says otherwise.   |
|-----------------------------------------------------------------------------|-------------------------------------------------------|

### Functions

| [`config_path`](#braidio.defaults.config_path)()                | Where the user's persisted defaults live (the file need not exist).   |
|-------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| [`user_config`](#braidio.defaults.user_config)()                | The user's persisted defaults, or `{}`.                               |
| [`default_delivery`](#braidio.defaults.default_delivery)([explicit]) | Resolve the delivery to render with.                                  |
| [`default_voice_id`](#braidio.defaults.default_voice_id)([explicit]) | Resolve the narration voice id, or `None` to let the caller decide.   |
| [`default_voice_settings`](#braidio.defaults.default_voice_settings)()     | The resolved delivery's voice settings, as a fresh dict.              |
| [`describe_defaults`](#braidio.defaults.describe_defaults)()          | What is in force, and where each part came from.                      |

### braidio.defaults.PACKAGE_DEFAULT_DELIVERY *: [Delivery](braidio.delivery.html.md#braidio.delivery.Delivery)* *= Delivery(name='v3-presenter', model_id='eleven_v3', voice_settings={'stability': 0.0, 'use_speaker_boost': True}, supports_audio_tags=True, supports_speed=False, note='Host/presenter commentary on v3 -- the expressive end. The narration-spine default (solo_explainer). Colour comes from tag density in the script; this setting just gets out of its way.', language_code=None)*

What braidio uses when nothing else says otherwise. `eleven_v3` so inline
`[audio tags]` fire, at the expressive end of `stability`.

### braidio.defaults.config_path()

Where the user’s persisted defaults live (the file need not exist).

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### braidio.defaults.default_delivery(explicit=None)

Resolve the delivery to render with.

* **Parameters:**
  **explicit** ([`Delivery`](braidio.delivery.html.md#braidio.delivery.Delivery) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – a [`Delivery`](braidio.delivery.html.md#braidio.delivery.Delivery), or the name of one
  (see `braidio.delivery.DELIVERIES`). Wins over everything.
* **Return type:**
  [`Delivery`](braidio.delivery.html.md#braidio.delivery.Delivery)

### Examples

```pycon
>>> default_delivery(V3_PRESENTER) is V3_PRESENTER
True
>>> default_delivery("v3-narrator").name
'v3-narrator'
```

### braidio.defaults.default_voice_id(explicit=None)

Resolve the narration voice id, or `None` to let the caller decide.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### braidio.defaults.default_voice_settings()

The resolved delivery’s voice settings, as a fresh dict.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### braidio.defaults.describe_defaults()

What is in force, and where each part came from.

Worth printing when a render does not sound the way somebody expected: the
commonest cause is a config file they forgot they wrote.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.defaults.user_config()

The user’s persisted defaults, or `{}`.

A missing file is normal. A malformed one warns *once* per path and is then
treated as absent.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]
