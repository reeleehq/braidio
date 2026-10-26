"""The rights ``Profile`` through the **graph** path (thorwhalen/braidio#47).

The no-graph fast path has always filtered beats through
``braidio.plan_production``; the graph path did not. ``ingest_script`` walked
every beat unconditionally and the episode body's ``profile`` was the literal
``"personal"``, so a ``published`` render on the graph path quietly played
audio a ``published`` render on the fast path would refuse — and then labelled
the result a personal cut. On the live connector that was a caller rendering a
publishable episode out of personal-only sources with no filter in the way.

What these tests pin, and why each one exists:

- **The two paths agree.** :func:`test_a_published_profile_refuses_a_personal_only_segment`
  asserts the graph render contains no clip member for a segment the fast
  path's plan drops, and does it by comparing against ``plan_production``
  itself rather than against a hand-copied expectation — if the two ever
  disagree about what ``published`` means, this fails.
- **Legacy is untouched.**
  :func:`test_no_declared_profile_calls_weave_timeline_exactly_as_before` pins
  the transform's *call* — the items and every keyword — for a project that
  declares no profile. This is deliberately the same shape of characterization
  ``tests/test_transforms_structure.py`` uses, and for the same reason: a
  literal decoded-PCM hash captured from ``origin/main`` is not portable
  because CI renders on three ffmpeg builds and mp3 output is not stable
  across them. Pinning the call is what no change inside ``weave_timeline``
  can hide.
- **The decision is recorded**, not just enacted: the profile is a declared
  input of the episode transform, so it lands in provenance and re-stales the
  episode.

Nothing here reaches a network or a paid API; ``narrate`` writes a tone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import braidio

from _audio import FixedSource, needs_ffmpeg, tone

pytestmark = [
    pytest.mark.skipif(not braidio.HAS_NW, reason="nw (and lacing) not available"),
    needs_ffmpeg,
]

# The episode transform's literal weave defaults — what the characterization
# reference reproduces. Keep in step with braidio.transforms._episode.
PRE_CHANGE_DEFAULTS = {
    "clip_edge_overlap_s": 0.5,
    "crossfade_s": 0.12,
    "target_lufs": -16.0,
    "true_peak_dbtp": -1.0,
    "sample_rate": 44100,
}


@pytest.fixture
def tone_narration(monkeypatch):
    """narrate() → a fixed length of *tone*. No TTS call, no spend.

    Audible rather than silent because ffmpeg's ``loudnorm`` aborts on a mix
    that is 100% digital silence — and a published cut that drops its only clip
    is exactly such a mix (same reason ``tests/test_transforms_structure.py``
    keeps a tone fixture).
    """

    def fake_narrate(text, out, *, return_cache_status=False, **kw):
        p = tone(Path(out), 220, 1.0)
        return (p, False) if return_cache_status else p

    monkeypatch.setattr(braidio, "narrate", fake_narrate)


@pytest.fixture
def project(tmp_path):
    return braidio.Project.init(tmp_path / "proj", title="graph rights")


@pytest.fixture
def source(tmp_path):
    """A source resolving every reference to a 2s window of a 440 Hz tone."""
    return FixedSource(tone(tmp_path / "song.wav", 440, 8.0), start_s=1.0, end_s=3.0)


def _script(*beats):
    return braidio.Script(title="Graph", id_slug="graph", beats=list(beats))


def _member_kinds(project, episode) -> list[str]:
    """``"narration"`` / ``"clip"`` per member of ``episode``, in play order."""
    import nw

    from braidio.transforms._common import TIER_NARRATION_RENDER

    index = {str(a.id): a for a in nw.iter_all_annotations(project.root)}
    return [
        "narration" if index[mid].tier == TIER_NARRATION_RENDER else "clip"
        for mid in episode.body["ordered_member_ids"]
    ]


def _personal_only_script():
    """Narration around one ``owned-local`` clip — playable personally, not
    publishable (``owned-local`` is not in ``PUBLISHABLE_CLIP_RIGHTS``)."""
    return _script(
        braidio.Narration(text="one"),
        braidio.SegmentBeat(reference="the hook", label="hook", rights="owned-local"),
        braidio.Narration(text="two"),
    )


# --- the bug: a commercial cut must refuse personal-only source audio --------


@needs_ffmpeg
def test_a_published_profile_refuses_a_personal_only_segment(
    project, source, tone_narration
):
    """``published`` drops an ``owned-local`` clip on the graph path — exactly as
    it does on the fast path.

    The expectation is taken from ``plan_production`` rather than written out by
    hand, so this test asserts the two paths *agree* rather than asserting a
    copy of one of them.
    """
    script = _personal_only_script()

    fast_path = braidio.plan_production(script, braidio.Profile.PUBLISHED)
    assert [b.kind for b in fast_path.beats] == ["narration", "narration"]
    assert fast_path.dropped == ["hook"]

    episode = braidio.weave_project(
        project, script, source=source, profile=braidio.Profile.PUBLISHED
    )

    assert _member_kinds(project, episode) == [b.kind for b in fast_path.beats]
    assert "clip" not in _member_kinds(project, episode)


@needs_ffmpeg
def test_a_refused_segment_is_never_extracted_at_all(project, source, tone_narration):
    """Refusal happens at ingest, so the clip is not merely left out of the mix —
    it never becomes a node, is never cut, and (for narration) is never paid for."""
    import nw

    from braidio.transforms._common import TIER_AUDIO_CLIP, TIER_SEGMENT_EXTRACTION

    braidio.weave_project(
        project,
        _personal_only_script(),
        source=source,
        profile=braidio.Profile.PUBLISHED,
    )

    assert nw.annotations_at_tier(project.root, TIER_AUDIO_CLIP) == []
    assert nw.annotations_at_tier(project.root, TIER_SEGMENT_EXTRACTION) == []


@needs_ffmpeg
def test_the_same_segment_still_plays_under_the_personal_profile(
    project, source, tone_narration
):
    """The other half of the rule: ``personal`` is unaffected — the refusal is the
    profile's doing, not a new blanket restriction on owned-local audio."""
    episode = braidio.weave_project(
        project,
        _personal_only_script(),
        source=source,
        profile=braidio.Profile.PERSONAL,
    )
    assert _member_kinds(project, episode) == ["narration", "clip", "narration"]


@needs_ffmpeg
def test_a_publishable_clip_survives_the_published_profile(
    project, source, tone_narration
):
    """``published`` filters by rights, not by beat type: a public-domain clip
    plays in the published cut."""
    episode = braidio.weave_project(
        project,
        _script(
            braidio.Narration(text="one"),
            braidio.SegmentBeat(
                reference="the hook", label="hook", rights="public-domain"
            ),
        ),
        source=source,
        profile=braidio.Profile.PUBLISHED,
    )
    assert _member_kinds(project, episode) == ["narration", "clip"]


@needs_ffmpeg
def test_a_published_substitute_is_ingested_as_narration(
    project, source, tone_narration
):
    """A refused segment carrying a ``published_substitute`` becomes that
    narration — the substitution the fast path makes, made here too."""
    episode = braidio.weave_project(
        project,
        _script(
            braidio.SegmentBeat(
                reference="the hook",
                label="hook",
                rights="copyrighted",
                published_substitute="a transformative description of the hook",
            ),
        ),
        source=source,
        profile=braidio.Profile.PUBLISHED,
    )
    assert _member_kinds(project, episode) == ["narration"]

    import nw

    from braidio.transforms._common import TIER_NARRATIVE_BEAT

    (beat,) = nw.annotations_at_tier(project.root, TIER_NARRATIVE_BEAT)
    assert beat.body["text"] == "a transformative description of the hook"


@needs_ffmpeg
def test_a_caller_supplied_rights_policy_decides_what_is_publishable(
    project, source, tone_narration
):
    """Rights are data the consumer injects: widening
    ``publishable_clip_rights`` lets an ``owned-local`` clip into the published
    cut, without braidio hardcoding that judgement."""
    episode = braidio.weave_project(
        project,
        _personal_only_script(),
        source=source,
        profile=braidio.Profile.PUBLISHED,
        rights=braidio.RightsPolicy(
            publishable_clip_rights=frozenset({"public-domain", "owned-local"})
        ),
    )
    assert _member_kinds(project, episode) == ["narration", "clip", "narration"]


@needs_ffmpeg
def test_a_profile_that_refuses_everything_fails_like_the_fast_path(
    project, source, tone_narration
):
    """Filtering at ingest makes "the profile left nothing to render" reachable on
    the graph path for the first time. It must land the way the fast path lands
    it — a ValueError naming the empty mix, not an opaque ffmpeg abort."""
    script = _script(
        braidio.SegmentBeat(reference="the hook", label="hook", rights="owned-local")
    )
    with pytest.raises(ValueError):
        braidio.weave_project(
            project, script, source=source, profile=braidio.Profile.PUBLISHED
        )


# --- the decision is recorded, not just enacted -----------------------------


@needs_ffmpeg
def test_the_episode_records_the_profile_it_actually_rendered(
    project, source, tone_narration
):
    """The bug's other half: the episode used to claim ``"personal"`` whatever was
    asked for."""
    episode = braidio.weave_project(
        project,
        _personal_only_script(),
        source=source,
        profile=braidio.Profile.PUBLISHED,
    )
    assert episode.body["profile"] == "published"


@needs_ffmpeg
def test_the_episode_derives_from_the_render_profile_node(
    project, source, tone_narration
):
    """The profile is a declared INPUT of the episode transform, so it lands in
    provenance.

    That edge is what makes a profile change re-stale the episode through
    ordinary freshness rather than through a special case;
    ``tests/test_transforms.py`` asserts the ``nw.stale_after`` consequence,
    where the in-place-rewrite helper lives.
    """
    from braidio.transforms._common import TIER_RENDER_PROFILE, optional_singleton

    episode = braidio.weave_project(
        project,
        _personal_only_script(),
        source=source,
        profile=braidio.Profile.PUBLISHED,
    )
    profile_node = optional_singleton(project, TIER_RENDER_PROFILE)

    assert profile_node is not None
    assert profile_node.id in episode.provenance.was_derived_from


@needs_ffmpeg
def test_the_render_profile_node_records_what_the_profile_decided(
    project, source, tone_narration
):
    """Dropped and substituted beats are recorded, so a rights decision can be
    audited after the fact instead of being inferred from an absence."""
    from braidio.transforms._common import TIER_RENDER_PROFILE, optional_singleton

    braidio.weave_project(
        project,
        _script(
            braidio.Narration(text="one"),
            braidio.SegmentBeat(
                reference="the hook", label="hook", rights="owned-local"
            ),
            braidio.SegmentBeat(
                reference="the bridge",
                label="bridge",
                rights="copyrighted",
                published_substitute="a description of the bridge",
            ),
        ),
        source=source,
        profile=braidio.Profile.PUBLISHED,
    )
    body = optional_singleton(project, TIER_RENDER_PROFILE).body

    assert body["profile"] == "published"
    assert body["dropped"] == ["hook"]
    assert body["substituted"] == ["bridge"]
    assert body["publishable_clip_rights"] == ["public-domain"]


# --- characterization: a profile-less project is untouched ------------------


@needs_ffmpeg
def test_no_declared_profile_calls_weave_timeline_exactly_as_before(
    project, source, tone_narration, monkeypatch
):
    """A project that declares no profile weaves exactly what it wove before.

    Pins the transform's *call* — the items and every keyword — which no change
    inside ``weave_timeline`` can mask. (Not a decoded-PCM hash: mp3 output is
    not stable across the three ffmpeg builds CI renders on, so the reference
    would be unportable rather than strict. Same reasoning as
    ``tests/test_transforms_structure.py``.)
    """
    calls = []
    real_weave = braidio.weave_timeline

    def recording_weave(items, out, **kw):
        calls.append((items, kw))
        return real_weave(items, out, **kw)

    monkeypatch.setattr(braidio, "weave_timeline", recording_weave)
    braidio.weave_project(project, _personal_only_script(), source=source)

    (items, kw) = calls[-1]
    assert [it.kind for it in items] == ["narration", "clip", "narration"]
    assert all(it.placement == "sequential" for it in items)
    assert not any(it.spotlight for it in items)
    assert kw == {
        "clip_edge_overlap_s": PRE_CHANGE_DEFAULTS["clip_edge_overlap_s"],
        "narration_crossfade_s": PRE_CHANGE_DEFAULTS["crossfade_s"],
        "target_lufs": PRE_CHANGE_DEFAULTS["target_lufs"],
        "true_peak": PRE_CHANGE_DEFAULTS["true_peak_dbtp"],
        "sample_rate": PRE_CHANGE_DEFAULTS["sample_rate"],
        "bed": None,
    }


@needs_ffmpeg
def test_no_declared_profile_writes_no_render_profile_node(
    project, source, tone_narration
):
    """The absence is load-bearing: a legacy project keeps the graph — and so the
    episode's provenance and identity — that it had before this layer existed.
    The same convention the production-structure node follows (braidio#39)."""
    from braidio.transforms._common import TIER_RENDER_PROFILE, optional_singleton

    episode = braidio.weave_project(project, _personal_only_script(), source=source)

    assert optional_singleton(project, TIER_RENDER_PROFILE) is None
    # …and it still reports the profile it really rendered, the fast path's default.
    assert episode.body["profile"] == braidio.DEFAULT_PROFILE.value == "personal"


@needs_ffmpeg
def test_declaring_the_default_profile_renders_the_same_members(
    project, source, tone_narration, tmp_path
):
    """Declaring ``personal`` explicitly is the same render as declaring nothing —
    the node it adds records the decision, it does not change one."""
    undeclared = braidio.weave_project(project, _personal_only_script(), source=source)
    declared = braidio.weave_project(
        braidio.Project.init(tmp_path / "declared", title="declared"),
        _personal_only_script(),
        source=source,
        profile=braidio.Profile.PERSONAL,
    )
    assert undeclared.body["profile"] == declared.body["profile"] == "personal"


@needs_ffmpeg
def test_paced_seed_is_the_script_position_on_both_paths(
    project, source, tone_narration, tmp_path, monkeypatch
):
    """A dropped segment shifts plan positions, not script positions: both paths
    must seed pacing on the latter (braidio#118)."""
    import braidio.render as render_mod
    import braidio.transforms._narration as narration_mod

    seeds: dict[str, list[int]] = {"fast": [], "graph": []}

    def spy(name, real):
        def planner(*args, **kw):
            seeds[name].append(kw["seed_offset"])
            return real(*args, **kw)

        return planner

    def fast_narrate(text, out, **kw):  # the fast path calls render's own `narrate`
        return tone(Path(out), 220, 1.0)

    monkeypatch.setattr(render_mod, "narrate", fast_narrate)
    real = render_mod.plan_beat_turns
    monkeypatch.setattr(render_mod, "plan_beat_turns", spy("fast", real))
    monkeypatch.setattr(narration_mod, "plan_beat_turns", spy("graph", real))
    script = _personal_only_script()
    cfg = braidio.WeaveConfig(segmentation_unit="sentence", min_turn=1, max_turn=3)

    braidio.render_production(
        script,
        source=source,
        config=cfg,
        profile=braidio.Profile.PUBLISHED,
        out_path=tmp_path / "fast.mp3",
        tts_dir=tmp_path / "tts",
        clips_dir=tmp_path / "clips",
    )
    braidio.weave_project(
        project, script, source=source, profile=braidio.Profile.PUBLISHED, config=cfg
    )

    # (the graph plans in `plan` and again in `execute`: one seed per beat)
    assert seeds["fast"] == sorted(set(seeds["graph"])) == [0, 2]
