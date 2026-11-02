# braidio — agent & contributor guide

`braidio` weaves **commentary** with **extracted source clips** into one rendered
audio production. Seven format presets ship today — `solo_explainer`,
`deep_dive`, `interview`, `interview_host_removed`, `panel`, `debate`,
`documentary_vo` (`braidio.formats.FORMATS` is the registry; there is no `duo`
preset — the two-host shape is `deep_dive`).
The talk is the spine; narration bridges and source clips are the
illustration layers. It is a thin **orchestration** layer: it owns the
composition model, the format templates, the rights projection and the cost
model, and delegates DSP/TTS to `mixing`, the graph to `lacing`, and the
workflow/provenance to `nw`.

## Read this first: braidio is live infrastructure

This is not an experiment. Three things depend on the code in this repo, so a
careless signature change has real blast radius:

| Consumer | What it uses | Failure mode if you break it |
|---|---|---|
| **PyPI** (`braidio`, published by CI on merge) | the whole public API | anyone installing the release |
| **`reelee`** — `reelee/transforms/panel_to_voiceover.py` | `braidio.narrate`, `braidio.render_dialogue`, `braidio.ConversationCast` (declared `braidio>=0.0.51`) | storyboard voiceover stops rendering |
| **Two deployed MCP connectors** | `braidio.mcp.TOOL_REFS` (own connector) and `braidio.mcp.register_tools(..., prefix="braidio_")` (aggregated into the unified AV connector) | a live tool surface breaks for real users mid-conversation |

Practical consequences:

- **`narrate`, `render_dialogue`, `ConversationCast` are a contract.** Adding a
  keyword-only arg with a default is fine; renaming, reordering, or changing a
  return type is a coordinated change with `reelee`.
- **The MCP tool signatures + docstrings *are* the wire schema.** A tool's
  parameters and its whole docstring are what the model sees, and the
  description is what tool search indexes.
  `tests/test_wire_descriptions.py` caps every wire description at
  `MAX_WIRE_DESCRIPTION_CHARS` (500) — long, jargon-dense descriptions measurably
  embed *away* from short user queries, which is how a tool once became
  unfindable on a live connector. Keep the wire to purpose + key constraint,
  in the user's vocabulary; put detail in `help` / `_guide.py` / docs.
- **`braidio.mcp.FREE_TOOLS` / `COSTED_TOOLS` are the money boundary.** A new
  tool that spends ElevenLabs credits MUST be listed in `COSTED_TOOLS`, not
  `FREE_TOOLS` — the deployed connector passes `metered_tools=set(COSTED_TOOLS)`
  to its metering middleware, so a costed tool in the wrong list is spend that
  no cap applies to. Count the surface with `len(braidio.mcp.TOOL_NAMES)` rather
  than quoting a number. Every costed tool also threads the caller's key
  (`api_key=caller_elevenlabs_key()`, see "The caller's key" below) — a costed
  tool that does not bills the server's key for a BYO caller.

### How a change actually reaches production (the deploy coupling)

Merging to `main` here does **not** update the live connectors. The connector
app lives in the platform repo (`tw_platform`, app `braidio_mcp`); its venv
installs `braidio[mcp]` as an **unversioned git pin**, and the aggregated tools
ride along in the sibling `reelee_mcp` app the same way. The sequence is:

1. Merge to braidio `main` (CI bumps the version and publishes to PyPI).
2. Run a platform deploy. Its per-connector refresh step reads the app's
   `deploy/connector-requirements.txt`, compares each git requirement's
   installed commit against the remote, reinstalls the ones that moved with
   `--force-reinstall --no-deps`, and restarts the systemd unit only if
   something actually changed.
3. Confirm the connector reports the new commit.

Why the ceremony: **`pip install --upgrade` is a silent no-op on an unversioned
git pin.** pip sees the requirement already satisfied, upgrades the
*dependencies*, and reports success — which is exactly how a connector once
served days-old code while every deploy stayed green. If you change something
the connector depends on, say so in the PR so the deploy is not skipped.

## Architecture — the layering

Each layer only knows the ones beneath it. Keep it that way.

| Layer | Modules | Depends on |
|---|---|---|
| **1. Functional core** | `script` (Narration/SegmentBeat/Dialogue/Script), `rights` (Profile + plan_production), `sources` (SegmentSource, TimedLine), `tts`, `cost`, `delivery`, `pacing`, `multivoice`, `weave_config`, `music`, `compose`, `weave`, `render`, `timeline`, `captions`, `relevance`, `textprep`, `style`, `kinds` | `mixing`, `elevenlabs`, `ffmpeg` on PATH — nothing else |
| **2. Format templates** | `formats` (`Format`, `render_format`, `FORMATS`) | layer 1 only. Templates are *good defaults over the primitives*, never new mechanism |
| **3. Graph vocabulary** | `bodies/` — lacing body schemas + tiers, registered as an import side effect | `lacing` (extra `graph`) |
| **4. nw pipeline** | `transforms/` (voice-assignment → narration-render / dialogue-render → segment-extraction → episode), `provenance`, `project`, `genre` | layers 1–3 + `nw` (extra `nw-app`) |
| **5. MCP tool surface** | `mcp/` — `tools.py` (the tools), `_guide.py` (the front door), `metering.py`, `workspace.py`, `_docs.py`, `_helpers.py` | everything above + `fastmcp`/`py2mcp` (extra `mcp`) |

Two invariants worth stating explicitly:

- **`import braidio` never requires the optional layers.** `braidio/__init__.py`
  imports `bodies`/`provenance` and `project`/`transforms`/`genre` behind
  `try/except ImportError`, reporting `HAS_GRAPH` / `HAS_NW`. Never move an
  optional-dep import to the top of a core module.
- **`braidio.genre` must stay fastmcp-free.** It imports
  `braidio.mcp.workspace` *lazily* inside the project factory; a top-level
  import would pull `fastmcp` through `braidio.mcp` and flip `HAS_NW` off. There
  is a comment saying so in `genre.py` — do not "clean it up".

Renders take one path or the other: the **no-graph fast path**
(`render_production` / `render_format` — one call, one file) and the **graph
path** (`save_script` → `weave_project`, provenance + partial re-render). The
transforms delegate to the same core primitives; do not fork the DSP.

Both paths render **structural music** (braidio#25 for the fast path, #39 for the
graph one). In the graph, a `SceneBreak` is a `scene-break/v1` node ordered with
the beats — it has no render node of its own, and `weave_to_episode` turns it
into a sting or a pause via `braidio.structure`. The production's declared
structure + bed/sting assets are one singleton `production-structure/v1` node,
written **only** when a production declares them — that absence is what keeps a
structure-free format's render byte-identical, and
`tests/test_transforms_structure.py` pins it on decoded PCM. Assets reach the
graph as content-addressed ids resolved through the caller's `Workspace`, never
as paths a tool caller supplied.

Both paths also apply the rights **`Profile`** (braidio#47). The graph path runs
`plan_production` **once, at ingest**, so a beat the profile refuses never
becomes a node — never extracted, never synthesized, never billed — and a
substituted one is ingested as its substitute. No transform re-decides rights.
`rights.DEFAULT_PROFILE` is the single definition of what an undeclared profile
means; a declared one is recorded as a singleton `render-profile/v1` node (with
what it dropped/substituted) that `weave_to_episode` derives from, so changing
the profile re-stales the episode through ordinary freshness. Same absence rule
as the structure node: undeclared writes nothing, which is what keeps a legacy
project byte-identical.

**Dialogue beats** ride the graph too (braidio#46). A `Dialogue` ingests as a
`dialogue-beat/v1` (its turns, identified by `beat_id` like any beat) and
renders through `dialogue_render.tts`, which wraps `braidio.render_dialogue`
(one Text-to-Dialogue pass per exchange — never `render_multivoice`) with the
same `cache_key` compare-and-skip and cost attribution as narration. The
**cast** — which voice each role speaks with — is a production decision, so it
is one singleton `dialogue-cast/v1` node, resolved exactly as the fast path
resolves it (explicit `cast=` > the format's `cast` > `DEFAULT_CAST`), and
every dialogue render derives from `[beat, cast]` and nothing else: a recast
re-stales the dialogue renders and only them. Same absence rule again, keyed on
the *script* rather than the declaration: the cast node is written **iff the
plan has a dialogue beat**, so a script without dialogue writes nothing at the
tier whatever the format declares, and a 0.0.35-written project re-weaves as a
no-op (`tests/test_transforms_dialogue.py` pins that). A turn whose role the
cast does not name fails in the plan, naming the roles the cast has.

**Ingest is a reconcile, not an append** (braidio#49, #51). `ingest_script`
*plans* first — the rights plan, every `SegmentSource.resolve`, every asset
hash, every dialogue role's lookup in the cast — and only then *commits*, so a
failure never leaves a half-written graph. The commit writes **by identity**:
the rule is stated once, in `transforms/_common.py` (`SINGLETON_TIERS`,
`node_identity`). Singleton tiers (`weave-configs`, `production-structures`,
`render-profiles`, `dialogue-casts`) are identified by the tier; beat-derived
nodes by `beat_id` (script
position). Same identity + same value → no write; same identity + changed
value → rewritten **under the same annotation id**, which is what makes the
change reach the renders (nw's freshness compares each parent's value digest
against what its dependents recorded); identity gone from the plan → removed.
Never mint a fresh id for an existing identity, and never add a second node
at a singleton tier: both leave the old renders looking fresh, which is worse
than a refusal. `weave_project`'s wire description promises exactly this
behaviour on a re-run.

## The importer (`braidio.importing`) — four things that are not obvious

`import_production(manifest, project_root, …)` turns a normalized manifest of a
**finished** production into a real project graph. Its module docstrings carry
the full reasoning; these are the four a caller gets wrong.

1. **Retrievability is not optional, and it is a separate write from the
   graph.** The graph records an artifact by content hash; until a row exists
   in the project's delivery catalog (`.reelee/artifacts/{catalog,blobs}`) that
   id **404s on every surface**. `register_artifacts=True` is the default for
   that reason. Two rules inside it: the row is keyed on the hash the graph
   already recorded (a host-minted opaque id gives a populated catalog that
   answers nothing the project asks for), and a row's `url` is the host's
   route, **never** a `file://` path — one of those reached an `<img src>` and
   put a home directory in a page's DOM. `assert_local_backend` refuses up
   front when the host is configured to read from an object store, because
   that is the one failure mode that would otherwise report success.
2. **A delivered cut comes into the project; a motion pass does not.**
   `materialize_cuts="delivered"` — a path outside the project is not
   retrievable through any surface and does not exist on the server. Motion
   passes are resumable intermediates and roughly double the weight.
3. **Replacing a narration take is a re-weave, not an edit.** Beats and takes
   are imported so a segment is addressable at all, but a panel is pinned to
   the mix by a `MediaRef` over the mix's *content hash*, so a new take changes
   the mix's bytes and its own duration. Measured: one take reaches 27
   downstream nodes on a 24-panel production. `NARRATION_REPLACEMENT_NOTE` is
   the text to quote; never describe it as cheap.
4. **`StillBodyV1.license` is the canonical code, `license_label` is the
   spelling.** A gate comparing `"CC BY-SA 4.0"` against `"by-sa"` matches
   nothing while looking like it works, and an unrecognised code survives
   `normalize_license` unchanged — so the failure is silent. `credit_line`
   prefers the label and falls back to the code.

## Body schemas are a federation contract

`braidio/bodies/` registers 22 lacing body-schema URIs (7 domain, 11 render,
4 video — see `braidio.bodies.SCHEMA_URIS`). These are **on the wire**: the nw pipeline
persists them in project graphs, and both deployed MCP connectors read and
write them live. Renaming a URI, or renaming, removing, retyping, or
re-defaulting a serialized field is a **federation event** — it silently
breaks every stored annotation and every downstream round-trip. It needs a
new schema version plus a `lacing.register_migration` from the old one,
landed together with the nw/connector updates that depend on it. Adding an
**optional** field with a default is additive and needs no migration — that
is how `AudioClipBodyV1.spotlight` and the `scene-break/v1` /
`production-structure/v1` / `render-profile/v1` bodies themselves arrived.

**"Additive" means forward-compatible, NOT backward-compatible — and the
deploy order follows from that.** An *old body* loads on a *new build*: that
is what the default is for, and it is what the additive test asserts. The
other direction does not hold. Every body here is `extra="forbid"`, and the
read path does not validate (`lacing.schema.validate` is not called when an
annotation is loaded), so a new field rides along invisibly until something
constructs the model from the stored dict — `credit_line(annotation.body)` is
the live example, and it **raises** on a field it does not know. Measured: an
`extra="forbid"` model without `license_label` rejects a still body written by
this build.

So **deploy the new build before importing or writing with it**, exactly as
the lacing `.annot` migration note says for the same reason: the failure shows
up not at open but at the first place a body becomes a model, which is
somewhere downstream and far from the change.

`tests/test_body_schema_stability.py` pins every URI and every field's
serialized shape (name, JSON type, required/optional, default), and fails
additive vs. breaking changes in separate tests with different advice. It is
not derived from the models it protects — the pinned table is literal,
generated once from the current models and committed. If that test fails,
the paragraph above is the rule it is enforcing; do not edit the pin to make
a change pass without doing the migration first.

## `mixing` owns the audio DSP — do not wrap it in a blindfold

Everything braidio does to actual samples goes through `mixing` (or a direct
`ffmpeg` subprocess): `mixing.text_to_speech` under `braidio.tts.narrate`,
`mixing.concatenate_audio` in `render`/`conversation`/`multivoice`,
`mixing._cache` for the TTS and dialogue caches. braidio never reimplements the
DSP, and it does not own `mixing`'s bugs — but it must not hide them either.

**Rule: no broad `except` around a `mixing` (or ffmpeg) call.** A
`except Exception: pass` there turns a real DSP failure into a silent, wrong
production — a track that renders "successfully" with a missing beat. The
current code obeys this: every broad catch in the package is on a non-DSP
boundary (metering context lookup, URL fetch, yt-dlp extractor errors, an
advisory duration probe) and each carries a `# noqa: BLE001` with the reason.
Match that pattern: catch the *specific* exception, or let it propagate.

The one deliberate exception is `render._end_tail`, which catches
`(subprocess.CalledProcessError, ValueError, KeyError)` because the end-fade is
cosmetic — note that it is narrow and commented, not a bare `Exception`.

Delegation contract (from the README, and it holds):

| Concern | Owner |
|---|---|
| Raw audio DSP + TTS (crop, concat, duck, loudnorm, synth) | `mixing` / `falaw` |
| Linked-artifact graph / content-addressed media | `lacing` |
| Project workflow, provenance, partial re-render | `nw` |
| Video render + visual support | `reelee` — **except** the one case below |
| Content acquisition (lyrics, audiobooks, news…) | the consuming app, via a `SegmentSource` |

### The one video exception: `braidio.video`

`reelee` owns video — generated footage, characters, shots, storyboards — and that
has not changed. But **reelee imports braidio**, so braidio can never import
reelee, and "a rendered episode over caller-supplied stills" needs something
braidio already has and reelee would have to ask for: the beat timeline. So
`braidio.video` owns exactly one thing, *where the cuts fall*, and delegates the
pan/zoom motion to **`burns`** — the same leaf package reelee's
`panel_to_clip.kenburns` uses. Two wrappers over one shared primitive, not two
implementations.

The boundary, and the reason it is narrow:

- braidio: a finished braidio mix + stills the caller supplies → one Ken Burns
  film. It fetches no images (same rule as `SegmentSource`) and generates nothing.
- reelee: anything with panels, model sheets, shots, or generated media.

`braidio[video]` (`burns`, `pillow`) is **optional**, and the guard is stricter
than the other optional layers: `braidio/video.py` imports its dependencies
*inside the functions that use them*, so `import braidio.video` works on a bare
install and the pure planners (`plan_spans`, `assign_stills`) stay usable.
`HAS_VIDEO` reports the render path; `missing_dependencies()` names what's absent.
`mixing` happens to pull `burns` transitively today — do not rely on that, it is
not a contract.

`braidio.captions` is **core**, not optional: it is pure, and building subtitles
from the authored script plus the render's own timeline is strictly better than
running ASR over a mix whose words you already have.

## Money: ElevenLabs is the only spend

Everything else — extraction, weaving, loudness, mastering — is local ffmpeg and
free. ElevenLabs bills **per character of submitted text** (including `eleven_v3`
`[audio tags]`), which is why `braidio.cost.billable_chars` is just `len(text)`
behind a named function.

- `estimate_cost(script)` is free and must be offered before any paid render.
- Characters are always exact. **Dollars are a rate estimate**, resolved
  most-specific-first: `MODEL_USD_PER_1K_CHARS` (currently empty, by design —
  only confirmed per-model rates go there) → the env override →
  `DEFAULT_USD_PER_1K_CHARS`.
- **Unpriced mode is honest, not free.** Setting the rate env var to `none`,
  `unpriced`, `unknown`, or empty — or to anything non-numeric, negative, or
  non-finite — makes `usd_per_1k_chars()` return `None`, so costs report `None`
  (*unpriced*) with the character counts still exact. Never let an unknown rate
  become `0.0` for non-empty text; empty text is genuinely `0.0`.

| Env var | Meaning |
|---|---|
| `BRAIDIO_TTS_USD_PER_1K_CHARS` (`cost.RATE_ENV_VAR`) | USD per 1000 chars. Unset → conservative default; `none` → unpriced |
| `BRAIDIO_TTS_VOICE` (`tts.VOICE_ENV_VAR`) | default ElevenLabs voice id for narration |
| `BRAIDIO_DIALOGUE_CACHE_DIR` (`tts.DIALOGUE_CACHE_ENV_KEY`) | on-disk cache dir for Text-to-Dialogue takes |
| `BRAIDIO_DATA_HOME` (`mcp.workspace.DATA_HOME_ENV_VAR`) | data root for per-user projects/renders/assets (default `~/.local/share/braidio`) |
| `BRAIDIO_AUDIO_MAX_BYTES`, `BRAIDIO_AUDIO_MAX_DURATION_S` | bounds on `download_audio` server-side fetches |
| `ELEVENLABS_API_KEY` / `ELEVEN_API_KEY` | resolved by `mixing` / the elevenlabs client when no `api_key=` is threaded |

### The caller's key (bring-your-own ElevenLabs, braidio#58)

Every render entry point takes an explicit `api_key` — `narrate`,
`render_dialogue`, `render_production`, `render_format`, and the graph
driver `weave_project(api_key=)` — and `None` means "resolve from the
environment" (the table above). On the graph path the key travels through
nw's `execute(..., secrets=)` seam as `{"elevenlabs": key}`, handed by
`weave_project` to the two paid transforms (`narration_render.tts`,
`dialogue_render.tts`) and to no other; they read it with
`_common.elevenlabs_key` and pass it straight to synthesis. **A key is never
persisted**: not in a node body, provenance, a `cache_key` (it is not an
audio-affecting input — the same text under two keys is one render and one
cache hit), or the usage ledger. `tests/test_transforms_secrets.py` greps the
project tree for a sentinel; the decision record is on braidio#58.

At the MCP boundary the key comes **from the request, never from a tool
argument** (tool arguments are model-visible and land in the ledger):
`braidio.mcp.credentials.caller_elevenlabs_key()` reads the
`X-Elevenlabs-Key` header — reelee's BYO header — and every costed tool
threads it, so the one-shot renders and `weave_project` bill the same key.
Absent header → `None` → the server's key, exactly as before. Adding a
costed tool means threading `api_key=caller_elevenlabs_key()` too;
`tests/test_mcp_caller_key.py` pins each one. The deployed connectors do not
yet forward a per-user key (that is platform work, tracked on braidio#58).

Cache-vs-live: `narrate(..., return_cache_status=True)` reports whether `mixing`
served the audio from disk, and the `narration_render` transform uses it to
record `cost_usd_actual = 0.0` on a cache hit. The one-shot MCP tools still
report the rate estimate (`cost_basis="estimate"`) — a deliberate over-estimate
for a spend ledger.

### Metering (MCP)

`mcp/metering.py` is fail-closed by construction and the reason it is
*middleware*, not decorators: a decorator you forget on a paid tool is untracked
spend. Identity is resolved once from the verified OAuth token and read by tools
via `current_email()`; a write-ahead ledger entry is recorded *before* the call
and a failed ledger write **refuses** the call. Do not add an ambient identity
fallback, and do not let a tool re-derive the caller's identity.

**Know which middleware is actually running.** `braidio.mcp.MeteringMiddleware`
(via `build_server`) is the local/stdio/dev path. The **deployed** connectors
build their app from `TOOL_REFS` + `INSTRUCTIONS` and wrap them in the
*platform's* shared `enlace_metering` middleware — durable `dol` ledger, an
email allow-set, and a per-principal monthly credit cap over
`set(COSTED_TOOLS)`. So a change to `braidio/mcp/metering.py` does **not** change
production behaviour, while a change to `COSTED_TOOLS`, a tool's signature, or
`INSTRUCTIONS` does. `current_email()` is written to work under either host's
middleware — keep it that way.

## What actually controls pacing

This cost a round trip once, and the wrong answer got written into a shipped
skill, so it is written down here. "The narration sounds robotic" has **four**
possible causes and they live in different places.

| Lever | Where | Live on which path |
|---|---|---|
| `WeaveConfig.segmentation_unit` | config | **The master switch** for intra-beat pacing. `"beat"` (the bare-`WeaveConfig()` default) = one TTS call per narration beat, one prosodic arc, no silence inside it |
| `gap_turn_s`, `speed_jitter`, `speed_base`, `min_turn`/`max_turn` | config | `render_production` **only when `segmentation_unit != "beat"`**; `compose_narration` → `render_multivoice` always |
| `Narration.lead_gap_s` | the beat | every path, always — `render.py`'s `_lead_gap` |
| `Delivery` | render arg | v2 cannot render `[audio tags]` **at all**; only a v3 delivery makes `[pause]`/`[dryly]` fire. v3 in exchange has **no speed knob** (`Delivery.supports_speed`) |

The trap (braidio#64): before the pacing wiring, `gap_turn_s` and `speed_jitter`
were read by `compose.py` and **nothing else**. `render_format` /
`render_production` — what every `Format` template runs — ignored them, so
`gap_turn_s=0.0`, `0.28` and `3.0` produced byte-identical files. The audible
improvement that got credited to them actually came from `lead_gap_s` and the
eleven_v3 delivery. `tests/test_pacing.py::test_gap_turn_s_changes_the_rendered_timeline`
is the guard: it renders twice, changes only that knob, and fails on a zero
delta. Do not delete it, and do not relax it to a "config is threaded" assertion —
it has to measure the *output*.

`braidio/pacing.py` is the pure planner (text + knobs → `NarrationTurn`s: what to
say, how fast, how much silence after). `render.py` executes it and owns no
pacing policy of its own. Boundary strength is a named table (`BOUNDARIES`) —
a paragraph break pauses longer than a comma, and each boundary also slows the
approach to it, because *pause without final lengthening* is the robotic
signature. Add a boundary class there, not an `if` in the renderer.

Two format templates opt in (`solo_explainer`, `documentary_vo` — the
narration-heavy ones), on a v3 delivery. Everything else keeps
`segmentation_unit="beat"` and renders exactly as before.

### A render records its own settings

Making the knobs real made a render *un*reproducible: one script now has many
possible cuts, and a file on disk said nothing about which one it was. So
`render_production(..., return_timeline=True)` fills
`TimelineBreakdown.settings` (`braidio.timeline.render_settings`) — the whole
resolved `WeaveConfig` under `"weave"`, the delivery (name + model_id +
voice_settings), the rights `profile`, and a `"resolved"` block of what the
render *actually used*. Plain JSON, so a consumer that already persists the
breakdown (Hamilton's episode anatomy, from which its video pipeline takes panel
timings) persists the settings for free.

Two rules keep it honest:

- **Record the whole config, not a chosen subset.** A curated list drifts the
  day someone adds a knob; `WeaveConfig.to_dict()` cannot.
- **Record what was resolved, not what was passed.** `"resolved"` exists because
  those are not the same: a caller may pass no `config` at all (then `"weave"`
  is `None` and the pacing knobs were never consulted), the concat path takes
  its crossfade from the config *or* the `crossfade_s` argument, and `normalize`
  is a render argument, not a weave choice. A record of the caller's partial
  override would be worse than none.

The field is optional with a default, so `build_timeline` stays usable by hand
and older serialized breakdowns still load.
`tests/test_render_settings.py` pins the invariant that earns it: a `"beat"`
render and a `"sentence"` render of one script produce different durations *and*
different records.

## Conventions

- Favour functional style; small focused helpers (`_underscore` for
  module-private, inner functions for single-use). `dataclasses`, mostly
  `frozen=True`, for data. `Script`, `WeaveConfig`, `Delivery`, `Format`,
  `MusicBed` are all frozen — build variants with `WeaveConfig.with_(...)` /
  `dataclasses.replace`, never by mutation.
- Arguments beyond the 2nd–3rd position are **keyword-only**.
- No magic numbers: name them as module constants (`_DEFAULT_LUFS`,
  `BED_GAIN_BY_INTENSITY`, `PUBLISHABLE_CLIP_RIGHTS`, `CLIP_PLACEMENTS`).
- Every module needs a top-level docstring — `ruff` selects `D100` and CI
  collects doctests from the package itself.
- Time is **seconds** (`float`) everywhere.
- Rights are **data, not judgement**: `SegmentBeat.rights` + `Profile` +
  `plan_production` decide what renders; the consumer injects *what* is
  forbidden via `RightsPolicy`. Never hardcode a rights decision in a renderer —
  braidio#47 is what that costs: a literal `profile="personal"` in the episode
  transform meant the graph path skipped the filter entirely. Every render entry
  point takes a `profile` and threads it to the one `plan_production` call.
- MCP tools are flat, JSON-in/JSON-out. Return values go through
  `_helpers.to_json`; `bytes` deliberately raise — write audio into the caller's
  workspace and return its path/url.

## Tests = guardrails

### Before trusting a guard, make it fail on purpose

A guard that has only been *read* has not been checked. Three ways this repo's
verification has reported green on a smaller world than it claimed to cover —
all three found in one review of the importer, all three invisible to reading:

1. **A red baseline makes every mutation read as CAUGHT.** A mutation suite
   that does not first assert the unmutated tree is green is measuring
   nothing, and it reports a perfect score while doing it.
2. **A narrower command than CI makes a guarded fix read as UNGUARDED.** The
   fix to the burnt-in attribution was covered by a doctest; a run of
   `tests/` alone said it had no guard at all. Mutation-test with CI's own
   invocation, `--doctest-modules` included — **and with CI's flags, not
   yours.** `doctest_optionflags` in `pyproject.toml` is set to exactly what
   the wads runners pass, because they override the key wholesale and any
   difference disagrees in both directions at once.
3. **A guard that ENUMERATES what to check cannot notice what it forgot.**
   The artifact census listed four tiers by hand and omitted
   `segment-extractions`, so deleting every clip registration passed the whole
   suite. Derive the scope from the artifact — `iter_all_annotations` — so the
   census cannot have a blind spot its author did not think of.

The cheap tell for all three is the same: none of them survives one deliberate
mutation, and all of them survive being read carefully.

### State a rule where it can bind, not where it is already obeyed

`_place_into`'s docstring is the place that explains why media is copied
rather than hardlinked out of a shared source tree. The one code path that
violated the rule was `copy_media=False` — *the path that never calls
`_place_into`*. A rule written at the site that obeys it cannot bind the site
that bypasses it; if a rule matters, put a refusal at the seam, not a
paragraph at the compliant end.

### A second ROUND, not just a second reviewer

Four of the thirteen defects in the importer review were introduced **while
fixing** earlier ones, and two of those were worse than what they replaced (a
rollback that deleted the directory a `--dry-run` was pointed at; a copy that
wrote through a content-addressed blob's inode and served the wrong picture
under a live id). A single review pass would have caught the first six and
shipped those four. Re-review the fixes.

Run `pytest -q` for the current count, and `pytest -q --doctest-modules` for the
CI-equivalent pass (`testpaths` lists both `tests` and `braidio`, so
`--doctest-modules` doubles as an import smoke test for every module). CI
installs the `mcp` extra — without it the MCP and nw-gated tests silently skip,
which is worse than red.

The suite is mostly characterization: `test_mcp.py` pins the tool surface and
its error messages, `test_wire_descriptions.py` pins what the model sees,
`test_cost.py` pins the unpriced/priced semantics, `test_transforms.py` pins the
graph pipeline including the `$0`-on-cache-hit attribution,
`test_body_schema_stability.py` pins every body schema's URI and serialized
shape (see "Body schemas are a federation contract" above). **Do not edit an
assertion to make a refactor pass** — if behaviour must change, change the
assertion deliberately and say why in the commit.

`ffmpeg` must be on PATH for the render tests (declared in
`[tool.wads.ops.ffmpeg]` so CI installs it).

## What never to do

- Never add a costed tool to `FREE_TOOLS`, and never let a paid path skip the
  metering middleware.
- Never wrap a `mixing`/ffmpeg call in a broad `except` — see above.
- Never move an optional-dep import (`lacing`, `nw`, `fastmcp`) to module top
  level in the functional core, and never let `braidio.genre` import
  `braidio.mcp` eagerly.
- Never write user data into the app/deploy tree — per-user projects, renders
  and assets belong under the data root (`BRAIDIO_DATA_HOME`), because a
  deploy's `rsync --delete` would erase anything inside the app dir.
- Never bypass `Workspace`'s path validation: tools take a `project_id`, never a
  path, so one caller can't reach another's data.
- Never ship music/media in the package — a `MusicBed` asset is always supplied
  by the caller.
- Never change `narrate` / `render_dialogue` / `ConversationCast` signatures
  without checking `reelee` first.
- Never rename/remove/retype/re-default a body-schema field, or a URI, without
  a `lacing.register_migration` — see "Body schemas are a federation contract".

Research and style references live in `misc/docs/` (never inside the importable
package): `research/commentary-formats-and-styles.md` is the taxonomy the
`formats` presets encode, and `style/` holds the commentary-voice guide,
anti-platitude checklist, and per-critic voice files.
