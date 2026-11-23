# braidio.music

Music bed — an instrumental underscore laid under the whole production, ducked.

The bed is the spanning generalization of a per-clip `under` placement: instead
of one clip beneath one talk beat, an app-supplied **instrumental** asset runs
beneath the entire timeline at reduced gain, entering after speech onset and
fading at the ends (the conventions from
`docs/research/commentary-formats-and-styles.md`: bed 10–15 dB under voice,
~1.5s fade-in, *posted* after the start; beds must be instrumental).

braidio ships **no music** — the caller supplies the asset (a licensed/owned
instrumental). A [`Format`](braidio.formats.html.md#braidio.formats.Format)’s `music_bed` *intensity*
(continuous / light / sparse / none) picks a sensible gain via
`BED_GAIN_BY_INTENSITY`; `render_format()` builds the bed for you when
given a `bed_asset`.

**Fade-to-spotlight** ([`braidio.structure`](braidio.structure.html.md#module-braidio.structure)) is expressed here as bed
*regions*: [`bed_regions()`](#braidio.music.bed_regions) cuts the bed’s span into the stretches around the
spotlit windows, each with its own fades, and [`prepare_bed_regions()`](#braidio.music.prepare_bed_regions) renders
them so the weave mixes each in at its own start. With no spotlit window the bed
is one region rendered by [`prepare_bed()`](#braidio.music.prepare_bed) — the unchanged single-file path.

### Functions

| [`bed_for_intensity`](#braidio.music.bed_for_intensity)(asset_path, intensity, ...)   | Build a [`MusicBed`](#braidio.music.MusicBed) at the gain for a Format `music_bed` intensity.   |
|--------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| [`bed_regions`](#braidio.music.bed_regions)(bed, total_s, spotlights)           | Cut the bed's span `[lead_in_s, total_s]` around the `spotlights`.                                                  |
| [`prepare_bed`](#braidio.music.prepare_bed)(bed, target_s, out_path, \*[, ...]) | Render `bed` to a ready-to-mix underscore of length `target_s - lead_in_s`.                                         |
| [`prepare_bed_regions`](#braidio.music.prepare_bed_regions)(bed, regions, out_dir, ...) | Render each [`BedRegion`](#braidio.music.BedRegion) to `out_dir/<stem>-<k>.mp3`.                 |

### Classes

| [`BedRegion`](#braidio.music.BedRegion)(start_s, end_s, fade_in_s, fade_out_s)   | One stretch of the timeline the bed plays over, with its own fades.       |
|-----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`MusicBed`](#braidio.music.MusicBed)(asset_path[, gain_db, fade_in_s, ...])    | An instrumental underscore spanning the production, mixed under the talk. |

### *class* braidio.music.BedRegion(start_s, end_s, fade_in_s, fade_out_s)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One stretch of the timeline the bed plays over, with its own fades.

`start_s` / `end_s` are timeline offsets (the bed’s own `lead_in_s` is
already applied). The bed keeps its place in the asset across a gap: a
region seeks into the asset to where the music *would* have been.

#### *property* length_s *: [float](https://docs.python.org/3/builtins/functions.html#float)*

Length in seconds (`end_s - start_s`).

### *class* braidio.music.MusicBed(asset_path, gain_db=-22.0, fade_in_s=1.5, fade_out_s=2.0, lead_in_s=2.0, start_s=0.0, loop=True, spotlight_fade_s=0.6)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

An instrumental underscore spanning the production, mixed under the talk.

`asset_path` is an app-supplied instrumental. `gain_db` sets how far under
the voice it sits; `lead_in_s` posts the bed *after* speech onset so its
entrance feels motivated; `start_s` is the in-point into the asset;
`loop` repeats the asset to cover a timeline longer than it.
`spotlight_fade_s` is how fast the bed drops out before a spotlit exhibit
(it resumes after the exhibit over `fade_in_s`).

### braidio.music.bed_for_intensity(asset_path, intensity, \*\*overrides)

Build a [`MusicBed`](#braidio.music.MusicBed) at the gain for a Format `music_bed` intensity.

Returns `None` for `"none"` (or an unknown intensity), so callers can do
`bed = bed_for_intensity(asset, fmt.music_bed)` and skip when falsy.

* **Return type:**
  [`MusicBed`](#braidio.music.MusicBed) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### braidio.music.bed_regions(bed, total_s, spotlights)

Cut the bed’s span `[lead_in_s, total_s]` around the `spotlights`.

Pure function. Each spotlight `(start, end)` (timeline seconds) opens a
gap: the bed fades *out* over `bed.spotlight_fade_s` to be silent by
`start`, and fades back *in* over `bed.fade_in_s` from `end`. The first
region keeps the bed’s own fade-in and the last its fade-out; a region that
would be empty (a spotlight at the very top, two spotlights back to back) is
dropped. With no spotlights the result is the single whole-span region.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`BedRegion`](#braidio.music.BedRegion)]

### braidio.music.prepare_bed(bed, target_s, out_path, , sample_rate=44100)

Render `bed` to a ready-to-mix underscore of length `target_s - lead_in_s`.

Seeks to `bed.start_s` (looping if needed), trims to the covered length, and
bakes in fades + `gain_db` + stereo. The caller mixes the result delayed by
`bed.lead_in_s`. Returns `out_path`.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### braidio.music.prepare_bed_regions(bed, regions, out_dir, , stem, sample_rate=44100)

Render each [`BedRegion`](#braidio.music.BedRegion) to `out_dir/<stem>-<k>.mp3`.

Returns `[(path, start_s), …]` — the caller mixes each file in delayed by
its `start_s`. A region seeks into the asset to where the bed would have
been had it played through (so the music resumes in place after a
spotlight), wrapping around the asset’s length when the bed loops. A
non-looping bed that has already run out by a region’s start has nothing
to play there: that region is dropped (seeking past the end would yield an
empty file the mix can’t open).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path), [`float`](https://docs.python.org/3/builtins/functions.html#float)]]
