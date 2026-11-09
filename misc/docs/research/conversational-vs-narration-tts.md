# Making TTS Sound Like Conversation, Not Narration

**A research + implementation guide for braidio (ElevenLabs, `mixing.text_to_speech`)**

_Last updated: 2026-07-22. Scope: ElevenLabs `eleven_multilingual_v2` and `eleven_v3` (audio tags), current as of the model's GA API release (Aug 2025) and after._

---

## Executive summary — the highest-impact changes to stop sounding "narrated"

braidio currently synthesizes **every line as an independent single-voice call** to `eleven_multilingual_v2` with narration-tuned settings (`stability: 0.5, similarity_boost: 0.8, style: 0.0`), then stitches. That pipeline **structurally cannot** produce conversation: `multilingual_v2` has no audio-tag or disfluency vocabulary, and per-line synthesis means no speaker ever *reacts* to another — prosody can't carry across a turn boundary because each turn is generated in isolation. The result is alternating monologues [1][6][12].

The five changes that matter most, in priority order:

1. **Use a different model *and* API for the conversational register.** Route conversational passages through **ElevenLabs Text to Dialogue** (`POST /v1/text-to-dialogue`, `eleven_v3` only), which synthesizes a *whole multi-speaker exchange in one pass*, "matching prosody, emotional range and taking cues from audio tags" so speaker B's delivery is conditioned on speaker A's turn [1][2][7]. This single change does most of the work — it is the mechanism NotebookLM/DeepMind use (single autoregressive pass over the whole dialogue, not line-by-line) [8][9].

2. **Manufacture disfluency in the *script*, deliberately.** The single most-cited reason NotebookLM sounds human: after generating a clean script, a dedicated stage *adds* "the banter and the pauses and the likes… because you cannot listen to two robots talking to each other" [8][9][15]. Write backchannels (`right`, `mm-hm`, `yeah`, `exactly`), reactive fragments, self-interruptions (em-dash), contractions, and trailing `…` into the copy. Do **not** rely on the model to invent them.

3. **Loosen the voice settings for the conversational cast.** Narration wants steady (`Natural`/`Robust` stability, low `style`); conversation wants variability — v3 **Creative** or **Natural** stability, so audio tags and reactive prosody actually fire [3][4][14].

4. **Pick conversational-labelled voices, not narrator voices.** ElevenLabs' library separates `conversational` voices ("relaxed yet confident… dynamic co-host style") from `narrator`/`audiobook` voices; the timbre and default cadence differ [10][11].

5. **Engineer turn-taking at the audio layer only where the model can't** — tight inter-turn gaps and short (~150–400 ms) overlaps on *backchannels/interjections only*. Overlapping content words turns to mush [8][12]. This is where braidio's weave-engine `overlap` parameter earns its keep.

Keep the **narration register exactly as it is** (`multilingual_v2`, steady settings) — it is already correct for a single consistent reading voice. The work is entirely in adding a *second* register, not changing the first.

**Reliable now:** Text to Dialogue + audio tags on `eleven_v3`; scripted disfluencies; conversational voice selection; settings changes; audio-layer gap/overlap engineering. **Experimental:** exotic sound-effect/environment tags; precise overlap *timing* from tags alone; deterministic reproducibility (v3 is nondeterministic even with a seed) [2][5].

---

## 1. What makes TTS sound "narrated" vs "conversational"

Read/narrated speech and spontaneous conversational speech differ measurably and consistently — speech-recognition and style-classification research treats the distinction as robust enough to *classify* audio by it [12][13][19].

| Dimension | Narration (read) | Conversation (spontaneous) |
|---|---|---|
| **Speaking rate** | Even, moderate | Faster and more *variable*; bursts + slowdowns [12][19] |
| **Pitch (F0)** | Controlled, gradual contours, wider planned arcs | Wider *local* range, uptalk on questions, sharp reactive jumps [12][19] |
| **Pausing** | Regular, longer, at clause boundaries (~500–700 ms) | Shorter gaps between turns (~200 ms), *filled* pauses ("uh", "um"), mid-clause breaks [12][19] |
| **Fluency** | Fluent, complete sentences | Disfluencies: repairs, restarts, partial words, hesitations, repetitions [12][19] |
| **Turn structure** | One voice, monologue | Interactivity: speaker turns, **overlap**, backchannels [12][13] |
| **Non-verbal** | Rare | Laughter, breath, sighs, "mm-hm", gasps [8] |
| **Sentences** | Complete, well-formed | Fragments, incomplete thoughts, self-interruption |

**What current ElevenLabs TTS can and cannot produce:**

| Feature | `eleven_v3` | `eleven_multilingual_v2` |
|---|---|---|
| Laughter, sighs, breath, throat-clear | Yes — `[laughs]`, `[sighs]`, `[exhales]`, `[clears throat]` [4][5] | No |
| Emotional reactivity (`[excited]`, `[sarcastic]`, `[annoyed]`) | Yes [3][5] | No (only global stability/style) |
| Whisper / shout / deadpan delivery | Yes — `[whispers]`, `[shouts]`, `[deadpan]` [3][5] | No |
| Interruptions / overlap cues | Yes — em-dash + `[interrupting]`/`[overlapping]` [4] | No |
| Uptalk on questions | Partial (punctuation-driven; stronger in v3) | Weak |
| Filler words ("um", "like") | Rendered if **written into the text**; not auto-inserted [8][9] | Rendered as literal text, flatly |
| Cross-turn prosody (B reacts to A) | **Yes, via Text to Dialogue** (single pass) [1][2] | No |
| Steady, consistent long read | Possible (Robust) but overkill | **Yes — its strength** [14] |

The decisive gap is the last row. Backchannels and disfluencies you can *write*; but a speaker sounding like they *heard* the other speaker requires the turns to be generated together — which only Text to Dialogue (or a NotebookLM-style single-pass model) does [1][2][8].

---

## 2. ElevenLabs conversational features

### 2.1 Models (choose per register)

| Model ID | Latency | Audio tags | Multi-speaker | Best for | Role in braidio |
|---|---|---|---|---|---|
| `eleven_v3` | ~1–2 s (not real-time) | **Yes** | **Yes (Text to Dialogue)** | Storytelling, character voices, drama, dialogue [14] | **Conversational register** |
| `eleven_multilingual_v2` | ~1–2 s | No | No | Audiobooks, podcasts, narration [10][14] | **Narration register (keep)** |
| `eleven_turbo_v2_5` / `eleven_flash_v2_5` | ~75 ms | No | No | Real-time agents/chatbots [14] | Not needed (braidio is offline) |

**Update 2026-11-07 (braidio#210):** `eleven_v4` (released 2026-09-28) is now ElevenLabs' current model and `eleven_v3` is listed as "previous generation". v4 reads the same audio tags as v3 ("more reliably"), supports no SSML, has stability and similarity only (no style, no speed), takes 10,000 characters per request and accepts `language_code` [ElevenLabs, Eleven v4](https://elevenlabs.io/docs/overview/capabilities/text-to-speech/eleven-v4); [models](https://elevenlabs.io/docs/overview/models). braidio ships it as the `v4-presenter` / `v4-narrator` deliveries; the default stays `v3-presenter` for now. A one-take check of the v3 "tags are the engine, stability is noise" table on v4 (one voice, one sentence, pitch range f0 p5–p95 via librosa pyin) did not reproduce it: plain 215 Hz, tagged 201 Hz at stability 0.5; tagged at stability 0.0 / 0.5 / 1.0: 169 / 201 / 197 Hz; v3 tagged at 0.0: 379 Hz. With one take per cell and v4's take-to-take variance, this is inconclusive. It says only that the v3 numbers should not be quoted for v4, and that a listening comparison should decide the default. Explicit pauses longer than a tag can give are SSML breaks, which braidio turns into exact silence on every model (via `voxy.pauses`).

`eleven_v3` reached **general availability with public API access on 20 August 2025** (`model_id: "eleven_v3"`) [15][16]. It is explicitly **not** recommended for real-time use (higher latency, needs prompt engineering) — which is fine for an offline podcast renderer.

### 2.2 Text to Dialogue — the key conversational primitive

**Endpoint:** `POST https://api.elevenlabs.io/v1/text-to-dialogue` (only on `eleven_v3`) [2][7].

It "weaves multiple voices together to create a seamless interaction… matching prosody, emotional range and taking cues from audio tags" [1] — i.e. it is a *dialogue*-native call, not a loop over Text to Speech.

**Request body** [2]:

```json
{
  "inputs": [
    { "text": "[giggling] Knock knock", "voice_id": "JBFqnCBsd6RMkjVDRZzb" },
    { "text": "[curious] Who is there?", "voice_id": "Aw4FAjKCGjjNkVhN1Xmq" }
  ],
  "model_id": "eleven_v3",
  "settings": { "stability": 0.5 },
  "seed": 12345
}
```

- `inputs`: array of turns, each `{ text, voice_id }`. **No limit on number of speakers**; audio tags go *inside* each turn's `text` [1][2].
- Optional: `model_id` (default `eleven_v3`), `language_code`, `settings`, `seed` (0–4294967295; still nondeterministic), `apply_text_normalization` (`auto`/`on`/`off`), `pronunciation_dictionary_locators` (≤3).
- Query: `output_format` (e.g. `mp3_44100_128`), `enable_logging`.
- **Hard limit: total of all `inputs[].text` ≤ 2000 characters per request** [2] — long exchanges must be chunked (see §4).
- Response: binary audio (the *whole* exchange as one clip).

This is a **new function** braidio must add — the existing `text_to_speech` hits `/v1/text-to-speech/{voice_id}` (single voice) and cannot express turns.

### 2.3 Audio tags useful for conversation

Tags are natural-language directives in `[square brackets]`, placed **immediately before the span they affect**; the `voice_id` still picks the speaker while the tag guides delivery [1][5]. Reliability tiers (v3 was an alpha research preview — "requires more prompt engineering than previous models" [5]):

**Reliable — non-verbal & reactions:** `[laughs]`, `[laughs harder]`, `[starts laughing]`, `[chuckles]`, `[giggles]`, `[sighs]`, `[exhales]`, `[gasps]`, `[clears throat]`, `[gulps]` [4][5].

**Reliable — emotion:** `[excited]`, `[curious]`, `[sad]`, `[angry]`, `[happily]`, `[nervous]`, `[annoyed]`, `[flustered]`, `[sarcastic]`, `[mischievously]`, `[crying]` [3][5][17].

**Reliable — delivery direction:** `[whispers]`, `[shouts]`/`[shouting]`, `[deadpan]`, `[flatly]`, `[cheerfully]`, `[playfully]`, `[pauses]`, `[hesitates]`, `[stammers]`, `[drawn out]`, `[rushed]` [3][5].

**Useful but less deterministic — dialogue dynamics:** `[interrupting]`, `[interrupts]`, `[cuts in]`, `[overlapping]`, `[overlapping speech]`, `[interjecting]`, `[starting to speak]` [4]. These *influence* timing but do not guarantee sample-accurate overlap (see §4).

**Experimental / voice-dependent — accents & SFX:** `[strong French accent]`, `[strong Texas accent]`; `[applause]`, `[clapping]`, `[gunshot]`, `[explosion]`, `[leaves rustling]` [1][5]. Third-party "1450+ tag" catalogues adding `[forest ambient]`, `[night dreamy]`, `[noir]`, etc. are **aspirational, not officially supported** — treat as experimental [18].

**Two critical caveats:**
- **Tags must match the voice's character.** A tag like `[giggles]` on a grave "documentary narrator" voice is often ignored or sounds wrong; the voice's own range bounds what tags do [5].
- **Stability gates tags.** On `Robust`, directional tags are largely ignored (behaves like v2); use `Creative`/`Natural` for tags to fire [3][14].

### 2.4 Voice selection: conversational vs narrator

ElevenLabs' library is explicitly split. **Conversational** voices are "relaxed yet confident… clear, personable… dynamic co-host style" [10]; **narrator/audiobook** voices are "authoritative… professional-level pacing" for long-form reading [11]. `list_voices()` returns a `labels` dict with `use_case` (`conversational`, `narration`, etc.) and `description` — filter on these. For the two-host cast, pick **two contrasting conversational voices** (e.g. different gender/age/pitch) so turn-taking is audible; for narration keep one steady narrator voice.

### 2.5 Voice settings per feel

| Setting | Narration (steady) | Conversation (loose) |
|---|---|---|
| `stability` (v2, 0–1) | ~0.5 (current) | ~0.3 — varied & expressive [14] |
| v3 stability **mode** | `Natural` or `Robust` | **`Creative`** (most emotional; hallucination-prone) or `Natural` (balanced) [3][14] |
| `similarity_boost` | ~0.8 (current) | ~0.75 (library sweet spot) [14] |
| `style` (0–1) | 0.0 (current) | 0.1–0.4 if the voice needs a nudge; start 0.0 [14] |
| `speed` | 1.0 | 1.0–1.1 (conversation is faster/looser) [12] |
| `use_speaker_boost` | true | true |

v3 exposes stability as three discrete modes: **Creative** ("more emotional and expressive, but prone to hallucinations"), **Natural** ("closest to the original voice recording — balanced and neutral"), **Robust** ("highly stable, less responsive to directional prompts… similar to v2") [3]. Via the API these correspond to `stability` values (roughly Creative≈0.0, Natural≈0.5, Robust≈1.0) — lower = more expressive.

---

## 3. Scripting techniques for natural dialogue

This is where braidio has the most leverage, and it's mostly **writing**, not TTS config. The DeepMind/NotebookLM pipeline is the template: generate a clean script, then a dedicated pass **injects** disfluency and banter before synthesis [8][9]. braidio should do the same — a "conversationalize" transform on the commentary text before it hits Text to Dialogue.

**Rules for writing two-person commentary so v3 renders it conversationally:**

1. **Short turns.** Break long commentary into 1–2 sentence turns that ping-pong. Long turns re-collapse into narration.
2. **Contractions everywhere.** "it is" → "it's", "that is" → "that's". Formal orthography reads as reading.
3. **Reactive fragments that reference the other speaker.** `[laughs] Right, and that "ten-dollar founding father" line—` Fragments + a callback to what was just said = the model (and listener) hears a *response*.
4. **Self-interruption with em-dashes.** `So the whole verse is basically— [interrupting] —a mission statement, yeah.` Em-dash is the reliable interruption/cutoff signal across v3 docs [4][5].
5. **Trailing hesitation with ellipses.** `I mean… it's ambitious, right?` Ellipses create natural pauses [5].
6. **Backchannels as their own micro-turns.** Insert `right`, `mm-hm`, `yeah`, `exactly`, `wait—`, `huh` as short turns from the *listening* speaker. In Text to Dialogue these become genuine reactions; at the audio layer they can be laid *under* the other voice (§4).
7. **Questions that hand off (uptalk).** End a turn on a real question so the next speaker answers: `…but does that hold up by the second verse?` Question marks drive rising intonation [5].
8. **CAPS for emphasis, sparingly.** `That's the WHOLE point.` Capitalization adds stress [5].
9. **Emotion/reaction tags at turn starts, matched to the voice** (§2.3). `[excited]`, `[skeptical]`, `[laughs]`.
10. **Give ≥250 characters of context per generation.** v3 is unreliable on very short isolated prompts; batching the exchange into one Text to Dialogue call (rather than tiny per-line calls) also satisfies this [3][5].

**How much to script vs. leave to the model:** Script the *structure and disfluencies explicitly* (turns, fragments, em-dashes, backchannels, tags) — the model will not reliably invent them, exactly as Google found necessary [8][9]. Leave *fine prosody* (pitch contour, micro-timing, breath) to v3 — over-tagging every word causes hallucination on `Creative` [3][5]. Rule of thumb: **1 tag per turn**, at the start, plus punctuation.

**Worked example** (same beat, three registers):

- _Narrated (current output):_ "Hamilton describes himself as a ten-dollar founding father without a father. The line establishes his ambition and his outsider status."
- _Conversational script for Text to Dialogue:_
  ```
  Host A: [amused] Okay, "ten-dollar founding father without a father"—
  Host B: [jumping in] —he's putting his own face on the money in line one.
  Host A: Right? [laughs] It's a flex AND an origin story.
  Host B: Exactly. And "without a father"… that's the whole outsider thing in four words.
  Host A: Mm-hm.
  ```

---

## 4. Overlap / turn-taking realism at the audio layer

Two ways to get overlap, with different fidelity:

**(a) Let the model do it (preferred).** Because Text to Dialogue renders the whole exchange in one pass, scripting `—` + `[interrupting]`/`[overlapping]` makes the model produce genuine crosstalk with correct prosody — the same mechanism as DeepMind's SoundStorm single-pass dialogue synthesis, which handles "overlapping speech, natural pacing" learned from annotated multi-speaker data [4][8]. **Limitation:** you cannot *dial* the overlap amount; it's whatever the model does. Good for realism, weak for control.

**(b) Engineer it at the stitch layer (braidio's weave engine).** When you must synthesize turns/chunks separately (e.g. exchange > 2000 chars, or you want a controllable `overlap` knob), assemble with these audio-layer moves:

- **Tighten inter-turn gaps.** Conversation gaps ≈ 200 ms vs narration pauses ≈ 500–700 ms [12][19]. Just closing gaps is the cheapest single audio-layer win.
- **Short, targeted overlaps only.** Overlap the *tail* of turn A under the *head* of turn B by ~**150–400 ms**, and **only** for backchannels/interjections — never overlap two streams of content words, which reads as mush [8][12]. This maps directly onto braidio's `overlap` parameter: cap it, and gate it to interjection turns.
- **Backchannel bed.** Render `mm-hm`/`right`/`yeah` as separate low-level clips and mix them *under* the speaking turn at −8 to −12 dB, offset a beat in — this is the acoustic signature of "someone is listening" and is very cheap to add.
- **Crossfade at the seam** (10–30 ms) so stitched turns don't click.

**Relation to NotebookLM style:** the two-host "deep dive" feel comes from (1) a script with manufactured banter/disfluency and (2) single-pass multi-speaker synthesis that lets prosody flow across turns and produces natural overlap [8][9][20]. braidio can approximate (2) with Text to Dialogue and, where it stitches, approximate the *timing* portion with (b). Doing only per-line TTS + fixed gaps reproduces neither — hence "narrated."

---

## 5. Per-register parameterization — concrete recommendation

Expose **two named registers** in braidio's config, each a self-contained bundle (SSOT — one dict per register, no scattered magic numbers):

```python
# Narration register — keep as-is (a single steady reading voice)
NARRATION = {
    "model_id": "eleven_multilingual_v2",
    "api": "text_to_speech",  # existing /v1/text-to-speech/{voice_id}
    "voice_selection": {"use_case": "narration"},  # ONE voice, held constant
    "voice_settings": {
        "stability": 0.5,
        "similarity_boost": 0.8,
        "style": 0.0,
        "use_speaker_boost": True,
    },
    "scripting": "verbatim passage, complete sentences",
    "timing": {"inter_gap_ms": 600, "overlap_ms": 0},
}

# Conversation register — the new one
CONVERSATION = {
    "model_id": "eleven_v3",
    "api": "text_to_dialogue",  # NEW /v1/text-to-dialogue (whole exchange, one call)
    "voice_selection": {
        "use_case": "conversational",
        "cast_size": 2,
    },  # 2+ contrasting voices
    "voice_settings": {
        "stability": 0.35,
        "similarity_boost": 0.75,
        "style": 0.3,
        "use_speaker_boost": True,
    },  # v3: Creative/Natural
    "scripting": "short turns, contractions, em-dash interruptions, "
    "ellipsis hesitations, 1 audio-tag/turn, backchannel micro-turns",
    "timing": {
        "inter_gap_ms": 200,
        "overlap_ms": 250,  # only on interjections
        "backchannel_bed_db": -10,
    },
    "chunk_char_limit": 2000,  # Text to Dialogue hard limit → chunk longer exchanges
}
```

Implementation notes:
- Add a `text_to_dialogue(turns, *, voices, model_id="eleven_v3", settings, seed, output_format, ...)` function alongside `text_to_speech` in `mixing/dubbing/tts.py`, POSTing to `/v1/text-to-dialogue` with the `inputs` array. Reuse the existing cache keying (include the full turn list + voice map + settings in the SHA-256).
- braidio's weave engine keeps its `overlap` parameter but **gates it to the conversation register and to interjection turns only**, capped at ~400 ms.
- The "conversationalize" script transform (adding disfluencies/backchannels/tags to clean commentary) is a separate, testable text→text step — mirror NotebookLM's dedicated disfluency stage [8][9].

### Experiment matrix (A/B on one short two-person exchange)

Use the same ~5-turn beat (e.g. the "ten-dollar founding father" example above) for every variant. Judge on a blind 1–5 "sounds like two people talking, not reading" scale.

| # | Model / API | Voices | Settings | Scripting | Timing / overlap | What it isolates |
|---|---|---|---|---|---|---|
| **V0** _control_ | `multilingual_v2`, per-line TTS | 2 narrator voices | stab 0.5, style 0.0 | plain sentences | fixed 600 ms gaps, no overlap | Current braidio baseline |
| **V1** | `multilingual_v2`, per-line TTS | 2 **conversational** voices | stab **0.3**, speed 1.05 | plain sentences | gaps **200 ms** | Do voice + settings + tighter gaps alone help? |
| **V2** | `multilingual_v2`, per-line TTS | 2 conversational | stab 0.3 | **disfluencies + backchannels scripted in** | 200 ms gaps + **250 ms overlap on interjections + backchannel bed −10 dB** | Value of *scripting + audio-layer overlap* without v3 |
| **V3** | **`eleven_v3`**, per-line TTS | 2 conversational | **Creative** (stab ~0.35) | **audio tags + fragments + em-dashes** | 200 ms gaps + engineered overlap | Value of v3 tags/expressiveness, still line-by-line |
| **V4** | **`eleven_v3` Text to Dialogue** (one call) | 2 conversational | Natural (stab ~0.5) | tags + em-dashes + backchannel turns | model-native turn-taking, no manual overlap | Value of **single-pass multi-speaker** synthesis |
| **V5** | **Text to Dialogue** + audio-layer polish | 2 conversational | **Creative** (stab ~0.35) | full conversationalize transform | model-native + **backchannel bed** + tight seam crossfades | Best-of-both ceiling |

Expected ordering (hypothesis): **V5 ≳ V4 > V3 > V2 > V1 > V0.** The big jump should be V3→V4 (single-pass dialogue) and V1→V2 (scripted disfluency) — if V4 doesn't clearly beat V3, the win is mostly in scripting; if it does, prioritize the Text to Dialogue integration. Run 2–3 seeds per variant since v3 is nondeterministic [2].

---

## Annotation / scripting cheat sheet

| Technique | How to write it | Model / where |
|---|---|---|
| **Laughter / breath** | `[laughs]`, `[chuckles]`, `[sighs]`, `[exhales]` before the span | `eleven_v3` only [4][5] |
| **Emotion on a turn** | `[excited]` / `[sarcastic]` / `[skeptical]` at turn start, 1 per turn | `eleven_v3` [3][5] |
| **Whisper / shout / deadpan** | `[whispers]`, `[shouts]`, `[deadpan]` | `eleven_v3` [3][5] |
| **Self-interruption / cutoff** | End turn with em-dash `—`; next turn `[interrupting] —…` | `eleven_v3` (em-dash works broadly; strongest in dialogue) [4][5] |
| **Overlap / crosstalk** | `[overlapping]` + em-dash in adjacent turns | `eleven_v3` Text to Dialogue (model-native) [4]; or engineer 150–400 ms overlap at stitch layer (any model) |
| **Hesitation / trailing off** | `…` (ellipsis) | Both; stronger in v3 [5] |
| **Emphasis** | `CAPS` on the stressed word | Both; stronger in v3 [5] |
| **Backchannel ("mm-hm", "right")** | Own micro-turn in `inputs`, or a separate clip mixed −10 dB under the other voice | v3 Text to Dialogue for reaction; `mixing` audio overlay for the bed |
| **Uptalk / hand-off question** | End turn on a real `?` question | Both (punctuation-driven) [5] |
| **Contractions / fragments** | Write "it's", "that's"; allow incomplete sentences | Any model (pure text) |
| **Reactive callback** | Quote/reference what the other just said in the next turn | Any model (pure text) — biggest "they're listening" cue |
| **Steady narration** | Complete sentences, no tags, `Natural`/`Robust` stability | `multilingual_v2` (keep) [14] |

**Punctuation rules (v3):** ellipses `…` → pauses; em-dash `—` → interruption/cutoff; CAPS → emphasis/volume; standard punctuation → rhythm [5].

---

## What's reliable now vs experimental

**Reliable now:**
- `eleven_v3` GA with API access (`model_id: "eleven_v3"`), Text to Dialogue endpoint [15][16][2].
- Non-verbal/emotion/delivery audio tags (§2.3 reliable tiers) [4][5].
- Single-pass multi-speaker synthesis giving cross-turn prosody [1][2].
- Scripted disfluencies/backchannels improving naturalness (industry-proven by NotebookLM) [8][9].
- Conversational voice selection and stability/settings changes [10][14].
- Audio-layer gap tightening and short interjection overlaps [12][19].

**Experimental / caveat:**
- Exotic SFX/environment/mood tags and giant third-party tag catalogues [18].
- Deterministic reproducibility — v3 is nondeterministic even with `seed`; expect variation [2].
- Precise, dial-able overlap *timing* from tags alone (model chooses; engineer at audio layer for control) [4].
- Very short prompts (<250 chars) render inconsistently on v3 — batch into dialogue calls [3][5].
- 2000-char per-request limit on Text to Dialogue forces chunking of long exchanges (re-introduces a seam to manage) [2].
- Tag effectiveness is voice- and stability-dependent — always A/B on the actual cast voice [5].

---

## REFERENCES

[1] ElevenLabs. [Text to Dialogue — Capabilities](https://elevenlabs.io/docs/overview/capabilities/text-to-dialogue). ElevenLabs Documentation.

[2] ElevenLabs. [Create dialogue — Text to Dialogue API reference](https://elevenlabs.io/docs/api-reference/text-to-dialogue/convert). ElevenLabs Documentation.

[3] ElevenLabs. [Prompting Eleven v3 (alpha)](https://elevenlabs.io/docs/best-practices/prompting/eleven-v3). ElevenLabs Documentation.

[4] ElevenLabs. [Eleven v3 Audio Tags: Multi-Character Dialogue in AI Speech](https://elevenlabs.io/blog/eleven-v3-audio-tags-bringing-multi-character-dialogue-to-life). ElevenLabs Blog.

[5] ElevenLabs. [What are Eleven v3 Audio Tags — and why they matter](https://elevenlabs.io/blog/v3-audiotags). ElevenLabs Blog.

[6] ElevenLabs. [Text to Speech — Capabilities](https://elevenlabs.io/docs/overview/capabilities/text-to-speech). ElevenLabs Documentation.

[7] ElevenLabs. [Eleven v3 — Most Expressive AI Voice Model](https://elevenlabs.io/v3). ElevenLabs product page.

[8] Google DeepMind. [Pushing the frontiers of audio generation](https://deepmind.google/blog/pushing-the-frontiers-of-audio-generation/). (NotebookLM Audio Overview: two-stage training, disfluencies, single-pass multi-speaker synthesis, turn-taking.)

[9] Willison S. [NotebookLM's automatically generated podcasts are surprisingly effective](https://simonwillison.net/2024/Sep/29/notebooklm-audio-overview/). (Steven Johnson on the outline→critique→disfluency-injection pipeline.)

[10] ElevenLabs. [Conversational AI Voices — Voice Library](https://elevenlabs.io/voice-library/conversational). ElevenLabs.

[11] ElevenLabs. [Narrator AI Voices — Voice Library](https://elevenlabs.io/voice-library/narrator-voices). ElevenLabs.

[12] Nakamura M, et al. [Differences between acoustic characteristics of spontaneous and read speech and their effects on speech recognition performance](https://www.sciencedirect.com/science/article/abs/pii/S0885230807000459). Computer Speech & Language.

[13] Chojnacka R, et al. [Classification of Spontaneous and Scripted Speech for Multilingual Audio](https://arxiv.org/html/2412.11896v1). arXiv:2412.11896.

[14] Webfuse. [ElevenLabs Cheat Sheet (2026): Models, Voices, API, Streaming & Agents](https://www.webfuse.com/elevenlabs-cheat-sheet). (Model IDs, latencies, voice-setting ranges, v3 stability modes.)

[15] ElevenLabs. [Changelog — August 20, 2025](https://elevenlabs.io/docs/changelog/2025/8/20). (Eleven v3 API availability.)

[16] ElevenLabs. [Eleven v3: Most Expressive AI TTS Model Launched](https://elevenlabs.io/blog/eleven-v3). ElevenLabs Blog.

[17] ElevenLabs. [Eleven v3 Audio Tags: Expressing Emotional Context in Speech](https://elevenlabs.io/blog/eleven-v3-audio-tags-expressing-emotional-context-in-speech). ElevenLabs Blog.

[18] Audio Generation Plugin. [ElevenLabs Eleven v3 Alpha — Complete Guide to Audio Tags](https://audio-generation-plugin.com/elevenlabs-v3/). (Third-party extended tag catalogue — treat as experimental.)

[19] Fujimura O, et al. [The Impact of Prosodic Segmentation on Speech Synthesis of Spontaneous Speech](https://arxiv.org/html/2511.14779v1). arXiv:2511.14779. (Turn-taking, pauses, disfluencies in spontaneous-speech synthesis.)

[20] TechRadar. [Google NotebookLM's AI podcast hosts can now get into an argument over your notes](https://www.techradar.com/ai-platforms-assistants/gemini/google-notebooklms-ai-podcast-hosts-can-now-get-into-an-argument-over-your-notes). (Two-host banter/argument as a conversational device.)
