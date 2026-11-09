"""Sound design on the graph path (braidio#161, part 2).

``weave_project(sound_design=SoundDesign())`` runs nw's foley Transform over
the finished episode through braidio's subclass
(:mod:`braidio.transforms._sound`). foley is a stand-in module (the shape nw's
own tests pin against the real signature), synthesis is stubbed, and falaw's
cache and content store are isolated: offline, nothing spent. What is pinned:

- the absence rule: undeclared, the graph gains no node and the episode is
  the one it always was;
- declared, foley is handed the episode's *spoken* sentences (never a clip's
  label) at starts inside their beats, the episode's own mix as the audio,
  and the licence intent of the rights profile; the design is its own node,
  on the receipt, with credits, and its cost is unknown, never free;
- the design reuses on a no-op re-weave, re-designs on a changed knob, and
  reads stale when sound design is dropped.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

import braidio

pytestmark = pytest.mark.skipif(not braidio.HAS_NW, reason="nw not installed")

if braidio.HAS_NW:
    from falaw.testing import isolated_falaw_cache  # noqa: F401  (autouse fixture)

from test_transforms import (  # noqa: F401  (fixtures)
    patched_synthesis,
    project,
    script_and_source,
)


class _Serializable(SimpleNamespace):
    def to_dict(self):
        return dict(vars(self))


@pytest.fixture
def foley(monkeypatch):
    """A ``foley`` stand-in: ``score`` records its call, ``audio.save`` encodes."""
    mod = ModuleType("foley")
    mod.calls = []

    def score(
        segments,
        *,
        audio=None,
        segment_starts=None,
        commercial_ok=None,
        max_events=6,
        master="podcast",
        allow_generate=True,
        budget=None,
        **kw,
    ):
        mod.calls.append(
            {
                "segments": list(segments),
                "audio": audio,
                "segment_starts": list(segment_starts or ()),
                "commercial_ok": commercial_ok,
                "max_events": max_events,
            }
        )
        return SimpleNamespace(
            timeline=_Serializable(items=({"sound_id": "door", "at": 0.4},)),
            events=[_Serializable(segment=0, query="door", sound_id="door")],
            weave=SimpleNamespace(
                audio=[[0.0, 0.0]] * 16,
                sr=8,
                credits=_Serializable(entries=({"title": "Door", "by": "X"},)),
                captions_vtt="WEBVTT\n",
                master_report={"integrated_lufs": -16.0},
                notes=["estimates"],
            ),
        )

    def save(samples, sample_rate, dst, *, fmt="flac", subtype="PCM_24"):
        dst.write(f"{fmt}:{sample_rate}:{len(samples)}:{len(mod.calls)}".encode())

    mod.score = score
    mod.audio = SimpleNamespace(save=save)
    mod.Budget = lambda *, max_usd: SimpleNamespace(max_usd=max_usd)
    monkeypatch.setitem(sys.modules, "foley", mod)
    return mod


def _tier(project, tier):
    import nw

    return nw.annotations_at_tier(project.root, tier)


def test_undeclared_writes_nothing_and_never_imports_foley(
    project, script_and_source, patched_synthesis, monkeypatch
):
    monkeypatch.setitem(sys.modules, "foley", None)  # would raise if reached
    script, source = script_and_source
    receipt = braidio.weave_project(project, script, source=source, return_receipt=True)
    assert receipt.sound_design is None
    assert _tier(project, "sound-design-config") == []
    assert _tier(project, "sound-design") == []


def test_a_declared_design_scores_the_spoken_episode(
    project, script_and_source, patched_synthesis, foley
):
    script, source = script_and_source
    plain = braidio.weave_project(
        project, script, source=source, profile=braidio.Profile.PUBLISHED
    )
    receipt = braidio.weave_project(
        project,
        script,
        source=source,
        profile=braidio.Profile.PUBLISHED,
        sound_design=braidio.SoundDesign(max_events=2),
        return_receipt=True,
    )
    assert receipt.episode.id == plain.id  # the episode is not touched
    (call,) = foley.calls
    # narration only: the clip contributes its label to the graph, not a segment
    assert call["segments"] == [
        "Opening line about the song.",
        "Closing thought on the hook.",
    ]
    starts = call["segment_starts"]
    assert starts == sorted(starts) and starts[0] == 0.0
    assert call["audio"].endswith(f"{plain.id}.mp3")
    assert (call["commercial_ok"], call["max_events"]) == (True, 2)

    design = receipt.sound_design
    assert design is not None and design.tier == "sound-design"
    assert design.body["credits"] == {"entries": [{"title": "Door", "by": "X"}]}
    assert receipt.has_unknown_costs  # foley reports no spend: unknown, not $0
    assert braidio.transforms.sound_design_of(project, plain).id == design.id
    (cfg,) = _tier(project, "sound-design-config")
    assert cfg.body["enabled"] is True and cfg.body["commercial_ok"] is True


def test_a_noop_reweave_reuses_the_design(
    project, script_and_source, patched_synthesis, foley
):
    script, source = script_and_source
    design = braidio.SoundDesign()
    first = braidio.weave_project(
        project, script, source=source, sound_design=design, return_receipt=True
    )
    again = braidio.weave_project(
        project, script, source=source, sound_design=design, return_receipt=True
    )
    assert len(foley.calls) == 1
    assert again.sound_design.id == first.sound_design.id
    assert len(_tier(project, "sound-design")) == 1


def test_a_changed_knob_redesigns_and_dropping_it_stales_the_design(
    project, script_and_source, patched_synthesis, foley
):
    import nw

    script, source = script_and_source
    first = braidio.weave_project(
        project,
        script,
        source=source,
        sound_design=braidio.SoundDesign(),
        return_receipt=True,
    ).sound_design
    second = braidio.weave_project(
        project,
        script,
        source=source,
        sound_design=braidio.SoundDesign(max_events=1),
        return_receipt=True,
    ).sound_design
    assert len(foley.calls) == 2 and second.id != first.id
    assert foley.calls[1]["max_events"] == 1

    plain = braidio.weave_project(project, script, source=source)
    assert _tier(project, "sound-design-config") == []
    verdicts = {v.annotation.id: v for v in nw.stale_verdicts_all(project.root)}
    assert verdicts[second.id].is_stale
    # a dropped design is not the episode's current one: no credits to ship
    assert braidio.transforms.sound_design_of(project, plain) is None


def test_the_personal_profile_admits_non_commercial_sounds(
    project, script_and_source, patched_synthesis, foley
):
    script, source = script_and_source
    braidio.weave_project(
        project,
        script,
        source=source,
        profile=braidio.Profile.PERSONAL,
        sound_design=braidio.SoundDesign(),
    )
    assert foley.calls[0]["commercial_ok"] is False


def test_without_foley_the_weave_refuses_before_any_voice(
    project, script_and_source, patched_synthesis, monkeypatch
):
    spoken = []
    monkeypatch.setattr(
        braidio, "narrate", lambda *a, **kw: spoken.append(a) or (a[1], False)
    )
    monkeypatch.setitem(sys.modules, "foley", None)
    script, source = script_and_source
    with pytest.raises(ModuleNotFoundError, match=r"braidio\[sound\]"):
        braidio.weave_project(
            project, script, source=source, sound_design=braidio.SoundDesign()
        )
    assert spoken == [] and _tier(project, "narrative-beats") == []


def test_a_custom_designer_is_refused_on_the_graph(
    project, script_and_source, patched_synthesis, foley
):
    script, source = script_and_source
    with pytest.raises(ValueError, match="fast path only"):
        braidio.weave_project(
            project,
            script,
            source=source,
            sound_design=braidio.SoundDesign(designer=lambda *a, **k: None),
        )


def test_a_failed_design_keeps_the_episode_on_the_receipt(
    project, script_and_source, patched_synthesis, foley, monkeypatch
):
    """The episode was made (and its voices paid for) before the design failed."""

    def broken(*a, **kw):
        raise RuntimeError("library offline")

    monkeypatch.setattr(foley, "score", broken)
    script, source = script_and_source
    with pytest.raises(Exception) as info:
        braidio.weave_project(
            project, script, source=source, sound_design=braidio.SoundDesign()
        )
    receipt = info.value.weave_receipt
    assert receipt.episode is not None and receipt.episode.tier == "episode-renders"
    assert receipt.has_unknown_costs


def test_an_episode_with_nothing_spoken_is_refused(project, tmp_path):
    from types import SimpleNamespace as NS

    from braidio.transforms._sound import EpisodeToSoundDesign, episode_talk

    episode = NS(id="e", body={"ordered_member_ids": [], "timeline": {"beats": []}})
    with pytest.raises(ValueError, match="no spoken text"):
        episode_talk(project, episode)
    with pytest.raises(RuntimeError, match="while planning"):
        EpisodeToSoundDesign().narration_segments(episode)


def _designed(project, script_and_source):
    script, source = script_and_source
    receipt = braidio.weave_project(
        project,
        script,
        source=source,
        sound_design=braidio.SoundDesign(),
        return_receipt=True,
    )
    return script, source, receipt


def _design_dir(project):
    return project.root.joinpath(*braidio.transforms._sound.SOUND_DESIGN_DIR)


def test_a_design_is_retrievable_by_id_and_in_the_status_door(
    project, script_and_source, patched_synthesis, foley
):
    """braidio#193: the mix is served from the project's delivery catalog under
    its recorded hash (never a file:// url), and the status door names it with
    the credits that ship with it."""
    from nw.media_catalog import HostArtifactCatalog

    script, source = script_and_source
    braidio.weave_project(project, script, source=source)
    assert "sound_design" not in project.genre_status()  # the absence rule
    _, _, receipt = _designed(project, script_and_source)
    design = receipt.sound_design
    artifact_id = design.body["artifact_id"]
    catalog = HostArtifactCatalog(project.root)
    assert artifact_id in catalog.registered_ids()
    blob = catalog.blob_path(artifact_id)
    assert blob is not None and blob.read_bytes().startswith(b"flac:")
    assert not any("file://" in str(row) for row in catalog.iter_rows())
    row = project.genre_status()["sound_design"]
    assert row == {
        "design_id": str(design.id),
        "artifact_id": artifact_id,
        "duration_s": design.body["duration_s"],
        "retrievable": True,
        "credits": {"entries": [{"title": "Door", "by": "X"}]},
    }


def test_registering_twice_copies_nothing_and_a_stale_design_is_not_served(
    project, script_and_source, patched_synthesis, foley, monkeypatch
):
    script, source, receipt = _designed(project, script_and_source)
    import nw.atomic_io

    copies = []
    real = nw.atomic_io.copy_file_atomic
    monkeypatch.setattr(
        nw.atomic_io,
        "copy_file_atomic",
        lambda *a, **k: (copies.append(a), real(*a, **k)),
    )
    assert braidio.transforms.register_sound_design(project, receipt.sound_design)
    assert copies == []  # already registered: nothing is copied again
    braidio.weave_project(project, script, source=source)  # sound design dropped
    assert "sound_design" not in project.genre_status()


def test_a_pruned_blob_warns_and_is_not_reported_retrievable(
    project, script_and_source, patched_synthesis, foley
):
    """A blob evicted from falaw's store cannot be served: registration warns
    instead of raising, and the status door says so (review of #193). nw's own
    reuse path still refuses such a design: braidio#228."""
    from falaw.content import default_content_store

    _, _, receipt = _designed(project, script_and_source)
    artifact_id = receipt.sound_design.body["artifact_id"]
    catalog_dir = project.root / ".reelee" / "artifacts"
    (catalog_dir / "blobs" / artifact_id).unlink(missing_ok=True)
    for row in (catalog_dir / "catalog").glob("*"):
        row.unlink()
    default_content_store().delete_blob(artifact_id)
    with pytest.warns(RuntimeWarning, match="pruned"):
        assert not braidio.transforms.register_sound_design(
            project, receipt.sound_design
        )
    assert project.genre_status()["sound_design"]["retrievable"] is False


def test_a_non_local_backend_is_refused_before_any_copy(
    project, script_and_source, patched_synthesis, foley, monkeypatch
):
    script, source = script_and_source
    monkeypatch.setenv("REELEE_ARTIFACT_BACKEND", "aws")
    with pytest.warns(RuntimeWarning, match="not registered"):
        receipt = braidio.weave_project(
            project,
            script,
            source=source,
            sound_design=braidio.SoundDesign(),
            return_receipt=True,
        )
    assert receipt.sound_design is not None  # the paid design is kept
    assert not list(_design_dir(project).glob("*"))


def test_an_opaque_blob_backend_is_spooled_through_a_temp_file(
    project, script_and_source, patched_synthesis, foley, monkeypatch
):
    from falaw.content import default_content_store

    store = default_content_store()
    monkeypatch.setattr(type(store), "blob_path", lambda self, h: None)
    _designed(project, script_and_source)
    assert project.genre_status()["sound_design"]["retrievable"] is True
    assert list(_design_dir(project).glob(".part-*")) == []


def test_large_credits_are_counted_not_carried_in_the_status_door():
    from types import SimpleNamespace

    from braidio.transforms._sound import STATUS_CREDITS_MAX_BYTES, sound_design_row

    big = {"entries": ["x" * 100] * (STATUS_CREDITS_MAX_BYTES // 50)}
    design = SimpleNamespace(
        id="d", body={"artifact_id": "a" * 64, "duration_s": 1.0, "credits": big}
    )
    row = sound_design_row(design, retrievable=True)
    assert row["credits"] is None and row["credits_omitted"] is True
