"""Structural music (braidio#25): scene stings + fade-to-spotlight.

Offline. The audio tests synthesize every input with ffmpeg's lavfi sources
(a sine tone, digital silence — see ``tests/_audio.py``) and inspect the
*decoded* PCM of the render — never the encoded bytes, never a real TTS call
(``narrate`` is replaced by a tone writer). The characterization tests pin the
seam's default: a script with no scene break and no spotlit clip must render the
same decoded audio as the plain weave, and the bed's single-region renderer must
equal the old whole-span renderer.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import braidio
from braidio import (
    DEFAULT_STRUCTURE,
    MusicBed,
    MusicStructure,
    Narration,
    SceneBreak,
    Script,
    SegmentBeat,
    Sting,
    TimelineItem,
    estimate_cost,
    weave_timeline,
)
from braidio.music import BedRegion, bed_regions, prepare_bed, prepare_bed_regions
from braidio.rights import Profile, content_violations, plan_production

from _audio import (
    AUDIBLE_RMS,
    SILENT_RMS,
    FixedSource,
    decode,
    needs_ffmpeg,
    rms,
    seconds,
    silence,
    tone,
)


@pytest.fixture
def silent_narration(monkeypatch):
    """narrate() → a fixed length of silence, so only the music is audible.

    Any real synthesis would be a bug: the stub never touches the network.
    """
    import braidio.render as render_mod

    def fake_narrate(text, out_path, **kw):
        return silence(Path(out_path), 1.0)

    monkeypatch.setattr(render_mod, "narrate", fake_narrate)


def _render(script, tmp_path, **kw):
    """render_production with every non-structural post-step disabled."""
    kw.setdefault("source", None)
    kw.setdefault("normalize", False)
    return braidio.render_production(
        script,
        out_path=tmp_path / f"out-{len(list(tmp_path.iterdir()))}.mp3",
        end_fade_s=0.0,
        end_silence_s=0.0,
        tts_dir=tmp_path / "tts",
        clips_dir=tmp_path / "clips",
        **kw,
    )


# --- the model ---------------------------------------------------------------


def test_scene_break_defaults_and_validates_marker():
    assert SceneBreak().marker is None and SceneBreak().label == ""
    assert SceneBreak(marker="none", label="rebuttal").marker == "none"
    with pytest.raises(ValueError):
        SceneBreak(marker="fanfare")


def test_segment_spotlight_defaults_to_deferred():
    assert SegmentBeat("ref").spotlight is None
    assert SegmentBeat("ref", spotlight=True).spotlight is True


def test_music_structure_resolves_beat_overrides_over_format_defaults():
    st = MusicStructure(
        sting=Sting("s.wav"), scene_marker="sting", spotlight_clips=True
    )
    assert st.marker_for(SceneBreak()) == "sting"
    assert st.marker_for(SceneBreak(marker="none")) == "none"
    assert st.plays_sting(SceneBreak()) and not st.plays_sting(
        SceneBreak(marker="none")
    )
    assert st.spotlight_for(SegmentBeat("r")) is True
    assert st.spotlight_for(SegmentBeat("r", spotlight=False)) is False
    # no sting asset → a "sting" break cannot play one, whatever it asks for
    assert not MusicStructure(scene_marker="sting").plays_sting(SceneBreak())
    with pytest.raises(ValueError):
        MusicStructure(scene_marker="swell")


def test_default_structure_is_inert():
    assert DEFAULT_STRUCTURE.sting is None
    assert DEFAULT_STRUCTURE.spotlight_clips is False
    assert not DEFAULT_STRUCTURE.plays_sting(SceneBreak())
    assert DEFAULT_STRUCTURE.spotlight_for(SegmentBeat("r")) is False


def test_scene_break_is_planned_under_both_profiles_and_costs_nothing():
    script = Script(
        title="t",
        id_slug="01",
        beats=[Narration("a" * 10), SceneBreak(label="act 2"), Narration("b" * 10)],
    )
    for profile in (Profile.PERSONAL, Profile.PUBLISHED):
        plan = plan_production(script, profile)
        assert [b.kind for b in plan.beats] == ["narration", "scene_break", "narration"]
        assert plan.beats[1].from_index == 1
    assert content_violations(plan_production(script, Profile.PUBLISHED), ["x"]) == []
    est = estimate_cost(script)
    assert est.characters == 20 and len(est.lines) == 2


def test_every_format_declares_its_structure():
    for fmt in braidio.FORMATS.values():
        assert isinstance(fmt.structure, MusicStructure)
        assert (
            fmt.structure.sting is None
        )  # the asset is the caller's, never the preset's
    assert braidio.SOLO_EXPLAINER.structure.spotlight_clips is True
    assert braidio.SONG_EXPLODER.structure.scene_marker == "none"
    assert braidio.DEBATE.structure.scene_marker == "sting"


def test_render_format_threads_structure_and_builds_the_sting(monkeypatch):
    import braidio.render as render_mod

    captured = {}
    monkeypatch.setattr(
        render_mod,
        "render_production",
        lambda script, **kw: captured.update(kw) or "OUT",
    )
    braidio.render_format(braidio.PANEL, "S", source=None)
    assert captured["structure"] is braidio.PANEL.structure

    captured.clear()
    braidio.render_format(braidio.PANEL, "S", source=None, sting_asset="hit.wav")
    st = captured["structure"]
    assert st.sting == Sting("hit.wav")
    assert replace(st, sting=None) == braidio.PANEL.structure

    captured.clear()  # an explicit structure override wins over the asset
    mine = MusicStructure(scene_marker="none")
    braidio.render_format(
        braidio.PANEL, "S", source=None, sting_asset="h", structure=mine
    )
    assert captured["structure"] is mine


# --- bed regions (pure) ------------------------------------------------------

_BED = MusicBed(
    "bed.wav", fade_in_s=1.5, fade_out_s=2.0, lead_in_s=2.0, spotlight_fade_s=0.6
)


def test_no_spotlight_is_one_whole_span_region_with_the_beds_own_fades():
    assert bed_regions(_BED, 30.0, []) == [BedRegion(2.0, 30.0, 1.5, 2.0)]


def test_a_spotlight_splits_the_bed_with_a_quick_drop_and_a_slow_resume():
    regions = bed_regions(_BED, 30.0, [(10.0, 14.0)])
    assert regions == [
        BedRegion(2.0, 10.0, fade_in_s=1.5, fade_out_s=0.6),  # silent by the clip
        BedRegion(14.0, 30.0, fade_in_s=1.5, fade_out_s=2.0),  # back in after it
    ]
    assert regions[0].length_s == 8.0


def test_spotlight_windows_are_merged_clamped_and_empty_regions_dropped():
    # overlapping windows merge; one at the very top leaves no leading region;
    # one running past the end leaves no trailing region
    regions = bed_regions(_BED, 20.0, [(0.0, 5.0), (4.0, 6.0), (18.0, 25.0)])
    assert regions == [BedRegion(6.0, 18.0, fade_in_s=1.5, fade_out_s=0.6)]
    assert bed_regions(_BED, 20.0, [(1.0, 25.0)]) == []


# --- characterization: the default reproduces today's output ------------------


@needs_ffmpeg
def test_single_region_bed_decodes_identically_to_prepare_bed(tmp_path):
    """The bed renderer was refactored into spans; the whole-span case must be
    the same audio the old single-file path produced."""
    asset = tone(tmp_path / "bed.wav", 110, 3.0)
    bed = MusicBed(str(asset), gain_db=-12.0, lead_in_s=1.0, start_s=0.5)
    whole = prepare_bed(bed, 8.0, tmp_path / "whole.mp3")
    [(region, start_s)] = prepare_bed_regions(
        bed, bed_regions(bed, 8.0, []), tmp_path, stem="region"
    )
    assert start_s == bed.lead_in_s
    assert decode(whole) == decode(region)


@needs_ffmpeg
def test_default_structure_renders_the_plain_weave_exactly(tmp_path, silent_narration):
    """A script with no scene break and no spotlit clip, rendered under a format
    that *does* declare stings + spotlight, decodes identically to the plain
    weave of the same parts — the seam's default is a no-op."""
    bed = MusicBed(str(tone(tmp_path / "bed.wav", 110, 3.0)), lead_in_s=0.5)
    script = Script(title="t", id_slug="c", beats=[Narration("one"), Narration("two")])

    plain = weave_timeline(
        [
            TimelineItem("narration", str(silence(tmp_path / "p0.mp3", 1.0))),
            TimelineItem("narration", str(silence(tmp_path / "p1.mp3", 1.0))),
        ],
        tmp_path / "plain.mp3",
        clip_edge_overlap_s=0.0,
        narration_crossfade_s=0.12,
        bed=bed,
    )
    by_default = _render(script, tmp_path, music_bed=bed)
    declared = _render(
        script, tmp_path, music_bed=bed, structure=braidio.SOLO_EXPLAINER.structure
    )
    with_asset = _render(
        script,
        tmp_path,
        music_bed=bed,
        structure=MusicStructure(
            sting=Sting(str(tone(tmp_path / "hit.wav", 880, 1.0))),
            spotlight_clips=True,
        ),
    )
    reference = decode(plain)
    assert reference and rms(reference, 0.6, 1.9) > AUDIBLE_RMS  # the bed is there
    assert decode(by_default) == reference
    assert decode(declared) == reference
    assert decode(with_asset) == reference


# --- behaviour: stings + spotlight ------------------------------------------


@needs_ffmpeg
def test_scene_break_plays_the_sting_between_the_talk(tmp_path, silent_narration):
    sting = Sting(
        str(tone(tmp_path / "hit.wav", 880, 1.0)),
        gain_db=0.0,
        max_len_s=1.0,
        fade_out_s=0.2,
        gap_after_s=0.0,
    )
    script = Script(
        title="t",
        id_slug="s",
        beats=[Narration("one"), SceneBreak(label="act 2"), Narration("two")],
    )
    out, tl = _render(
        script,
        tmp_path,
        structure=MusicStructure(sting=sting),
        return_timeline=True,
    )
    pcm = decode(out)
    assert seconds(pcm) == pytest.approx(3.0, abs=0.3)
    assert rms(pcm, 0.1, 0.8) < SILENT_RMS  # talk (silent here)
    assert rms(pcm, 1.1, 1.7) > AUDIBLE_RMS  # the sting
    assert rms(pcm, 2.2, 2.9) < SILENT_RMS  # talk again
    assert [s.kind for s in tl.beats] == ["narration", "sting", "narration"]
    assert tl.beats[1].label == "act 2"


@needs_ffmpeg
def test_scene_break_without_a_sting_is_a_pause(tmp_path, silent_narration):
    script = Script(
        title="t", id_slug="p", beats=[Narration("one"), SceneBreak(), Narration("two")]
    )
    no_asset, tl = _render(script, tmp_path, return_timeline=True)
    assert seconds(decode(no_asset)) == pytest.approx(
        2.0 + DEFAULT_STRUCTURE.pause_s, abs=0.3
    )
    assert [s.kind for s in tl.beats] == ["narration", "scene-break", "narration"]

    # marked "none" → a pause even when the production has a sting
    quiet = Script(
        title="t",
        id_slug="q",
        beats=[Narration("one"), SceneBreak(marker="none"), Narration("two")],
    )
    sting = Sting(str(tone(tmp_path / "hit.wav", 880, 1.0)), gain_db=0.0)
    out = _render(quiet, tmp_path, structure=MusicStructure(sting=sting, pause_s=0.4))
    pcm = decode(out)
    assert seconds(pcm) == pytest.approx(2.4, abs=0.3)
    assert rms(pcm, 0.0, seconds(pcm) - 0.05) < SILENT_RMS


@needs_ffmpeg
@pytest.mark.parametrize("via", ["beat_flag", "format_default"])
def test_spotlight_drops_the_bed_under_the_clip_and_brings_it_back(
    tmp_path, silent_narration, via
):
    """Talk and clip are silent, the bed is a tone: with spotlight the clip's
    window is the only silent stretch; without it the bed plays throughout."""
    bed = MusicBed(
        str(tone(tmp_path / "bed.wav", 220, 3.0)),
        gain_db=0.0,
        lead_in_s=0.0,
        fade_in_s=0.1,
        fade_out_s=0.1,
        spotlight_fade_s=0.1,
    )
    source = FixedSource(silence(tmp_path / "src.wav", 4.0), start_s=0.5, end_s=1.5)
    if via == "beat_flag":
        clip, structure = SegmentBeat("x", spotlight=True), None
    else:
        clip, structure = SegmentBeat("x"), MusicStructure(spotlight_clips=True)
    script = Script(
        title="t", id_slug="sp", beats=[Narration("a"), clip, Narration("b")]
    )

    # config=None → no clip edge overlap, clip floor length 2.2 s:
    # narration 0–1, clip 1–3.2, narration 3.2–4.2
    lit = decode(
        _render(script, tmp_path, source=source, music_bed=bed, structure=structure)
    )
    assert rms(lit, 0.2, 0.8) > AUDIBLE_RMS  # bed under the set-up
    assert rms(lit, 1.3, 2.9) < SILENT_RMS  # bed out under the exhibit
    assert rms(lit, 3.6, 4.1) > AUDIBLE_RMS  # bed back under the payoff

    unlit_beat = SegmentBeat("x", spotlight=False)
    unlit = decode(
        _render(
            replace(script, beats=[Narration("a"), unlit_beat, Narration("b")]),
            tmp_path,
            source=source,
            music_bed=bed,
            structure=structure,
        )
    )
    assert rms(unlit, 1.3, 2.9) > AUDIBLE_RMS  # the per-beat False wins
    assert seconds(unlit) == pytest.approx(seconds(lit), abs=0.1)


@needs_ffmpeg
def test_spotlight_without_a_bed_is_inert(tmp_path, silent_narration):
    source = FixedSource(tone(tmp_path / "src.wav", 330, 4.0), start_s=0.5, end_s=1.5)
    beats = [Narration("a"), SegmentBeat("x", spotlight=True), Narration("b")]
    script = Script(title="t", id_slug="nb", beats=beats)
    lit = decode(_render(script, tmp_path, source=source))
    plain = decode(
        _render(
            replace(script, beats=[Narration("a"), SegmentBeat("x"), Narration("b")]),
            tmp_path,
            source=source,
        )
    )
    assert lit == plain


@needs_ffmpeg
def test_sting_gap_after_renders_as_trailingsilence(tmp_path):
    """The breathing room after a sting must survive the trim (a `-t` output
    option used to cut it off — PR #40 review)."""
    from braidio.structure import prepare_sting

    hit = tone(tmp_path / "hit.wav", 880, 1.0)
    for gap in (0.0, 0.3, 1.0):
        sting = Sting(str(hit), gain_db=0.0, max_len_s=1.0, gap_after_s=gap)
        pcm = decode(prepare_sting(sting, tmp_path / f"s{gap}.mp3", target_lufs=-16.0))
        assert seconds(pcm) == pytest.approx(1.0 + gap, abs=0.08)
        if gap:
            assert rms(pcm, 1.05, 1.0 + gap) < SILENT_RMS  # the gap is silence
    assert rms(pcm, 0.1, 0.7) > AUDIBLE_RMS  # and the sting itself is there


@needs_ffmpeg
def test_a_sting_is_levelled_with_one_gain_and_keeps_its_dynamics(tmp_path):
    """braidio#130: a quiet swell into a hit stays a swell into a hit. One-pass
    loudnorm (what this replaced) rode the gain over anything 3 s or longer and
    pulled the two together (under 3 s it fell back to one linear gain)."""
    import numpy as np
    from mixing.decode import decode_float_chunks
    from mixing.loudness import integrated_lufs as lufs
    from scipy.io import wavfile

    from braidio.structure import prepare_sting

    rate, each_s = 48000, 4
    t = np.arange(rate * each_s) / rate
    swell, hit = 0.02 * np.sin(2 * np.pi * 440 * t), 0.1 * np.sin(2 * np.pi * 440 * t)
    asset = tmp_path / "sting.wav"
    wavfile.write(asset, rate, np.concatenate([swell, hit]).astype(np.float32))
    sting = Sting(
        str(asset), gain_db=0.0, max_len_s=2 * each_s, fade_out_s=0.05, gap_after_s=0.0
    )

    got_rate, chunks = decode_float_chunks(
        prepare_sting(sting, tmp_path / "s.mp3", target_lufs=-20.0)
    )
    out = np.concatenate(list(chunks))
    n, edge = got_rate * each_s, got_rate // 10  # one half, and a margin off its edges

    spread_in = lufs(hit[:, None], rate) - lufs(swell[:, None], rate)
    spread_out = lufs(out[n + edge : 2 * n - edge], got_rate) - lufs(
        out[edge : n - edge], got_rate
    )
    assert spread_out == pytest.approx(spread_in, abs=0.7)
    assert lufs(out, got_rate) == pytest.approx(-20.0, abs=0.7)
    # gain_db is part of the level, so the limiter runs last whatever it is
    quieter = replace(sting, gain_db=-6.0)
    got_rate, chunks = decode_float_chunks(
        prepare_sting(quieter, tmp_path / "q.mp3", target_lufs=-20.0)
    )
    assert lufs(np.concatenate(list(chunks)), got_rate) == pytest.approx(-26.0, abs=0.7)


@needs_ffmpeg
def test_non_looping_bed_that_has_run_out_before_a_spotlight_does_not_crash(tmp_path):
    """A 3 s bed that does not loop, a spotlit clip after 4 s: the region after
    the clip would seek past the asset's end. It is dropped, and the mix still
    renders (it used to hand ffmpeg an empty file — PR #40 review)."""
    bed = MusicBed(
        str(tone(tmp_path / "bed.wav", 220, 3.0)),
        gain_db=0.0,
        loop=False,
        lead_in_s=0.0,
        fade_in_s=0.1,
        fade_out_s=0.1,
    )
    items = [
        TimelineItem("narration", str(silence(tmp_path / "n0.mp3", 4.0))),
        TimelineItem("clip", str(silence(tmp_path / "c.mp3", 4.0)), spotlight=True),
        TimelineItem("narration", str(silence(tmp_path / "n1.mp3", 4.0))),
    ]
    out = weave_timeline(items, tmp_path / "w.mp3", clip_edge_overlap_s=0.0, bed=bed)
    pcm = decode(out)
    assert seconds(pcm) == pytest.approx(12.0, abs=0.3)
    assert rms(pcm, 0.3, 2.7) > AUDIBLE_RMS  # the bed, while it lasts
    assert rms(pcm, 4.5, 11.5) < SILENT_RMS  # nothing after: no crash, no bed
    [(path, start_s)] = prepare_bed_regions(
        bed, bed_regions(bed, 12.0, [(4.0, 8.0)]), tmp_path, stem="r"
    )
    assert start_s == 0.0  # only the region the asset can cover survives
