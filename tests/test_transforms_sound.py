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

    braidio.weave_project(project, script, source=source)
    assert _tier(project, "sound-design-config") == []
    verdicts = {v.annotation.id: v for v in nw.stale_verdicts_all(project.root)}
    assert verdicts[second.id].is_stale


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
