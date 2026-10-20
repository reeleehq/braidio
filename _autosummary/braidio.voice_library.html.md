# braidio.voice_library

Voice-pool sourcing and curation from the ElevenLabs shared Voice Library.

The premade pools in [`braidio.multivoice`](braidio.multivoice.html.md#module-braidio.multivoice) are the safe default. For a large
“many people / interviews” cast, the shared Voice Library has thousands of
voices of uneven quality, so they go through a pipeline before use:

1. [`fetch_shared_voices()`](#braidio.voice_library.fetch_shared_voices) pages through `GET /v1/shared-voices`;
2. [`curate()`](#braidio.voice_library.curate) keeps the ones fit for narration (use case, language,
   popularity, notice period, an explicit deny list);
3. [`dedup_voices()`](#braidio.voice_library.dedup_voices) drops near-identical entries;
4. [`diverse_pool()`](#braidio.voice_library.diverse_pool) picks `n` voices spread across gender, accent and age,
   returned as [`Voice`](braidio.multivoice.html.md#braidio.multivoice.Voice) so `assign_voices` and
   `render_multivoice` take them unchanged.

The network edge is the `get_page` keyword (`params -> response dict`); the
default calls the API with `$ELEVENLABS_API_KEY`, and tests pass a fake. A
shared voice may have to be added to the account’s library
(`POST /v1/voices/add/{public_owner_id}/{voice_id}`) before TTS can use it;
[`SharedVoice`](#braidio.voice_library.SharedVoice) keeps `public_owner_id` for that step.

### Functions

| [`curate`](#braidio.voice_library.curate)(voices, \*[, use_cases, languages, ...])   | Voices fit for narration, most-used first.                                  |
|----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| [`dedup_voices`](#braidio.voice_library.dedup_voices)(voices, \*[, key])                   | Drop near-identical voices, keeping the first of each group (order = rank). |
| [`diverse_pool`](#braidio.voice_library.diverse_pool)(voices, n, \*[, spread, seed])       | `n` voices spread as evenly as possible over the `spread` attributes.       |
| [`fetch_shared_voices`](#braidio.voice_library.fetch_shared_voices)(\*[, max_voices, ...])        | Yield shared voices, page by page, until `max_voices` or the end.           |
| [`http_get_page`](#braidio.voice_library.http_get_page)(params, \*[, api_key, timeout])     | One page of `GET /v1/shared-voices` (the default `get_page`).               |

### Classes

| [`SharedVoice`](#braidio.voice_library.SharedVoice)(voice_id, name[, ...])   | One shared-library voice, with the fields curation needs.   |
|---------------------------------------------------------------------------------------|-------------------------------------------------------------|

### *class* braidio.voice_library.SharedVoice(voice_id, name, public_owner_id='', gender='', age='', accent='', language='', use_case='', descriptive='', category='', cloned_by_count=0, usage_character_count_1y=0, notice_period=None, free_users_allowed=True, preview_url='', extra=dict())

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One shared-library voice, with the fields curation needs.

#### to_voice()

As a [`Voice`](braidio.multivoice.html.md#braidio.multivoice.Voice) (gender `M`/`F`/`N`).

* **Return type:**
  [`Voice`](braidio.multivoice.html.md#braidio.multivoice.Voice)

### braidio.voice_library.curate(voices, , use_cases=('narrative_story', 'conversational', 'informative_educational'), languages=('en',), min_cloned_by=0, min_usage_1y=0, min_notice_days=None, free_users_only=False, deny_ids=(), allow_ids=None)

Voices fit for narration, most-used first. `None` disables a filter.

`allow_ids` (an audition allowlist) keeps only those ids; `deny_ids`
drops voices auditioned as unfit. `min_notice_days` keeps only voices
their owner cannot withdraw at short notice (protects a long production); an
unknown notice period counts as 0, so it is dropped. `languages` match on
the code before any region (`"en-US"` is `"en"`).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`SharedVoice`](#braidio.voice_library.SharedVoice)]

### braidio.voice_library.dedup_voices(voices, , key=None)

Drop near-identical voices, keeping the first of each group (order = rank).

Default key: same owner, base name, gender and accent — the shape of an
owner re-uploading one voice under variants. Pass `key` to tighten or
loosen (e.g. an embedding-cluster id once a quality gate exists).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`SharedVoice`](#braidio.voice_library.SharedVoice)]

### braidio.voice_library.diverse_pool(voices, n, , spread=('gender', 'accent', 'age'), seed=0)

`n` voices spread as evenly as possible over the `spread` attributes.

Round-robins over the `spread[0]` groups (gender: an even M/F split
when `n` is even), and within each group prefers attribute values not yet
used, so the pool differs in accent and age too. Values are compared
case-insensitively; voices with no `spread[0]` value (or a gender other
than male/female) only fill places the main groups cannot. Input order is
the preference order (pass [`curate()`](#braidio.voice_library.curate)’s ranking); `seed` only picks
which group goes first.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Voice`](braidio.multivoice.html.md#braidio.multivoice.Voice)]

### braidio.voice_library.fetch_shared_voices(, max_voices=500, page_size=100, get_page=None, \*\*filters)

Yield shared voices, page by page, until `max_voices` or the end.

`filters` are passed as API query parameters (e.g. `language="en"`,
`use_cases=["narrative_story"]`, `gender="female"`, `sort="cloned_by_count"`).

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Iterator)[[`SharedVoice`](#braidio.voice_library.SharedVoice)]

### braidio.voice_library.http_get_page(params, , api_key=None, timeout=30)

One page of `GET /v1/shared-voices` (the default `get_page`).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
