"""Shared Voice Library sourcing + curation (API-free: a fake ``get_page``)."""

from __future__ import annotations

import pytest

from braidio.multivoice import Voice, assign_voices
from braidio.voice_library import (
    SharedVoice,
    curate,
    dedup_voices,
    diverse_pool,
    fetch_shared_voices,
    http_get_page,
)


def _v(i, gender="male", accent="american", age="young", **kw):
    return {"voice_id": f"v{i}", "name": f"Voice{i}", "public_owner_id": f"o{i}",
            "gender": gender, "accent": accent, "age": age, "language": "en",
            "use_case": "narrative_story", "cloned_by_count": 100 - i,
            "usage_character_count_1y": 1000 - i, "rate": 1.0, **kw}


def _pages(items, size):
    calls = []

    def get_page(params):
        calls.append(params)
        p, s = params["page"], params["page_size"]
        chunk = items[p * s: (p + 1) * s]
        return {"voices": chunk, "has_more": (p + 1) * s < len(items)}

    get_page.calls = calls
    return get_page


def test_fetch_pages_until_end_and_caps():
    get = _pages([_v(i) for i in range(5)], 2)
    got = list(fetch_shared_voices(get_page=get, page_size=2, language="en"))
    assert [v.voice_id for v in got] == ["v0", "v1", "v2", "v3", "v4"]
    assert [c["page"] for c in get.calls] == [0, 1, 2] and get.calls[0]["language"] == "en"
    assert got[0].extra == {"rate": 1.0}
    assert len(list(fetch_shared_voices(get_page=_pages([_v(i) for i in range(5)], 2),
                                        page_size=2, max_voices=3))) == 3


def test_curate_filters_and_ranks():
    vs = [SharedVoice.from_api(d) for d in (
        _v(1), _v(2, use_case="characters_animation"), _v(3, language="fr"),
        _v(4, cloned_by_count=1), _v(5, notice_period=30), _v(0),
    )]
    assert [v.voice_id for v in curate(vs)] == ["v0", "v1", "v4", "v5"]
    assert [v.voice_id for v in curate(vs, min_cloned_by=10, deny_ids=["v0"])] == ["v1", "v5"]
    assert [v.voice_id for v in curate(vs, min_notice_days=7)] == ["v5"]
    assert [v.voice_id for v in curate(vs, allow_ids=["v1"])] == ["v1"]
    assert len(curate(vs, use_cases=None, languages=None)) == 6


def test_dedup_owner_variants():
    a = SharedVoice.from_api({**_v(1), "name": "Brian - deep", "public_owner_id": "o"})
    b = SharedVoice.from_api({**_v(2), "name": "Brian (v2)", "public_owner_id": "o"})
    c = SharedVoice.from_api({**_v(3), "name": "Brian", "public_owner_id": "other"})
    assert dedup_voices([a, b, c]) == [a, c]


def test_diverse_pool_balances_gender_and_accents():
    vs = [SharedVoice.from_api(_v(i, gender=g, accent=acc)) for i, (g, acc) in enumerate(
        [("male", "american")] * 3 + [("female", "american")] * 3
        + [("male", "british"), ("female", "australian")]
    )]
    pool = diverse_pool(vs, 4)
    assert all(isinstance(v, Voice) for v in pool)
    assert sorted(v.gender for v in pool) == ["F", "F", "M", "M"]
    assert {v.accent for v in pool} == {"american", "british", "australian"}
    assert diverse_pool(vs, 4) == pool  # seeded
    assert len(assign_voices(10, pool, seed=1)) == 10  # plugs into casting
    with pytest.raises(ValueError, match="only 8"):
        diverse_pool(vs, 9)


def test_default_fetch_needs_key(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVEN_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
        http_get_page({})
