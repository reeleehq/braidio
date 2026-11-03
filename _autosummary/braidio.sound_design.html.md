# braidio.sound_design

Sound design for a rendered production: foley chooses and places the effects (braidio#161).

braidio designs no sound of its own beyond the caller’s music bed and the
structural stings. Sound effects are `foley`’s job: `foley.score()` reads
the narration text, picks sounds under its fail-closed licence gate, aligns
them to the spoken words in the rendered audio, weaves them in and masters the
mix. This module is the one adapter from a braidio render to that call:

- [`SoundDesign`](#braidio.sound_design.SoundDesign) is the switch and its knobs. Its *absence* is “off”: a
  render that is not given one is byte-for-byte what it was before.
- [`talk_segments()`](#braidio.sound_design.talk_segments) turns the render’s talk spans (where each spoken beat
  sits on the episode, and what it says) into foley’s segments: one per
  sentence, each with an estimated start, so foley’s per-segment `max_events`
  cap is per sentence (the taste ceiling) and its aligner searches the right
  stretch of audio rather than the whole file. Clips and stings contribute no
  segment, so no effect is anchored to a song excerpt.
- [`score_mix()`](#braidio.sound_design.score_mix) runs the designer over the mix and writes the result.

foley is the optional `braidio[sound]` extra, imported only when a design
runs. The designer is a seam (`SoundDesign(designer=...)`), so a host can
bring its own and the tests run without foley.

```pycon
>>> talk_segments([TalkSpan(start_s=2.0, duration_s=4.0, text="Rain fell. Door shut.")])
(['Rain fell.', 'Door shut.'], [2.0, 4.0])
```

### Module Attributes

| [`SOUND_EXTRA`](#braidio.sound_design.SOUND_EXTRA)          | The extra that installs the default designer.                                                                                             |
|-----------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------|
| [`DFLT_MAX_EVENTS`](#braidio.sound_design.DFLT_MAX_EVENTS)      | foley's defaults, named so a render record can say what was asked for.                                                                    |
| [`SOUND_SIDECAR_SUFFIX`](#braidio.sound_design.SOUND_SIDECAR_SUFFIX) | what was designed, and the credits that must ship with it (`episode.mp3` → `episode.sound.json`).                                         |
| [`COST_UNKNOWN`](#braidio.sound_design.COST_UNKNOWN)         | foley reports no spend yet (reeleehq/foley#280), though it may pay for an LLM rung or a generated sound, capped by `SoundDesign.max_usd`. |
| [`Designer`](#braidio.sound_design.Designer)             | `foley.score`'s signature (segments, then keyword options).                                                                               |

### Functions

| [`score_mix`](#braidio.sound_design.score_mix)(audio, spans, \*, design, profile, ...)   | Sound-design the mix at `audio` and write the result to `out_path`.                                                                                                                         |
|------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`spoken_of`](#braidio.sound_design.spoken_of)(beat)                                     | What a script beat says aloud: a narration's text, a dialogue's turns, `""` for anything else (a clip's words are the source's; a scene break says nothing).                                |
| [`spoken_text`](#braidio.sound_design.spoken_text)(text)                                   | `text` without its audio tags, whitespace collapsed.                                                                                                                                        |
| [`talk_segments`](#braidio.sound_design.talk_segments)(spans)                                | foley's `(segments, segment_starts)` for the talk on a timeline.                                                                                                                            |
| [`talk_spans`](#braidio.sound_design.talk_spans)(beats, texts)                            | The spoken [`TalkSpan`](#braidio.sound_design.TalkSpan) of a timeline: its beat spans (anything with `start` and `duration`) zipped with what each says, silent ones left out. |

### Classes

| [`ScoredMix`](#braidio.sound_design.ScoredMix)(path[, timeline, credits, ...])       | What a sound-design pass made: the mix, and what must ship with it.                                  |
|--------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| [`SoundDesign`](#braidio.sound_design.SoundDesign)([max_events, master, max_usd, ...]) | Sound design is on, with these knobs (foley's defaults throughout).                                  |
| [`TalkSpan`](#braidio.sound_design.TalkSpan)(start_s, duration_s, text)             | One spoken beat on the episode timeline: where it starts, how long it lasts, and what is said in it. |

### braidio.sound_design.COST_UNKNOWN *= {'cost_basis': 'unknown', 'cost_usd': None}*

foley reports no spend yet
(reeleehq/foley#280), though it may pay for an LLM rung or a generated
sound, capped by `SoundDesign.max_usd`. Unknown, never `0.0`.

* **Type:**
  What a design run spent is not known

### braidio.sound_design.DFLT_MAX_EVENTS *= 6*

foley’s defaults, named so a render record can say what was asked for.

### braidio.sound_design.Designer

`foley.score`’s signature (segments, then keyword options).

* **Type:**
  The designer

alias of `Callable`[[…], [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### braidio.sound_design.SOUND_EXTRA *= 'sound'*

The extra that installs the default designer.

### braidio.sound_design.SOUND_SIDECAR_SUFFIX *= '.sound.json'*

what was designed, and the credits that must
ship with it (`episode.mp3` → `episode.sound.json`).

* **Type:**
  Beside a sound-designed episode

### *class* braidio.sound_design.ScoredMix(path, timeline=dict(), credits=dict(), captions_vtt='', notes=())

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What a sound-design pass made: the mix, and what must ship with it.

`credits` must be published with the episode (licences of the sounds
used); `timeline` is foley’s editable sound-design timeline; `captions_vtt`
are SDH captions (sound cues only, no speech).

#### to_dict()

Plain JSON (everything but the path); foley’s numpy scalars become
Python numbers, so the record survives `json.dumps` as is.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### *class* braidio.sound_design.SoundDesign(max_events=6, master='podcast', max_usd=None, allow_generate=True, designer=None, writer=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Sound design is on, with these knobs (foley’s defaults throughout).

`max_events` caps the sounds per *sentence*; `master` is foley’s
delivery target (it always masters the woven mix, and `"podcast"` is
-16 LUFS / -1 dBTP, braidio’s own default); `max_usd` caps what one run
may spend (`None`: foley’s default cap); `allow_generate=False` is
retrieval only, with no paid generation fallback (offline and CI renders).
The licence intent is not a knob here: it follows the render’s rights
profile ([`commercial_ok()`](#braidio.sound_design.SoundDesign.commercial_ok)). `designer` is the strategy that does the
work, called like `foley.score()` (default: foley itself), and
`writer` the one that writes its mix, `(weave, path) -> None` (default:
soundfile then ffmpeg to MP3); neither is part of the record, and both are
fast-path only (the graph runs nw’s foley Transform, which records them).

#### *static* commercial_ok(profile)

foley’s licence intent for `profile`: a published production admits
only commercially-licensed sounds; a personal one also non-commercial.

```pycon
>>> SoundDesign.commercial_ok(Profile.PUBLISHED), SoundDesign.commercial_ok(Profile.PERSONAL)
(True, False)
```

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

#### missing_dependencies(, writes_mix=True)

What this design needs and this environment lacks, by import name:
`foley` unless a `designer` replaces it, and, when braidio writes
the mix itself (`writes_mix`, the fast path) with no `writer`,
`soundfile`. Checked **before** a render spends anything, so a
missing extra fails at the door rather than after every voice is billed.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

#### require(, writes_mix=True)

Raise `ModuleNotFoundError` naming the extra when
[`missing_dependencies()`](#braidio.sound_design.SoundDesign.missing_dependencies) is not empty.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### to_config(profile)

The graph’s `sound-design-config/v1` body (nw’s schema) for a
production rendered under `profile`: these knobs, switched on, with
the licence intent the profile implies.

```pycon
>>> SoundDesign(max_events=2).to_config(Profile.PERSONAL)["commercial_ok"]
False
```

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

#### to_dict()

Plain JSON, for a render’s settings record.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### *class* braidio.sound_design.TalkSpan(start_s, duration_s, text)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One spoken beat on the episode timeline: where it starts, how long it
lasts, and what is said in it.

### braidio.sound_design.score_mix(audio, spans, , design, profile, out_path)

Sound-design the mix at `audio` and write the result to `out_path`.

`spans` is the talk on the mix’s timeline ([`talk_segments()`](#braidio.sound_design.talk_segments)). The
design’s `designer` (default `foley.score()`) is called with foley’s
keywords; its `writer` writes the `weave` result (default: an MP3
through ffmpeg). `out_path` may be `audio` itself: the mix is fully
read before anything is written.

Raises `ValueError` when there is no talk to design for, rather than
hand foley nothing and report an undesigned mix as designed.

* **Return type:**
  [`ScoredMix`](#braidio.sound_design.ScoredMix)

### braidio.sound_design.spoken_of(beat)

What a script beat says aloud: a narration’s text, a dialogue’s turns,
`""` for anything else (a clip’s words are the source’s; a scene break
says nothing).

```pycon
>>> from braidio.script import Dialogue, Narration
>>> spoken_of(Narration("Hi.")), spoken_of(Dialogue(turns=[("a", "Yes."), ("b", "No.")]))
('Hi.', 'Yes. No.')
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.sound_design.spoken_text(text)

`text` without its audio tags, whitespace collapsed.

```pycon
>>> spoken_text("[dryly] Well. [pause] That went  well.")
'Well. That went well.'
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### braidio.sound_design.talk_segments(spans)

foley’s `(segments, segment_starts)` for the talk on a timeline.

Each span’s sentences start where the span starts, then advance in
proportion to the characters spoken before them. That is an estimate (the
beat’s start is exact, the sentence’s is not), and it only has to put
foley’s aligner in the right stretch of audio.

foley needs every worded segment to start strictly after the previous one.
The weave crossfades consecutive beats, so a beat can start before the
previous one ends: each span’s sentences are therefore spread over its
*window*, from its start to the next span’s start (or its own end when
that comes first), never over its full duration.

```pycon
>>> talk_segments([TalkSpan(0.0, 3.0, "One. Two two two."), TalkSpan(5.0, 1.0, "")])
(['One.', 'Two two two.'], [0.0, 0.75])
```

A long beat whose short last sentence would land after the crossfaded next
beat’s start stays inside its window:

```pycon
>>> talk_segments([TalkSpan(0.0, 12.0, "x" * 98 + ". No."), TalkSpan(11.88, 2.0, "Next.")])[1]
[0.0, 11.531, 11.88]
```

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`float`](https://docs.python.org/3/builtins/functions.html#float)]]

### braidio.sound_design.talk_spans(beats, texts)

The spoken [`TalkSpan`](#braidio.sound_design.TalkSpan) of a timeline: its beat spans (anything with
`start` and `duration`) zipped with what each says, silent ones left out.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`TalkSpan`](#braidio.sound_design.TalkSpan)]
