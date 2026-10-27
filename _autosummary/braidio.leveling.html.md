# braidio.leveling

Level one spoken part to a loudness target with a single measured gain.

Every narration take, dialogue exchange and source clip is levelled before it
is woven, so a quiet clip sits beside a loud take. ffmpeg’s one-pass
`loudnorm` would do that *dynamically*: it rides the gain through the part,
flattening a voice’s own dynamics (a whisper, an emphasis) and pumping quiet
passages. [`level_part()`](#braidio.leveling.level_part) instead measures the part once and applies one
gain, then a true-peak limiter holds the ceiling. The measurement and the
limiter are `mixing.loudness` (the fleet’s one loudness component);
this module only decodes, applies and encodes.

```pycon
>>> peak_ceiling(DEFAULT_TRUE_PEAK_DB)
0.84...
```

### Module Attributes

| [`DEFAULT_TARGET_LUFS`](#braidio.leveling.DEFAULT_TARGET_LUFS)   | Integrated loudness a spoken part is levelled to, in LUFS.                                                           |
|------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| [`DEFAULT_TRUE_PEAK_DB`](#braidio.leveling.DEFAULT_TRUE_PEAK_DB)  | True-peak ceiling of a levelled part, in dBTP.                                                                       |
| [`OUTPUT_SAMPLE_RATE`](#braidio.leveling.OUTPUT_SAMPLE_RATE)    | Sample rate every levelled part is written at, so the weave concatenates parts of one rate whatever each source was. |

### Functions

| [`level_part`](#braidio.leveling.level_part)(src, dst, \*[, target_lufs, ...])   | Write `src` to `dst` at `target_lufs` with one gain, true-peak limited.   |
|-------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`peak_ceiling`](#braidio.leveling.peak_ceiling)(true_peak_db)                     | The linear ceiling (full scale 1.0) of a `true_peak_db` dBTP limit.       |

### braidio.leveling.DEFAULT_TARGET_LUFS *= -16.0*

Integrated loudness a spoken part is levelled to, in LUFS.

### braidio.leveling.DEFAULT_TRUE_PEAK_DB *= -1.5*

True-peak ceiling of a levelled part, in dBTP.

### braidio.leveling.OUTPUT_SAMPLE_RATE *= 44100*

Sample rate every levelled part is written at, so the weave concatenates
parts of one rate whatever each source was.

### braidio.leveling.level_part(src, dst, , target_lufs=-16.0, true_peak_db=-1.5, sample_rate=44100)

Write `src` to `dst` at `target_lufs` with one gain, true-peak limited.

The gain is `mixing.loudness.level_group()` over the whole part (so the
part keeps its own dynamics; silence gets none), the limiter
`mixing.loudness.limit_peaks()` at `true_peak_db`. Gains are applied
in float, so nothing clips before the limiter. `dst` is written at
`sample_rate`, in the format its suffix names.

Bounds worth knowing:

- The gain is capped at `mixing.loudness.MAX_GAIN_DB` (with a warning):
  a part far below the target comes out quieter than it, rather than as
  amplified noise (`loudnorm` would have pulled it all the way up).
- The ceiling holds on the samples handed to the encoder; a lossy `dst`
  (mp3) can read a few tenths of a dB over it. A levelled part is an
  intermediate: the final master limits again.
- The whole part is decoded into memory (float, plus the limiter’s copy):
  fine for spoken parts and clips of minutes, roughly 1 GB for 10 minutes
  of 48 kHz stereo. A streaming file-level leveller belongs in `mixing`.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### braidio.leveling.peak_ceiling(true_peak_db)

The linear ceiling (full scale 1.0) of a `true_peak_db` dBTP limit.

* **Return type:**
  [`float`](https://docs.python.org/3/builtins/functions.html#float)
