# braidio

**Weave *commentary* — solo, duo, panel, debate, interview, documentary — with
source clips into a production.**

`braidio` braids the strands of a piece that talks *about* something (a song, a
book, a film, an event) into one rendered production. The **talk is the spine** —
a single presenter, two hosts, a panel, a narrator — and **source clips and
narration bridges are the "illustration" layers** woven onto it. Audio today,
with visual support to follow (it braids A/V — *braid-io*, and it sounds like
*radio*).

Two things braidio gives you:

1. **Ready-made format templates** under the standard, industry names people
   already recognize — *Deep Dive*, *Song Exploder-style*, *Panel*, *Debate*,
   *Documentary VO* — each a high-quality bundle of defaults you can render in one
   call.
2. **Full parametrization** underneath, so any style is expressible: a `Script`
   of beats, cast via a `ConversationCast`, tuned by `WeaveConfig` + `Delivery`,
   with per-beat voice/delivery overrides.

> `braidio` was extracted from the
> [Hamilton lyrics-podcast](https://github.com/thorwhalen/Hamilton) (its first
> application) into a reusable engine. It is now live infrastructure: released
> on PyPI, served as a multi-tool MCP connector, and imported in production by
> [`reelee`](https://github.com/thorwhalen/reelee). Treat a signature change
> here as having downstream blast radius.

## The model

A production is a **`Script`** — an ordered list of **beats**:

| Beat | What it is |
|---|---|
| `Narration(text, voice?, voice_settings?, …)` | one voice reading — a narrator **or** a solo presenter. `voice` / `voice_settings` override per beat, so one timeline can carry a lively **presenter** *and* a graver **book-narrator**. |
| `Dialogue(turns=[(role, text)])` | a multi-speaker exchange, synthesized in **one pass** (so it sounds like people *talking to each other*, not alternating monologues). A `ConversationCast` maps roles → voices. |
| `SegmentBeat(reference)` | a span of source media to cut and weave in (resolved by a pluggable `SegmentSource` — quote → `[start, end]`). `spotlight=True` drops the music bed out under it. |
| `SceneBreak(label?)` | a structural boundary — "a new section starts here". Renders as a short musical **sting** (your asset) or a beat of silence; costs nothing. |

`WeaveConfig` holds every editing knob (casting, turns, pacing, clip pre/post-roll,
duck, crossfades, loudness). `Delivery` presets bundle the TTS model + voice
settings (e.g. `V2_PRESENTER` lively vs `V2_NARRATOR` grave). The renderer
loudness-normalizes every part, tucks clips under the speech (speech stays
dominant), and masters the result.

## Ready-made formats

Pick a standard format and render it — the template supplies the cast, voices,
delivery and mix defaults:

```python
from braidio import DEEP_DIVE, render_format

render_format(DEEP_DIVE, script, source=my_source, out_path="episode.mp3")
```

| Preset id | Standard name | Shape |
|---|---|---|
| `solo_explainer` | Solo-Presenter Explainer (video/audio essay, close reading) | one presenter + exhibits |
| `deep_dive` | Two-Host Conversation (*"Deep Dive"*, Switched on Pop, NotebookLM) | two hosts; one teaches, one probes |
| `interview` | Interview (host + guest) | Q→A, guest is the center |
| `interview_host_removed` | *Song Exploder*-style (host removed) | guest monologue; each claim illustrated by its isolated stem; full artifact at the tail |
| `panel` | Panel / Roundtable (Pop Culture Happy Hour) | moderator routes distinct voices |
| `debate` | Debate (Oxford-style) | proposition / opposition / moderator, phased |
| `documentary_vo` | Documentary Voice-Over (*"Voice of God"*, This American Life) | narrator on top; interviews + clips + actuality beneath |

**Narration bridges** and **source clips** are optional *illustration layers*
usable with any format. The taxonomy, weaving grammar, exemplar recipes and the
full template specs are in
[`misc/docs/research/commentary-formats-and-styles.md`](misc/docs/research/commentary-formats-and-styles.md).

*Render support note:* the cast, per-role voices, narration deliveries, loudness
master, **per-clip placement** — `SegmentBeat(placement="before" | "under" |
"after")`, where `under` lays the clip concurrently beneath the talk, ducked —
a **music bed** (`MusicBed`, an app-supplied instrumental laid under the whole
production) and the **structural music** are all applied. Structure is a
`SceneBreak` beat in the Script — "a new section starts here" — which renders as
a short **sting** when you pass `render_format(..., sting_asset=…)` (or a beat of
silence when you don't), and `SegmentBeat(spotlight=True)` for
**fade-to-spotlight**: the bed drops out before that exhibit and resumes after
it. Each format's `structure` (`MusicStructure`) sets the defaults — whether a
break stings, whether every clip is spotlit — and a beat overrides them. As with
the bed, braidio ships no music: the sting is your asset. Both render paths carry
this: the graph path takes the same `structure=` / `bed=` (or a `fmt=`, whose
declared structure it uses) on `weave_project`, records the decision as a
`production-structure/v1` node, and re-renders only the episode when you swap the
sting. Calling `weave_project` again on the same project **re-ingests**: beats are
matched by position, unchanged ones reuse their renders, and a changed config,
structure, rights profile or dialogue cast is rewritten under its existing node so
everything derived from it reads stale through provenance — the only correct way
to change a profile after the first weave. Every beat type takes the graph path,
`Dialogue` included: an exchange is one `dialogue-beat/v1` rendered in one
Text-to-Dialogue pass, and the cast (`cast=`, else the format's, else the
default) is one `dialogue-cast/v1` node every dialogue render derives from — so
recasting re-renders the exchanges and nothing else. Design history in
[braidio#25](https://github.com/thorwhalen/braidio/issues/25),
[braidio#39](https://github.com/thorwhalen/braidio/issues/39),
[braidio#46](https://github.com/thorwhalen/braidio/issues/46) and
[braidio#51](https://github.com/thorwhalen/braidio/issues/51).

## Parametrize anything

The templates are just good defaults over the primitives — compose directly for
full control:

```python
from braidio import Script, Dialogue, Narration, SegmentBeat, WeaveConfig
from braidio.conversation import ConversationCast, JESSICA, CHRIS
from braidio import V2_NARRATOR, render_production

script = Script(
    title="…",
    id_slug="01",
    beats=[
        Dialogue(
            (("A", "First thing that gets me…"), ("B", "right — but isn't that…"))
        ),
        SegmentBeat("the lyric being discussed", label="hook"),
        Narration(
            "Here's how the book tells it —", voice_settings=V2_NARRATOR.voice_settings
        ),  # graver, per-beat
    ],
)
render_production(
    script,
    source=my_source,
    cast=ConversationCast(roles={"A": JESSICA, "B": CHRIS}),
    config=WeaveConfig(),
    out_path="out.mp3",
)
```

### Cost tracking

ElevenLabs TTS is braidio's only spend (everything else is local ffmpeg). `braidio.estimate_cost(script)` previews a production's cost before you pay for synthesis, and real renders attribute a per-character cost onto their artifacts. Dollars are a **rate estimate** — set your ElevenLabs plan's rate for exact figures:

| Env var | Meaning |
|---|---|
| `BRAIDIO_TTS_USD_PER_1K_CHARS` | USD per 1000 characters. Unset → a conservative default; `none` → mark spend *unpriced* (character counts still reported, dollars `None`). |
| `BRAIDIO_TTS_VOICE` | Default ElevenLabs voice id for narration. |
| `ELEVENLABS_API_KEY` | The key synthesis bills when a render is given no `api_key=`. |

Every render entry point — `narrate`, `render_dialogue`, `render_production`, `render_format` and the graph driver `weave_project` — takes an explicit `api_key=` for a caller's own ElevenLabs key; `None` (the default) resolves from the environment. On the graph path the key reaches the two synthesis transforms through nw's `execute(secrets=)` seam and is never persisted: not in a node, provenance, a cache key or a usage ledger (thorwhalen/braidio#58). The MCP server reads it from the `X-Elevenlabs-Key` request header, never from a tool argument.

### Any voice backend (`speak=`)

ElevenLabs is the default, not a requirement. `render_production` and `render_format` take `speak=`, any callable `speak(text, *, voice) -> (audio_bytes, format)`, and route every narration beat and turn through it; a dialogue beat is spoken turn by turn, since only ElevenLabs has the one-pass Text-to-Dialogue path. Formats, pacing and weaving are unchanged, so a local model (through `voxy`, say) gives a free preview of `DEEP_DIVE`, and a test or CI can render with no key at all:

```python
render_format(DEEP_DIVE, script, source=None, speak=my_local_tts)
```

`voice` is the id braidio resolved for the line (the format's narrator, the cast's roles), which under the shipped formats is an ElevenLabs id: pass `voice_id=` / `cast=` in your backend's vocabulary, or map ids inside `speak`. Inline `[audio tags]` are stripped before `speak` sees the text, speed and other ElevenLabs settings are ignored, and nothing is cached. The render's settings record names the backend under `resolved.tts`. Only the one-call path takes `speak` today; the graph path (`weave_project`) still synthesizes through ElevenLabs (#132).

## What it deliberately does *not* do

braidio is a thin orchestration layer. It **delegates**:

| Concern | Owner |
|---|---|
| Content acquisition (Genius, audiobooks, news…) | the consuming app, via `SegmentSource` adapters |
| Linked-artifact graph / content-addressed media | [`lacing`](https://github.com/thorwhalen/lacing) |
| Project workflow, provenance, plan/execute, partial re-render | [`nw`](https://github.com/thorwhalen/nw) |
| Video render + visual support | [`reelee`](https://github.com/thorwhalen/reelee) — except the stills case below |
| Pan/zoom motion over a still | [`burns`](https://github.com/thorwhalen/burns) |
| Raw audio DSP + TTS (crop, concat, duck, loudnorm, synth) | [`mixing`](https://github.com/thorwhalen/mixing) / `falaw` |

braidio orchestrates these; it never reimplements the DSP or the graph.

## Video: a commentary episode you can watch

`braidio[video]` turns a rendered episode into a **Ken Burns film over still
images** — the one video case that stays here rather than going to `reelee`,
because reelee imports braidio and the cuts have to land on braidio's own beat
timeline. `burns` owns the motion; braidio owns only where the picture changes.

```python
import braidio
from braidio.video import plan_spans, assign_stills, render_video

path, timeline = braidio.render_format(
    fmt, script, source=src, out_path="ep.mp3", return_timeline=True
)
panels = assign_stills(plan_spans(timeline), ["a.jpg", "b.jpg", "c.jpg"])
render_video(panels, audio_path="ep.mp3", out_path="ep.mp4")
```

`plan_spans` cuts on beat boundaries — splitting a long passage so no photograph
stalls on screen, merging a short beat so none flickers. Each `Span` carries the
beat's `label` and `kind`, because *which* picture belongs over a sentence needs
to know what the sentence says; `assign_stills` is the mechanical default for when
the images are interchangeable texture. Motion is content-aware by default, so a
slow push stays on the subject. braidio fetches **no images** — supply them, as
with `SegmentSource`.

Subtitles need no ASR: you authored the words and the timeline says when they
play.

```python
Path("ep.srt").write_text(braidio.captions_for(script, timeline, max_chars=42))
```

`braidio.captions` is part of the core (pure, no extra dependencies);
For a production you will **reopen and re-edit** — swap a still, change a
move, re-render — the picture track lives in the project graph instead: four
`lacing` bodies (`still/v1`, `video-panel/v1`, `video-cut/v1`,
`label-track/v1`) and three free transforms on the `commentary_weave` genre
(`video_panels.plan`, `video_cut.render`, `video_cut.finish`). The episode
persists its timeline, a panel pins its still to a span of the audio with an
authored camera move, and a label edit re-runs the cheap text pass, not the
frames. The `braidio-commentary-video` skill has the walkthrough.

### Where a commentary project lives

A `commentary_weave` project is created by braidio's registered `nw` project
factory, and **where it lands is the caller's decision, not braidio's** (nw#84).
Called with no placement — braidio's own connector — it goes in braidio's per-user
workspace under `BRAIDIO_DATA_HOME`, exactly as before. Called with a
`projects_dir`, it is created there instead, which is how a *host* that will serve
the project (reelee) puts it beside that caller's other projects rather than under
braidio's data home, where the host's router and lister would never find it.

`braidio.project.create_project_at(projects_dir, project_id)` is the host-placed
create on its own. It is deliberately outside `braidio.mcp`, so placing a project
never pulls the `[mcp]` extra.

`braidio.video`'s dependencies are imported inside the functions that use them, so
`import braidio.video` works on a bare install and the planners stay usable —
`braidio.HAS_VIDEO` reports whether the render path is available.

## Agent skills

braidio ships skills that install with it, so an agent host can use them without
cloning the repo:

| Skill | For |
|---|---|
| `braidio` | authoring and rendering a commentary production (audio) |
| `braidio-commentary-video` | the video pipeline above — panels, stills, captions, credits, rights |

```bash
ln -s "$(python -c 'import braidio; print(braidio.skills_dir())')/braidio" ~/.claude/skills/braidio
```

## Ecosystem

`braidio` is a **production kind** on top of `nw` — a reusable definition of
"commentary that weaves talk with extracted media" — the way a music video is
another production kind. It sits on `lacing` (graph) + `nw` (workflow /
provenance) + `mixing`/`falaw` (audio/TTS), and will use `reelee` for video. The
optional nw-app layer (`braidio.HAS_GRAPH` / `HAS_NW`) records render choices as
provenance so a change re-renders only the affected parts.

## Install

```bash
pip install braidio
```

ElevenLabs credentials are needed for TTS; `ffmpeg` must be on PATH for
everything else.

## License

MIT
