---
name: braidio
description: >
  Use `braidio` to produce narrated commentary audio — a podcast-style episode
  that talks *about* something (a song, a book, a film, a paper, an event) and
  weaves in clips of the source. Trigger on requests like "make a podcast-style
  commentary", "turn this document/article/paper into an episode", "narrate over
  these clips", "two hosts discussing this", "duo commentary track", "panel
  discussion audio", "roundtable about this", "debate two sides of this", "audio
  essay", "documentary voice-over", "Song Exploder style breakdown",
  "interview-style audio", "deep dive on this track", "add a narrator over this
  song", "voice this script with two speakers", "commentary with clips of the
  original". Covers: authoring the Script of beats, picking a ready-made format
  template (solo / deep-dive / interview / panel / debate / documentary VO),
  estimating the ElevenLabs cost before paying, pulling source audio into an
  asset library, and rendering the mixed episode. For plain audio/video editing
  (trim, fade, concat, transcribe, dub) use `mixing` instead; braidio is the
  layer that *composes a production*.
---

# braidio — make a commentary production

braidio braids **talk** (synthesized narration or a multi-voice exchange) with
**clips of source media** into one mixed audio file. You write the words; braidio
casts the voices, cuts the clips, ducks them under the speech, normalizes
loudness, and masters the result.

Two ways in — same model underneath:

| You have | Use |
|---|---|
| the **braidio MCP connector** (tools named `help`, `render_format`, … or namespaced `braidio_*`) | the tool path below |
| the **Python package** (`pip install braidio`, ffmpeg on PATH, `ELEVENLABS_API_KEY` set — or pass `api_key=` to any render, `weave_project` included) | the Python path below |

**Synthesis costs real money** (ElevenLabs, billed per character). Always
`estimate_cost` first, and ask the user about voice / format / length before
spending if any of it is unclear.

## Shortest path (MCP tools)

```
1. create_project("my-episode")                     # free
2. ingest_document("my-episode", uri="https://…")   # free — returns the text to analyze
   #   (or text="…" for pasted content; as_beats=true on markdown returns a ready
   #   Script of narration beats to tag — braidio.script_from_markdown in Python)
3.  … you read that text and author a Script (see below) …
4. estimate_cost(script)                            # free — always do this
5. render_format("deep_dive", script)               # [COSTED] → an mp3 url in your workspace
```

That is the whole happy path for a narration-only episode. Variants:

- **Want provenance + partial re-render?** `save_script(project_id, script)`
  (free) then `weave_project(project_id, script)` (costed). Re-running on the
  same project re-ingests: beats are matched by position, unchanged ones reuse
  their renders, and a changed beat, format or `profile` is what gets
  re-synthesized — so changing the rights profile is a re-run, not a new
  project. Every beat type, dialogue included: a dialogue beat renders in one
  pass under the format's cast, and changing the format recasts (and
  re-renders) only the dialogue beats. Both take the same optional `format_id`,
  `bed_asset_id` and `sting_asset_id` as the one-shot renders, so a graph
  episode gets its format's defaults, its cast, its music bed and its scene
  stings.
- **Want no project at all?** Skip step 1–2 and call `render_production(script)`
  or `render_format(format_id, script)` directly.
- **Just one voice reading one text?** `narrate(text)` — the smallest costed call.
- **Just a two-person exchange?** `render_dialogue([["A","…"],["B","…"]])`.

Discovery tools, all free: `help`, `list_formats`, `list_presets`,
`list_deliveries`, `list_voice_pools`, `describe_genre`.

## Shortest path (Python)

```python
import braidio
from braidio import Script, Narration, SegmentBeat, Dialogue, DEEP_DIVE

script = Script(
    title="What makes this song work",
    id_slug="01",
    beats=[
        Dialogue(
            (
                ("host_a", "The thing that gets me is the bass."),
                ("host_b", "Say more — it's doing something odd, right?"),
            )
        ),
        Narration("Here's the passage in question —"),
    ],
)

print(braidio.estimate_cost(script).summary)  # "110 chars → $0.0330"
braidio.render_format(DEEP_DIVE, script, source=None, out_path="episode.mp3")
```

`source` is required as a keyword; pass `source=None` when the script has no
segment beats. For full manual control use `braidio.render_production(script,
source=…, cast=…, config=…, delivery=…, out_path=…)`.

## The Script — an ordered list of beats

Four beat types (the `scene_break` is under *Structural music* below). Order
is the timeline.

| Beat | Python | JSON (MCP) |
|---|---|---|
| **Narration** — one voice reading | `Narration(text, voice=…, voice_settings=…, style=…, lead_gap_s=…, published_text=…)` | `{"type": "narration", "text": "…"}` |
| **Dialogue** — a multi-speaker exchange, synthesized in ONE pass so it sounds like people talking to each other | `Dialogue(turns=(("A","…"),("B","…")), label=…)` | `{"type": "dialogue", "turns": [["A","…"],["B","…"]]}` |
| **Segment** — a span of source media to cut in | `SegmentBeat(reference, label=…, rights=…, placement=…, published_substitute=…)` | `{"type": "segment", "reference": "…", "rights": "owned-local"}` |

The envelope is `{"title": …, "id_slug": "01", "beats": [...]}`. `id_slug` is a
short stable id used to name the render.

### Two voices in one production — the commentary and the record

**Trigger: your script contains both your own argument *and* a documented
record** — dates, names, sourced quotes, a passage from a book, the findings of
research someone else did. The moment those two kinds of material sit in one
timeline, give the record its own voice. The handoff is what tells a listener
"this is no longer the essayist talking", and it does that work without a single
word of signposting.

```python
PRESENTER = "iP95p4xoKVk53GoZ742B"  # the spine: your argument
RECORD = "nPczCjzI2devNBz1zQrb"  # deep, level: the documented record

Narration(claim, voice=PRESENTER)  # format's delivery
Narration(
    dates_and_quotes,
    voice=RECORD,
    style="archive",
    voice_settings=braidio.V3_NARRATOR.voice_settings,
    lead_gap_s=0.55,
)
```

**The contrast has to come from the `voice=`, not from the settings.** Two
voices measured across a real episode: presenter 127-134 Hz median f0, record
95-103 Hz — about four semitones, unmistakable. Reaching for `voice_settings`
instead buys you almost nothing (see *Making it sound alive* below).

Three details that make it read as deliberate rather than accidental:

- `lead_gap_s=0.5-0.6` entering each record block — the beat of silence is the
  handoff.
- `style="archive"` (any label) makes the renderer report those beats as their
  own kind in the returned timeline, so you can check the balance afterwards.
- Write the record voice *differently*: declarative, chronological, no
  rhetorical questions, a much sparser tag budget. Same voice id reading the
  presenter's prose does not sound like a different role.

`placement` on a segment beat is the weaving grammar:

- `"before"` (default) — set it up, then play the clip clean.
- `"after"` — play the clip clean, then react to it.
- `"under"` — the clip plays **concurrently beneath the next talk beat**, ducked.
  Put the segment beat immediately before the talk it should sit under.

## Format templates — start here, don't hand-roll

Each preset bundles the cast (role → voice), the narration voice + delivery, and
the mix defaults. `render_format(format_id, script)` / `braidio.render_format(FMT,
script, source=…)`.

| `format_id` | Standard name | Shape |
|---|---|---|
| `solo_explainer` | Solo-Presenter Explainer (video/audio essay, close reading) | one presenter + exhibits |
| `deep_dive` | Two-Host Conversation ("Deep Dive") | two hosts; one teaches, one probes |
| `interview` | Interview (host + guest) | Q→A, the guest is the centre |
| `interview_host_removed` | Song Exploder-style (host removed) | guest monologue; each claim illustrated by its isolated stem; full artifact at the tail |
| `panel` | Panel / Roundtable | a moderator routes distinct voices |
| `debate` | Debate (Oxford-style) | proposition / opposition / moderator, phased |
| `documentary_vo` | Documentary Voice-Over ("Voice of God") | narrator on top; interviews + clips beneath |

Each `Format` also carries **authoring guidance** — `roles`, `scripting`, the
recommended `clip_placement` and `music_bed` intensity. Read it (`list_formats`,
or the `Format` object's `scripting` field) *before* writing the script: it tells
you the shape the format expects (cold open → walkthrough → recap; state the
motion up front; strip the host's questions; …). Use the role names from the
format's cast as your Dialogue turn roles.

## Your defaults, and how to change them

Out of the box braidio renders on **`eleven_v3` at the expressive end** — every
shipped format, not just the solo ones. That is deliberate: the previous default
(`eleven_multilingual_v2`) cannot render `[audio tags]` *at all*, so it capped
every production at the flat end before an author wrote a word.

Resolution order, highest first:

| | |
|---|---|
| `render_production(delivery=…)` | a `Delivery`, or a name like `"v3-narrator"` |
| `$BRAIDIO_DELIVERY`, `$BRAIDIO_VOICE_ID` | per-shell |
| `~/.config/braidio/config.json` | **your persisted default** |
| the package default | `v3-presenter` |

```json
{
  "delivery": "v3-presenter",
  "voice_id": "iP95p4xoKVk53GoZ742B",
  "voice_settings": {"stability": 0.0, "use_speaker_boost": true}
}
```

Every key is optional. `braidio.describe_defaults()` prints what is in force and
where each part came from — reach for it first when a render does not sound the
way somebody expected, because the usual answer is a config file they forgot
they wrote.

## Making it sound alive (read this before writing a word)

The commonest failure of a braidio script is not a wrong fact or a clumsy
sentence. It is that the finished thing is **boring to listen to** — an
even, unvarying read that puts people to sleep. This is a solved problem and
the solution is counter-intuitive, so here are the measurements. One sentence,
one voice, pitch range (f0 p5-p95) as the proxy for liveliness:

| lever | effect |
|---|---|
| **plain text → densely tagged text** | 70.8 Hz → **124.4 Hz** (+76%) |
| **voice choice** (a warm read → a vivid one) | 124 Hz → **199 Hz** |
| `voice_settings["stability"]`, across its **whole** range 0→1 | 85 Hz → 72 Hz (−15%) |

**Tag density is the engine. `stability` is nearly noise.** A production that
varied stability between 0.30 and 0.65 to get "four registers" came out
uniformly flat — those two numbers are worth about 2 Hz of pitch range. Its
script carried ten tags in 1371 words. The listener's word was *somniferous*.

So:

1. **Write the performance into the text.** Target **2–5 inline `[audio tags]`
   per 100 words** and vary which one: `[laughs]` `[sighs]` `[dryly]`
   `[incredulous]` `[excited]` `[quietly]` `[rushed]` `[slowly]` `[pause]`
   `[beat]`. Tags only fire on a v3 delivery — on `eleven_multilingual_v2` they
   are read out as literal text.
2. **Gate it.** `braidio.audit_expressiveness(prose)` returns `[]` when you are
   in the band, and tells you which way you are out otherwise;
   `braidio.audio_tag_rate(prose)` is the bare number.
3. **Pick a voice with range.** `list_voice_pools` / the ElevenLabs voice list;
   a "warm storyteller" voice is a lovely thing to fall asleep to.
4. **Then leave `stability` alone.** The v3 presets already sit at the
   expressive end.

**What you *do* ration is the designed epigram.** A first draft once landed a
turn of phrase at the end of every beat — 8 in 338 words. No speaking human
sustains that, and it is the relentlessness that reads as machine-written.
Budget **≤2 per 3 minutes** and let the rest be plain talk with contractions,
fragments and uneven sentence length. `audit_platitudes` catches recycled tics,
not over-writing; it cannot be the only gate.

The two failures pull in opposite directions and you need both gates:
under-tagged and over-written is the classic AI read.

## Getting the words — lyrics, annotations, transcripts

braidio composes a production; it does **not** fetch the material you are
commenting on. That acquisition is the caller's job, exactly as image retrieval
is (see the `braidio-commentary-video` skill's *Images* section). Do not invent
the source material and do not type a lyric in by hand.

| you need | use |
|---|---|
| **word-level timings** for a recording | `mixing.transcribe(path, timestamps_granularity="word", cache=True)` — ElevenLabs Scribe. Disk-cached, so re-runs are free |
| **line-level timings** from those words | walk the measured words against the *known* line structure; a greedy match fixes ASR errors because you already know the words |
| **lyrics + per-line annotations** | the Genius acquisition path (`GeniusClient` / `acquire_song` / `extract_lyrics`, currently in `$PP/misc/Hamilton/hamilton_genius`, tokenless path works) |
| **synced lyrics** where they exist | LRCLIB (`fetch_synced_lyrics` / `parse_lrc` in the same place) |
| **the background of a *recording*** | ordinary web research — see the warning below |

⚠️ **Annotation sources describe the WORK, not the RECORDING.** Genius will tell
you what a lyric means and nothing at all about the circumstances of a
particular take. On one real episode the Genius entry for the studio version
carried nine annotations and the entry for the live version carried **zero** —
while the live recording's story (a free reunion concert in front of half a
million people) was the entire reason the episode was worth making. Always ask
separately: *who made this specific recording, when, and under what
circumstances?* That research is usually where the story is.

⚠️ **Scribe's `audio_event` tags are evidence, not decoration.** `[crowd
cheering]`, `[laughter]`, `[applause]` come back with timestamps, and they can
confirm — or refute — a claim you were about to make about a recording.

## Bringing in source clips

A segment beat needs a **source**: time-aligned lines plus the media file to cut
from.

MCP, in order:

1. `download_audio(url)` for a song/video *page* link (YouTube, SoundCloud,
   Bandcamp…) — pulls the audio into your asset library and returns a
   ContentRef whose `itemId` is the handle. `upload_asset(uri=… | data_b64=…)`
   for a direct file URL or inline bytes. `list_assets` / `get_asset` to manage
   them.
2. Pass the source alongside the script:
   `{"asset_id": "<itemId>", "lines": [{"index": 0, "start_s": 0.0, "end_s": 4.2, "text": "…"}]}`
   (a server-local path goes in `asset_path` instead).
3. Each `SegmentBeat.reference` is matched against those lines by token-F1, so a
   paraphrase or a partial quote still resolves. `find_segment(lines, quote)` is
   free — use it to check a reference resolves before paying for a render.

Python: build a `braidio.TimedLineSegmentSource(lines=[TimedLine(...)],
asset_path="song.mp3")` and pass it as `source=`.

⚠️ Downloading audio grants no rights. Only pull material the user is entitled to
use, and set each segment beat's `rights` honestly.

## Knobs worth knowing

- **Delivery** (`list_deliveries`) — the speaking register, and mostly a
  question of *which model*. Anything on `eleven_multilingual_v2` cannot render
  `[audio tags]` **at all**, which caps how alive it can sound; the `v3-*`
  presets can. Within one model the preset differences are small — see *Making
  it sound alive*. `v3-presenter` (the spine) and `v3-narrator` (the record) are
  the contrast pair, and the contrast that matters is the **voice id** you pass
  alongside them.
- **Per-beat `voice_settings` on a v3 delivery** — only `stability` does
  anything. `style`, `similarity_boost` and `speed` are accepted and **silently
  ignored**, so copying a `V2_*` preset's settings onto a v3 format quietly
  drops most of what you thought you were asking for.
- **Voice pools** (`list_voice_pools`) — `"four"` and `"many"`, for narration
  that cycles voices across segments (`render_multivoice`, `assign_voices`).
- **WeaveConfig presets** (`list_presets`) — `single_narrator`,
  `documentary_lyrics`, `panel_4`, `interview_many`: casting, turn lengths,
  pacing, clip pre/post-roll, duck depth, loudness targets.
- **Music bed** — an instrumental laid under the whole production, ducked.
  braidio ships no music: pass your own asset (`bed_asset=` to
  `braidio.render_format`, or a `MusicBed(...)`).
- **Structural music** — a `scene_break` beat is marked with a short **sting**
  (your asset, `sting_asset_id` / `sting_asset=`) or a beat of silence, and a
  segment beat's `spotlight` drops the bed out so the exhibit lands in silence.
  Both render paths carry it: the one-shot renders and the graph pipeline
  (`save_script` / `weave_project`, which records the decision as provenance).
- **Rights profile** — `"personal"` plays everything; `"published"` drops or
  substitutes non-publishable clips and beats carrying forbidden verbatim text.
  `plan_production(script, profile)` is a free dry run showing exactly what would
  be dropped; `content_violations(script, forbidden)` scans for leaks.
- **Writing helpers, all free** — `clean_text` (fix OCR ligatures, drop leaked
  speaker labels before TTS), `audit_platitudes` (flags recycled rhetorical tics
  and a per-1000-words rate), `narration_segments`, `build_timeline`.

## Gotchas

- **Estimate before you render.** The costed tools are `narrate`,
  `render_dialogue`, `render_multivoice`, `compose_narration`,
  `render_production`, `render_format`, `weave_project`. Everything else is free.
- **Dollars are a rate estimate, characters are exact.** If the connector reports
  an unpriced cost (`null` dollars), that means the rate is unconfigured — not
  that it is free. Quote the character count in that case.
- **Dialogue turn roles must be the cast's role names.** With a `format_id`
  that is the format's cast (`deep_dive`: `host_a` / `host_b`; `interview`:
  `host` / `guest`; see `list_formats`); without one it is the default cast's
  `A` / `B`. `save_script` and `weave_project` check this before writing
  anything and name the roles the cast has; `render_production` /
  `render_format` would fail inside the paid call instead.
- **A segment beat with no source is an error, not a silent skip.** Narration-only
  scripts need no source at all.
- **Text-to-Dialogue is one request**: keep a single `Dialogue` beat's turns under
  roughly 2000 characters total, and split long exchanges into several beats.
- **eleven_v3 is nondeterministic.** Renders are cached on disk, so an unchanged
  exchange re-renders instantly and identically — but that freezes one random
  take. Pass a `seed` for reproducibility, or `refresh=True` to re-roll.
- **Repeated renders of unchanged text are free in reality** (disk cache) but the
  one-shot tools still report the rate estimate — a deliberate over-estimate.
- **ffmpeg must be on PATH** for every render path.
- **The dialogue has to be written disfluent.** The model will not invent
  backchannels, interruptions or fragments; if you want it to sound like people
  talking, write it that way — contractions, ellipses, half-sentences.
- **Renders land in the caller's own workspace** (MCP) and are returned as a
  url/path; audio bytes are never inlined in a tool result.
