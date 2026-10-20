# braidio.video

Turn a rendered production into a Ken Burns film over still images.

braidio’s delegation table sends *video render* to `reelee` – and still does
for anything with generated footage, characters or shots. This module is the one
case reelee cannot serve: reelee imports braidio, so braidio cannot import
reelee. What it needs instead is a leaf dependency, and pan/zoom over stills is
exactly that: `burns` owns the motion, this module owns *where the cuts go*,
which is braidio-specific knowledge because only braidio knows where a beat ends.

**Optional layer.** Nothing here is imported by `braidio/__init__` eagerly and
no function-level dependency is imported at module scope, so `import
braidio.video` succeeds with nothing extra installed. `HAS_VIDEO` reports
whether the render path’s dependencies (`braidio[video]` -> `burns`,
`pillow`) are actually present; [`plan_spans()`](#braidio.video.plan_spans) and [`assign_stills()`](#braidio.video.assign_stills)
are pure and work regardless.

The seams, and what each defaults to:

Deliberately *not* seams: sentence splitting, the credits-card layout, the
panel-length bounds. They are written directly, with constants at module top.

Why the blurred fill: a pool of found stills mixes portrait and landscape, and
cover-cropping a tall image into a wide frame throws most of it away. Compositing
onto a blurred, darkened enlargement of the image itself keeps the whole picture
and never shows dead black bars.

### Functions

| [`assign_stills`](#braidio.video.assign_stills)(spans, stills, \*[, zoom])         | Attach stills to `spans`, cycling so none repeats back-to-back.                     |
|---------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------|
| [`credits_card`](#braidio.video.credits_card)(lines, dst, \*[, heading, ...])     | Render an end card listing `lines`, **fitted** so none is lost.                     |
| [`missing_dependencies`](#braidio.video.missing_dependencies)()                           | Which `braidio[video]` dependencies are absent (empty when ready).                  |
| [`plan_spans`](#braidio.video.plan_spans)(timeline, \*[, min_panel_s, ...])     | Cut `timeline` into contiguous spans of roughly one still each.                     |
| [`prepare_still`](#braidio.video.prepare_still)(src, dst, \*[, size])              | Composite `src` onto a blurred fill of itself at exactly `size`.                    |
| [`save_atomically`](#braidio.video.save_atomically)(img, dst, \*\*encode)            | `img.save(dst)` through a sibling temp file and an atomic rename.                   |
| [`render_video`](#braidio.video.render_video)(panels, \*, audio_path, out_path)   | Render `panels` as one film and mux `audio_path` under it.                          |
| [`concat_argv`](#braidio.video.concat_argv)(listing, \*, audio_path, ...[, ...]) | Join the pieces a concat `listing` names, under `audio_path`, as the delivered mp4. |
| [`segment_argv`](#braidio.video.segment_argv)(source, in_s, frames, \*, out_path) | One cut: exactly `frames` frames of `source` from `in_s`, as a piece.               |
| [`frame_counts`](#braidio.video.frame_counts)(panels, \*, fps)                    | Frames per panel, rounded on the ABSOLUTE timeline so they never drift.             |
| [`runs`](#braidio.video.runs)(panels)                                     | Group consecutive panels into `("footage" | "stills", indices)` runs.               |

### Classes

| [`Footage`](#braidio.video.Footage)(path[, in_s])                        | Recorded video a panel plays as it is — a straight cut, no camera move.                                     |
|-----------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| [`Panel`](#braidio.video.Panel)(start, end, still[, style, zoom, ...]) | A [`Span`](#braidio.video.Span) with a still and its camera move — or with footage. |
| [`Span`](#braidio.video.Span)(start, end, beat_index[, kind, label])  | A stretch of screen time, before any image is chosen for it.                                                |

### *class* braidio.video.Footage(path, in_s=0.0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Recorded video a panel plays as it is — a straight cut, no camera move.

`in_s` is where in `path` the panel’s span starts; the panel’s own
duration says how much of it plays. A screen recording, a clip of a talk,
a phone video: anything ffmpeg reads.

### *class* braidio.video.Panel(start, end, still, style='push', zoom=1.18, label='', footage=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A [`Span`](#braidio.video.Span) with a still and its camera move — or with footage.

With `footage` set, the panel plays that video over its span and
`still`/`style`/`zoom` are not rendered: `still` stays the panel’s
poster (what a storyboard or a picker shows for it).

### *class* braidio.video.Span(start, end, beat_index, kind='', label='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A stretch of screen time, before any image is chosen for it.

`beat_index` / `kind` / `label` carry the beat this came from, so a
caller assigning pictures can see what is being said over each span.

### braidio.video.assign_stills(spans, stills, , zoom=1.18)

Attach stills to `spans`, cycling so none repeats back-to-back.

This is the *mechanical* default. Which picture belongs over which sentence is
an authoring decision — it needs to know what is being said — so a caller that
cares builds [`Panel`](#braidio.video.Panel) objects directly and uses each span’s `label`
and `beat_index` to choose. This function is what you want when the stills
are interchangeable texture.

### Examples

```pycon
>>> spans = [Span(0, 5, 0), Span(5, 10, 1), Span(10, 15, 2)]
>>> [p.still for p in assign_stills(spans, ["a.jpg", "b.jpg"])]
['a.jpg', 'b.jpg', 'a.jpg']
>>> [p.style for p in assign_stills(spans, ["a.jpg", "b.jpg"])]
['push', 'drift', 'push']
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Panel`](#braidio.video.Panel)]

### braidio.video.concat_argv(listing, , audio_path, out_path, total_frames, fps=30, ffmpeg='ffmpeg', crf=18)

Join the pieces a concat `listing` names, under `audio_path`, as the delivered mp4.

```pycon
>>> argv = concat_argv("l.txt", audio_path="a.wav", out_path="o.mp4", total_frames=90)
>>> argv[argv.index("-t") + 1], argv[argv.index("-f") + 1]
('3.000', 'concat')
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### braidio.video.credits_card(lines, dst, , heading='Credits', footer='', size=(1920, 1080))

Render an end card listing `lines`, **fitted** so none is lost.

Reusing a photograph under CC BY / CC BY-SA is conditional on crediting it, so
for a film built from found images this card is part of the licence
compliance, not decoration. Generate `lines` from whatever manifest recorded
the fetches, so the card cannot drift from what was actually used.

Because it is compliance, it must not *silently* drop an attribution — and a
fixed 31px leading did exactly that past roughly thirty lines: the tail ran
off the bottom of the frame and the footer drew over whatever was left. A
forty-three-image film credited twenty-eight of them and looked fine.

So the type is fitted to the content instead. Leading shrinks first, then the
list goes to two columns, and only if it still will not fit does this raise —
which is the right failure, because a caller who cannot see the problem cannot
fix it. Pass a bigger `size` or split across two cards.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### braidio.video.frame_counts(panels, , fps)

Frames per panel, rounded on the ABSOLUTE timeline so they never drift.

Rounding each duration alone would let a film of many short panels slide
off its narration by a frame per cut; rounding each boundary keeps every
cut within half a frame of where the audio says it is.

```pycon
>>> ps = [Panel(0, 1.01, "a"), Panel(1.01, 2.02, "b"), Panel(2.02, 3.03, "c")]
>>> frame_counts(ps, fps=30)
[30, 31, 30]
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`int`](https://docs.python.org/3/builtins/functions.html#int)]

### braidio.video.missing_dependencies()

Which `braidio[video]` dependencies are absent (empty when ready).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### braidio.video.plan_spans(timeline, , min_panel_s=5.0, max_panel_s=9.0)

Cut `timeline` into contiguous spans of roughly one still each.

Cuts land on **beat boundaries** wherever a beat is already a comfortable
length, because that is where the narration actually changes subject. A beat
longer than `max_panel_s` is divided into equal parts (so a 30-second
passage becomes four panels rather than one stalled photograph); a beat
shorter than `min_panel_s` is merged forward into the next one rather than
producing a flicker.

The result is gapless and ordered: `spans[i].end == spans[i + 1].start`.

* **Parameters:**
  * **timeline** – a [`TimelineBreakdown`](braidio.timeline.html.md#braidio.timeline.TimelineBreakdown).
  * **min_panel_s** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – below this, a beat is merged into the following span.
  * **max_panel_s** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – above this, a beat is split into equal parts.
* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Span`](#braidio.video.Span)]
* **Returns:**
  Spans covering the whole production, in playback order.

### Examples

```pycon
>>> from braidio.timeline import build_timeline
>>> tl = build_timeline(kinds=["narration"], durations=[20.0])
>>> [round(s.duration, 2) for s in plan_spans(tl)]
[6.67, 6.67, 6.67]
```

A short beat does not become its own panel:

```pycon
>>> tl = build_timeline(kinds=["narration", "narration"], durations=[2.0, 8.0])
>>> len(plan_spans(tl))
1
```

### braidio.video.prepare_still(src, dst, , size=(1920, 1080))

Composite `src` onto a blurred fill of itself at exactly `size`.

Nothing is cropped away and no dead black bar appears, whatever the source
aspect. Idempotent: an existing `dst` is returned untouched, so re-running a
build does not redo the work.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### braidio.video.render_video(panels, , audio_path, out_path, size=(1920, 1080), fps=30, workdir=None, prepare=None, path_for=None, runner=None, footage_duration=None, \*\*write_kwargs)

Render `panels` as one film and mux `audio_path` under it.

Stills get a Ken Burns move; panels with [`Footage`](#braidio.video.Footage) play their video
as a straight cut. A film of stills only is one `burns.ken_burns_film`
pass rather than per-panel renders plus a concat: that avoids a re-encode
seam at every cut and a frozen frame at every panel tail. A film with
footage renders each run of consecutive stills that way (silent), cuts each
run and each footage panel into a piece of exactly its frames, one at a time
so memory stays flat ([`segment_argv()`](#braidio.video.segment_argv)), and joins the pieces under the
audio in one final encode ([`concat_argv()`](#braidio.video.concat_argv)).

* **Parameters:**
  * **panels** ([`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[[`Panel`](#braidio.video.Panel)]) – the stills (or footage) and their screen time, in order.
  * **audio_path** – the finished mix. Its length should match the panels; pad it
    first if the film ends on a credits card.
  * **out_path** – mp4 to write.
  * **size** ([`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`int`](https://docs.python.org/3/builtins/functions.html#int), [`int`](https://docs.python.org/3/builtins/functions.html#int)]) – frame geometry and rate.
  * **fps** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – frame geometry and rate.
  * **workdir** – where prepared canvases are cached (default: next to `out_path`).
  * **prepare** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]]) – still -> frame-sized image. Default [`prepare_still()`](#braidio.video.prepare_still).
  * **path_for** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`object`](https://docs.python.org/3/builtins/functions.html#object)]]) – `(image_path, index, panel) -> BurnsPath`. Default is
    `burns.content_aware_path_for`, which frames on the image’s salient
    region so a slow push stays on the subject.
  * **runner** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`object`](https://docs.python.org/3/builtins/functions.html#object)]]) – runs an ffmpeg argv (default [`subprocess.run()`](https://docs.python.org/3/library/subprocess.html#subprocess.run) with
    `check=True`); only the footage path shells out.
  * **footage_duration** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`float`](https://docs.python.org/3/builtins/functions.html#float)]]) – a footage file’s length in seconds (default: ffprobe);
    an in-point at or past it is refused before anything renders.
  * **\*\*write_kwargs** – forwarded to `burns.ken_burns_film` (the still runs;
    the footage path encodes its own pieces).
* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
* **Returns:**
  The written mp4 path.

### braidio.video.runs(panels)

Group consecutive panels into `("footage" | "stills", indices)` runs.

Each footage panel is its own run (it has its own in-point); consecutive
stills share one, rendered as a single Ken Burns pass.

```pycon
>>> P = lambda f=None: Panel(0, 1, "a.jpg", footage=f)
>>> runs([P(), P(), P(Footage("v.mp4")), P(Footage("v.mp4", 3)), P()])
[('stills', [0, 1]), ('footage', [2]), ('footage', [3]), ('stills', [4])]
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`int`](https://docs.python.org/3/builtins/functions.html#int)]]]

### braidio.video.save_atomically(img, dst, \*\*encode)

`img.save(dst)` through a sibling temp file and an atomic rename.

A cached canvas is shared: the studio’s move preview and a render can both
ask for it at once, and a reader that opens a half-written JPEG would
otherwise cache the truncation under its content key for good.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### braidio.video.segment_argv(source, in_s, frames, , out_path, size=(1920, 1080), fps=30, ffmpeg='ffmpeg')

One cut: exactly `frames` frames of `source` from `in_s`, as a piece.

Seeked at the input (fast, and exact in modern ffmpeg), resampled to
`fps`, fitted inside `size` without cropping (letterboxed on black),
and — should the source run out early — held on its last frame rather than
cutting short, so the picture never slides off the narration. Pieces are
near-lossless (they are re-encoded once more, by [`concat_argv()`](#braidio.video.concat_argv)).

```pycon
>>> argv = segment_argv("rec.mp4", 2.5, 60, out_path="p.mp4", size=(1080, 1920))
>>> argv[argv.index("-ss") + 1], argv[argv.index("-t") + 1]
('2.500', '3.000')
>>> "trim=end_frame=60" in argv[argv.index("-vf") + 1]
True
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]
