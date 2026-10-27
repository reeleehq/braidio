# braidio.structure

Structural music — stings at scene breaks, fade-to-spotlight on exhibits.

The music bed ([`braidio.music`](braidio.music.html.md#module-braidio.music)) is the *continuous* music layer. This module
is the *structural* one: the cues that tell a listener where they are, because
audio has no visual white space (`misc/docs/research/commentary-formats-and-
styles.md`, “Music as structure”):

- a **sting** — a short musical marker — at a [`SceneBreak`](braidio.script.html.md#braidio.script.SceneBreak)
  (“new section”);
- **fade-to-spotlight** — the bed drops *out* before a key exhibit so the clip
  lands in silence instead of competing with underscore, and resumes after it.

Both are driven by a [`MusicStructure`](#braidio.structure.MusicStructure): the format’s defaults
(`scene_marker`, `spotlight_clips`) plus the production’s [`Sting`](#braidio.structure.Sting)
asset. It is the single seam [`braidio.render.render_production()`](braidio.render.html.md#braidio.render.render_production) takes
(`structure=`); `None` means “no structure” and reproduces the plain weave
exactly. A beat overrides the defaults with `SceneBreak.marker` /
`SegmentBeat.spotlight`.

braidio ships **no music**: like the bed, the sting is an app-supplied asset
(`render_format(..., sting_asset=…)`). A scene break with no sting to play is
still audible — as `MusicStructure.pause_s` of silence — so the structure
is never silently lost.

### Functions

| [`prepare_pause`](#braidio.structure.prepare_pause)(seconds, out_path, \*[, ...])     | Render `seconds` of stereo silence — a scene break with no sting to play.   |
|--------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| [`prepare_sting`](#braidio.structure.prepare_sting)(sting, out_path, \*, target_lufs) | Render `sting` to a ready-to-place part: trimmed, faded, levelled, padded.  |

### Classes

| [`MusicStructure`](#braidio.structure.MusicStructure)([sting, scene_marker, ...])   | How a production marks its structure with music — the render seam.   |
|-----------------------------------------------------------------------------------------------|----------------------------------------------------------------------|
| [`Sting`](#braidio.structure.Sting)(asset_path[, gain_db, max_len_s, ...]) | A short musical marker played at a scene break.                      |

### *class* braidio.structure.MusicStructure(sting=None, scene_marker='sting', spotlight_clips=False, pause_s=0.8)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

How a production marks its structure with music — the render seam.

`scene_marker` is the default for a [`SceneBreak`](braidio.script.html.md#braidio.script.SceneBreak)
whose `marker` is `None` (`"sting"` / `"none"`). `spotlight_clips`
is the default for a [`SegmentBeat`](braidio.script.html.md#braidio.script.SegmentBeat) whose
`spotlight` is `None` — `True` drops the bed under every exhibit.
`sting` is the production’s sting asset; with none supplied a
`"sting"`-marked break falls back to `pause_s` of silence.

The all-defaults value is inert for a script with no scene breaks and no
spotlight-marked clips: a format that declares no structure renders exactly
as it did without this layer.

#### marker_for(beat)

The marker a scene break renders with (its override, else the default).

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

#### marker_of(marker)

The marker a break with this per-beat override renders with.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

#### plays_sting(beat)

Whether `beat` plays the sting (marked `"sting"` *and* one is supplied).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

#### plays_sting_of(marker)

Whether this per-beat marker plays the sting (and one is supplied).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

#### spotlight_for(beat)

Whether `beat` is spotlit (its override, else the format default).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

#### spotlight_of(spotlight)

Whether this per-beat override is spotlit (`None` = the format default).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### *class* braidio.structure.Sting(asset_path, gain_db=-6.0, max_len_s=3.0, fade_out_s=0.5, gap_after_s=0.3)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A short musical marker played at a scene break.

`asset_path` is an app-supplied sound (a hit, a riser, a few notes of the
theme). It is trimmed to `max_len_s` with a `fade_out_s` tail, levelled
to the production’s loudness target, sat `gain_db` under the voice, and
followed by `gap_after_s` of breathing room before the talk resumes.

### braidio.structure.prepare_pause(seconds, out_path, , sample_rate=44100)

Render `seconds` of stereo silence — a scene break with no sting to play.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### braidio.structure.prepare_sting(sting, out_path, , target_lufs, sample_rate=44100)

Render `sting` to a ready-to-place part: trimmed, faded, levelled, padded.

The result is already at production level (`target_lufs + sting.gain_db`),
so the caller places it as-is rather than normalizing it again. The level is
one measured gain with a true-peak limit applied last, so the ceiling holds
whatever `gain_db` is ([`braidio.leveling.level_part()`](braidio.leveling.html.md#braidio.leveling.level_part), as every
spoken part gets), not a gain ridden through the sting. Returns `out_path`.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
