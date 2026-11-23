"""Sound design under a music bed: foley listens to the voice (braidio#227).

A production with a music bed used to hand foley the bedded mix, so its aligner
and ducking treated the bed as speech. Now the episode transform also writes
two stems of the same layout (the mix without the bed, and the bed alone), the
design listens to the voice stem, and the bed is laid back under the design's
mix, so the mix a host serves still has its bed. Without a bed nothing changes:
foley gets the episode's mix and the design's mix is foley's.

Real ffmpeg renders (``narrate`` writes silence, the bed is a tone, so "is the
bed there" is an RMS reading); foley is a stand-in that writes real audio.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import braidio

from _audio import (
    AUDIBLE_RMS,
    SILENT_RMS,
    FixedSource,
    decode,
    needs_ffmpeg,
    rms,
    silence,
    tone,
)

pytestmark = [
    pytest.mark.skipif(not braidio.HAS_NW, reason="nw (and lacing) not available"),
    needs_ffmpeg,
]

if braidio.HAS_NW:
    from falaw.testing import isolated_falaw_cache  # noqa: F401  (autouse fixture)


class _Serializable(SimpleNamespace):
    def to_dict(self):
        return dict(vars(self))


@pytest.fixture
def foley(monkeypatch):
    """A ``foley`` stand-in whose design is silence as long as what it was given:
    any sound in the served mix then came from the bed laid back under it."""
    import numpy as np
    import soundfile as sf

    mod = ModuleType("foley")
    mod.calls = []
    rate = 44100

    def score(segments, *, audio=None, segment_starts=None, **kw):
        mod.calls.append({"segments": list(segments), "audio": audio})
        n = int(braidio.duration_s(audio) * rate)
        return SimpleNamespace(
            timeline=_Serializable(items=()),
            events=[],
            weave=SimpleNamespace(
                audio=np.zeros((n, 2), dtype="float32"),
                sr=rate,
                credits=_Serializable(entries=()),
                captions_vtt="WEBVTT\n",
                master_report={"integrated_lufs": -16.0},
                notes=[],
            ),
        )

    def save(samples, sample_rate, dst, *, fmt="flac", subtype="PCM_24"):
        sf.write(dst, samples, sample_rate, format=fmt.upper(), subtype=subtype)

    mod.score = score
    mod.audio = SimpleNamespace(save=save)
    mod.Budget = lambda *, max_usd: SimpleNamespace(max_usd=max_usd)
    monkeypatch.setitem(sys.modules, "foley", mod)
    return mod


@pytest.fixture
def silent_narration(monkeypatch):
    def fake_narrate(text, out, *, return_cache_status=False, **kw):
        p = silence(Path(out), 1.0)
        return (p, False) if return_cache_status else p

    monkeypatch.setattr(braidio, "narrate", fake_narrate)


@pytest.fixture
def source(tmp_path):
    return FixedSource(tone(tmp_path / "song.wav", 440, 8.0), start_s=1.0, end_s=3.0)


@pytest.fixture
def bed(tmp_path):
    return braidio.MusicBed(
        str(tone(tmp_path / "bed.wav", 110, 12.0)),
        gain_db=0.0,
        lead_in_s=0.0,
        fade_in_s=0.05,
        fade_out_s=0.05,
    )


def _script():
    return braidio.Script(
        title="Bed",
        id_slug="bed",
        beats=[
            braidio.Narration(text="Rain fell on the roof."),
            braidio.Narration(text="Then the door shut."),
        ],
    )


def _path(url):
    from braidio.transforms._common import url_to_path

    return url_to_path(url)


def _mix(design) -> Path:
    from falaw.content import default_content_store

    return Path(default_content_store().blob_path(design.body["artifact_id"]))


def test_with_a_bed_foley_hears_the_voice_and_the_served_mix_keeps_the_bed(
    project, silent_narration, source, bed, foley
):
    receipt = braidio.weave_project(
        project,
        _script(),
        source=source,
        bed=bed,
        sound_design=braidio.SoundDesign(),
        return_receipt=True,
    )
    episode, design = receipt.episode, receipt.sound_design
    body = episode.body
    voice, bed_stem = _path(body["voice_url"]), _path(body["bed_url"])
    assert body["voice_artifact_id"] and body["bed_artifact_id"]

    # the stems: the voice has no bed in it (the narration is silence), the bed
    # stem is the bed, and the episode's own mix still has it
    assert rms(decode(voice), 0.1, 1.5) < SILENT_RMS
    assert rms(decode(bed_stem), 0.1, 1.5) > AUDIBLE_RMS
    assert rms(decode(_path(body["url"])), 0.1, 1.5) > AUDIBLE_RMS

    # foley is handed the voice stem, not the bedded mix (fails on the old path)
    (call,) = foley.calls
    assert Path(call["audio"]).name == voice.name
    assert call["segments"] == ["Rain fell on the roof.", "Then the door shut."]

    # foley's design is silence; the mix the design names has the bed laid back
    assert rms(decode(_mix(design)), 0.1, 1.5) > AUDIBLE_RMS
    assert any("braidio#227" in n for n in design.body["notes"])

    # and it is what a host is served (registered under the bedded mix's id)
    row = project.genre_status()["sound_design"]
    assert row["artifact_id"] == design.body["artifact_id"]
    assert row["retrievable"] is True


def test_a_noop_reweave_with_a_bed_reuses_the_design(
    project, silent_narration, source, bed, foley
):
    kw = dict(source=source, bed=bed, sound_design=braidio.SoundDesign())
    first = braidio.weave_project(project, _script(), return_receipt=True, **kw)
    again = braidio.weave_project(project, _script(), return_receipt=True, **kw)
    assert len(foley.calls) == 1
    assert again.sound_design.id == first.sound_design.id


def test_without_a_bed_foley_hears_the_episode_and_the_design_is_foleys(
    project, silent_narration, source, foley
):
    receipt = braidio.weave_project(
        project,
        _script(),
        source=source,
        sound_design=braidio.SoundDesign(),
        return_receipt=True,
    )
    episode, design = receipt.episode, receipt.sound_design
    assert not {"voice_artifact_id", "bed_artifact_id"} & {
        k for k, v in episode.body.items() if v
    }
    (call,) = foley.calls
    assert Path(call["audio"]).name == _path(episode.body["url"]).name
    assert not any("braidio#227" in n for n in design.body["notes"])
    assert rms(decode(_mix(design)), 0.1, 1.5) < SILENT_RMS


@pytest.fixture
def project(tmp_path):
    return braidio.Project.init(tmp_path / "proj", title="bed design")
