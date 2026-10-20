"""Voice-pool sourcing and curation from the ElevenLabs shared Voice Library.

The premade pools in :mod:`braidio.multivoice` are the safe default. For a large
"many people / interviews" cast, the shared Voice Library has thousands of
voices of uneven quality, so they go through a pipeline before use:

1. :func:`fetch_shared_voices` pages through ``GET /v1/shared-voices``;
2. :func:`curate` keeps the ones fit for narration (use case, language,
   popularity, notice period, an explicit deny list);
3. :func:`dedup_voices` drops near-identical entries;
4. :func:`diverse_pool` picks ``n`` voices spread across gender, accent and age,
   returned as :class:`~braidio.multivoice.Voice` so ``assign_voices`` and
   ``render_multivoice`` take them unchanged.

The network edge is the ``get_page`` keyword (``params -> response dict``); the
default calls the API with ``$ELEVENLABS_API_KEY``, and tests pass a fake. A
shared voice may have to be added to the account's library
(``POST /v1/voices/add/{public_owner_id}/{voice_id}``) before TTS can use it;
:class:`SharedVoice` keeps ``public_owner_id`` for that step.
"""

from __future__ import annotations

import json
import os
import random
import re
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field

from braidio.multivoice import Voice

SHARED_VOICES_URL = "https://api.elevenlabs.io/v1/shared-voices"
DFLT_PAGE_SIZE = 100  # the API's maximum
NARRATION_USE_CASES = ("narrative_story", "conversational", "informative_educational")

GetPage = Callable[[dict], dict]


@dataclass(frozen=True)
class SharedVoice:
    """One shared-library voice, with the fields curation needs."""

    voice_id: str
    name: str
    public_owner_id: str = ""
    gender: str = ""  # "male" | "female" | "neutral" | ""
    age: str = ""  # "young" | "middle_aged" | "old" | ""
    accent: str = ""
    language: str = ""
    use_case: str = ""
    descriptive: str = ""
    category: str = ""
    cloned_by_count: int = 0
    usage_character_count_1y: int = 0
    notice_period: int | None = None  # days before the owner may withdraw it
    free_users_allowed: bool = True
    preview_url: str = ""
    extra: dict = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_api(cls, d: dict) -> SharedVoice:
        known = {f for f in cls.__dataclass_fields__ if f != "extra"}
        kw = {k: d[k] for k in known if d.get(k) is not None}
        return cls(**kw, extra={k: v for k, v in d.items() if k not in known})

    def to_voice(self) -> Voice:
        """As a :class:`~braidio.multivoice.Voice` (gender ``M``/``F``/``N``)."""
        gender = {"male": "M", "female": "F"}.get(self.gender.lower(), "N")
        note = ", ".join(x for x in (self.age.replace("_", " "), self.descriptive) if x)
        return Voice(self.voice_id, self.name, gender, self.accent, note)


def http_get_page(params: dict, *, api_key: str | None = None, timeout: float = 30) -> dict:
    """One page of ``GET /v1/shared-voices`` (the default ``get_page``)."""
    key = api_key or os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVEN_API_KEY")
    if not key:
        raise RuntimeError(
            "Set ELEVENLABS_API_KEY to browse the shared Voice Library "
            "(https://elevenlabs.io/app/settings/api-keys)."
        )
    url = f"{SHARED_VOICES_URL}?{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, headers={"xi-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https host
        return json.load(r)


def fetch_shared_voices(
    *,
    max_voices: int = 500,
    page_size: int = DFLT_PAGE_SIZE,
    get_page: GetPage | None = None,
    **filters,
) -> Iterator[SharedVoice]:
    """Yield shared voices, page by page, until ``max_voices`` or the end.

    ``filters`` are passed as API query parameters (e.g. ``language="en"``,
    ``use_cases=["narrative_story"]``, ``gender="female"``, ``sort="cloned_by_count"``).
    """
    get_page = get_page or http_get_page
    n, page = 0, 0
    while n < max_voices:
        body = get_page({**filters, "page_size": page_size, "page": page})
        for d in body.get("voices", []):
            yield SharedVoice.from_api(d)
            n += 1
            if n >= max_voices:
                return
        if not body.get("has_more"):
            return
        page += 1


def curate(
    voices: Iterable[SharedVoice],
    *,
    use_cases: Iterable[str] | None = NARRATION_USE_CASES,
    languages: Iterable[str] | None = ("en",),
    min_cloned_by: int = 0,
    min_usage_1y: int = 0,
    min_notice_days: int | None = None,
    free_users_only: bool = False,
    deny_ids: Iterable[str] = (),
    allow_ids: Iterable[str] | None = None,
) -> list[SharedVoice]:
    """Voices fit for narration, most-used first. ``None`` disables a filter.

    ``allow_ids`` (an audition allowlist) keeps only those ids; ``deny_ids``
    drops voices auditioned as unfit. ``min_notice_days`` keeps only voices
    their owner cannot withdraw at short notice (protects a long production).
    """
    use_cases = set(use_cases) if use_cases is not None else None
    languages = set(languages) if languages is not None else None
    deny, allow = set(deny_ids), set(allow_ids) if allow_ids is not None else None

    def keep(v: SharedVoice) -> bool:
        return (
            (use_cases is None or v.use_case in use_cases)
            and (languages is None or v.language in languages)
            and v.cloned_by_count >= min_cloned_by
            and v.usage_character_count_1y >= min_usage_1y
            and (min_notice_days is None or (v.notice_period or 0) >= min_notice_days)
            and (not free_users_only or v.free_users_allowed)
            and v.voice_id not in deny
            and (allow is None or v.voice_id in allow)
        )

    kept = [v for v in voices if keep(v)]
    return sorted(kept, key=lambda v: (-v.usage_character_count_1y, -v.cloned_by_count))


def _base_name(name: str) -> str:
    """``"Brian - deep narrator v2"`` → ``"brian"``: the name before any qualifier."""
    return re.split(r"[\s\-–—|(:,]", name.strip().lower(), maxsplit=1)[0]


def dedup_voices(
    voices: Iterable[SharedVoice], *, key: Callable[[SharedVoice], tuple] | None = None
) -> list[SharedVoice]:
    """Drop near-identical voices, keeping the first of each group (order = rank).

    Default key: same owner, base name, gender and accent — the shape of an
    owner re-uploading one voice under variants. Pass ``key`` to tighten or
    loosen (e.g. an embedding-cluster id once a quality gate exists).
    """
    key = key or (lambda v: (v.public_owner_id, _base_name(v.name), v.gender, v.accent))
    seen, out = set(), []
    for v in voices:
        k = key(v)
        if k not in seen:
            seen.add(k)
            out.append(v)
    return out


def diverse_pool(
    voices: Iterable[SharedVoice],
    n: int,
    *,
    spread: tuple[str, ...] = ("gender", "accent", "age"),
    seed: int | None = 0,
) -> list[Voice]:
    """``n`` voices spread as evenly as possible over the ``spread`` attributes.

    Round-robins over the ``spread[0]`` groups (gender: an even M/F split
    when ``n`` is even), and within each group prefers attribute values not yet
    used, so the pool differs in accent and age too. Input order is the
    preference order (pass :func:`curate`'s ranking); ``seed`` shuffles ties.
    """
    rng = random.Random(seed)
    groups: dict[str, list[SharedVoice]] = {}
    for v in voices:
        groups.setdefault(getattr(v, spread[0]) if spread else "", []).append(v)
    if n > sum(map(len, groups.values())):
        raise ValueError(f"asked for {n} voices, only {sum(map(len, groups.values()))} curated")
    order = sorted(groups)
    rng.shuffle(order)
    used: dict[str, set] = {a: set() for a in spread[1:]}
    picked: list[SharedVoice] = []
    while len(picked) < n:
        for g in order:
            if len(picked) == n or not groups[g]:
                continue
            cands = groups[g]
            best = min(
                range(len(cands)),
                key=lambda i: (sum(getattr(cands[i], a) in used[a] for a in used), i),
            )
            v = cands.pop(best)
            for a in used:
                used[a].add(getattr(v, a))
            picked.append(v)
    return [v.to_voice() for v in picked]
