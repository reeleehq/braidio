"""Sound design on the no-graph render path (braidio#161).

The designer (foley in production) is injected through ``SoundDesign(designer=)``
and the mix writer is replaced, so nothing here imports foley, touches the
network or spends. What is pinned:

- off (no ``SoundDesign``) is the render it always was: the designer is never
  called and nothing is written beside the episode;
- on, the designer is handed only what is *spoken* (narration and dialogue
  text, audio tags stripped, one segment per sentence), placed at the starts
  the render's own timeline laid each beat at; a clip's words are not
  segments;
- the licence intent follows the rights profile;
- the credits and foley's timeline ship with the episode (a sidecar, and the
  render record when a timeline is asked for).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import braidio
import braidio.sound_design as sd
from braidio.sound_design import SOUND_SIDECAR_SUFFIX
from braidio.sound_design import SoundDesign, TalkSpan, talk_segments

from _audio import needs_ffmpeg, tone


class _Designer:
    """A stand-in for ``foley.score``: records its call, returns a fixed design."""

    def __init__(self):
        self.calls = []

    def __call__(self, segments, **kwargs):
        self.calls.append((list(segments), kwargs))
        weave = SimpleNamespace(
            audio="MIX",
            sr=48000,
            credits={"sounds": [{"id": "fs:1", "license": "CC0"}]},
            captions_vtt="WEBVTT\n",
            notes=["approximate placements"],
        )
        timeline = SimpleNamespace(to_dict=lambda: {"events": [{"cue": "door"}]})
        return SimpleNamespace(timeline=timeline, weave=weave)


def _mark(woven, dst):
    """A mix writer that marks the file it replaces (no soundfile needed)."""
    Path(dst).write_bytes(b"DESIGNED")


@pytest.fixture
def designer():
    """The fake designer."""
    return _Designer()


@pytest.fixture
def tone_voices(monkeypatch):
    import braidio.render as render_mod

    monkeypatch.setattr(
        braidio, "narrate", lambda text, out, **kw: tone(Path(out), 440, 1.0)
    )
    monkeypatch.setattr(
        braidio,
        "render_dialogue",
        lambda turns, cast, out_path, **kw: tone(Path(out_path), 550, 1.5),
    )


class _ToneSource:
    def __init__(self, path):
        self.path = path

    def resolve(self, reference):
        return braidio.ResolvedSegment(
            asset_path=self.path, start_s=0.0, end_s=1.0, score=1.0, matched_text=""
        )


def _render(tmp_path, *, sound_design=None, profile=braidio.Profile.PERSONAL):
    song = tone(tmp_path / "song.mp3", 660, 3.0)
    script = braidio.Script(
        title="t",
        id_slug="sd",
        beats=[
            braidio.Narration("[dryly] The door creaked. Rain fell."),
            braidio.SegmentBeat(reference="a lyric about thunder", rights="owned"),
            braidio.Narration("Then silence."),
            braidio.Dialogue(
                turns=[("host", "Did you hear that?"), ("guest", "Thunder.")]
            ),
        ],
    )
    return braidio.render_production(
        script,
        source=_ToneSource(song),
        profile=profile,
        out_path=tmp_path / "ep.mp3",
        normalize=False,
        end_fade_s=0.0,
        end_silence_s=0.0,
        return_timeline=True,
        sound_design=sound_design,
        tts_dir=tmp_path / "tts",
        clips_dir=tmp_path / "clips",
    )


@needs_ffmpeg
def test_off_never_calls_the_designer(tmp_path, tone_voices, designer):
    out, tl = _render(tmp_path)
    assert designer.calls == []
    assert not out.with_suffix(SOUND_SIDECAR_SUFFIX).exists()
    assert "sound_design" not in tl.settings
    assert out.read_bytes() != b"DESIGNED"


@needs_ffmpeg
def test_on_designs_only_the_spoken_beats_at_their_timeline_starts(
    tmp_path, tone_voices, designer
):
    out, tl = _render(
        tmp_path, sound_design=SoundDesign(designer=designer, writer=_mark)
    )
    ((segments, kwargs),) = designer.calls
    assert segments == [
        "The door creaked.",
        "Rain fell.",
        "Then silence.",
        "Did you hear that?",
        "Thunder.",
    ]
    talk = [b for b in tl.beats if b.kind != "clip"]
    starts = kwargs["segment_starts"]
    assert starts[0] == pytest.approx(talk[0].start, abs=1e-3)
    assert starts[2] == pytest.approx(talk[1].start, abs=1e-3)
    assert talk[0].start < starts[1] < talk[0].end
    assert all(a < b for a, b in zip(starts, starts[1:]))  # foley's requirement
    assert kwargs["audio"] == str(out)
    assert (kwargs["max_events"], kwargs["master"]) == (6, "podcast")
    assert out.read_bytes() == b"DESIGNED"


@needs_ffmpeg
@pytest.mark.parametrize(
    "profile, commercial", [("personal", False), ("published", True)]
)
def test_the_licence_intent_follows_the_profile(
    tmp_path, tone_voices, designer, profile, commercial
):
    _render(
        tmp_path,
        sound_design=SoundDesign(designer=designer, writer=_mark),
        profile=braidio.Profile(profile),
    )
    ((_, kwargs),) = designer.calls
    assert kwargs["commercial_ok"] is commercial


@needs_ffmpeg
def test_the_credits_ship_with_the_episode(tmp_path, tone_voices, designer):
    design = SoundDesign(
        designer=designer, writer=_mark, allow_generate=False, max_usd=0.5
    )
    out, tl = _render(tmp_path, sound_design=design)
    sidecar = json.loads(out.with_suffix(SOUND_SIDECAR_SUFFIX).read_text())
    assert sidecar["credits"]["sounds"][0]["license"] == "CC0"
    assert sidecar["timeline"] == {"events": [{"cue": "door"}]}
    assert sidecar["cost_usd"] is None  # unknown, never free
    assert tl.settings["resolved"]["master"] == "podcast"
    assert sidecar["design"] == {
        "max_events": 6,
        "master": "podcast",
        "max_usd": 0.5,
        "allow_generate": False,
    }
    assert tl.settings["sound_design"] == sidecar
    json.dumps(tl.to_dict())  # the record stays plain JSON


def test_talk_segments_estimate_sentence_starts_within_each_beat():
    segments, starts = talk_segments(
        [
            TalkSpan(start_s=10.0, duration_s=2.0, text="Late."),
            TalkSpan(start_s=0.0, duration_s=4.0, text="Aa. [pause] Bbbbbb."),
        ]
    )
    assert segments == ["Aa.", "Bbbbbb.", "Late."]  # in start order, tags gone
    assert starts == [0.0, 1.2, 10.0]  # "Aa." is 3 of 10 characters


def test_a_production_with_nothing_spoken_is_refused(tmp_path, designer):
    with pytest.raises(ValueError, match="no spoken text"):
        sd.score_mix(
            tmp_path / "x.mp3",
            [TalkSpan(0.0, 1.0, "[laughs]")],
            design=SoundDesign(designer=designer, writer=_mark),
            profile=braidio.Profile.PUBLISHED,
            out_path=tmp_path / "y.mp3",
        )
    assert designer.calls == []


def test_without_foley_the_error_names_the_extra(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "foley", None)
    with pytest.raises(ModuleNotFoundError, match=r"braidio\[sound\]"):
        sd._foley_score(["x"])


def test_crossfaded_beats_keep_every_start_strictly_increasing():
    """A beat starts before the previous one ends (the weave's crossfade); the
    estimates must still increase, or foley refuses the whole design after
    every voice has been paid for."""
    spans = [
        TalkSpan(0.0, 12.0, "A long opening sentence that goes on and on. No."),
        TalkSpan(11.88, 5.0, "Short. Then a longer second sentence."),
        TalkSpan(16.76, 1.0, "End."),
    ]
    _, starts = talk_segments(spans)
    assert all(a < b for a, b in zip(starts, starts[1:]))
    assert starts[2] == 11.88 and starts[4] == 16.76


def test_the_record_is_plain_json_even_with_numpy_scalars(tmp_path, designer):
    class _Scalar:  # what numpy hands back: not a float, but has .item()
        def item(self):
            return 0.25

    def _numpy_designer(segments, **kwargs):
        result = designer(segments, **kwargs)
        result.weave.credits = {"gain": _Scalar()}
        return result

    scored = sd.score_mix(
        tmp_path / "in.mp3",
        [TalkSpan(0.0, 1.0, "Hello.")],
        design=SoundDesign(designer=_numpy_designer, writer=_mark),
        profile=braidio.Profile.PUBLISHED,
        out_path=tmp_path / "out.mp3",
    )
    record = scored.to_dict()
    assert record["credits"] == {"gain": 0.25}
    assert (record["cost_usd"], record["cost_basis"]) == (None, "unknown")
    json.dumps(record)


def test_a_missing_extra_is_refused_before_anything_is_spent(
    tmp_path, tone_voices, monkeypatch
):
    """foley absent: the render refuses at the door, not after the voices."""
    import sys

    import braidio.render as render_mod

    spoken = []
    monkeypatch.setattr(
        braidio, "narrate", lambda text, out, **kw: spoken.append(text) or out
    )
    monkeypatch.setitem(sys.modules, "foley", None)
    with pytest.raises(ModuleNotFoundError, match=r"braidio\[sound\]"):
        _render(tmp_path, sound_design=SoundDesign())
    assert spoken == []
    assert SoundDesign(designer=_Designer(), writer=_mark).missing_dependencies() == []
    assert render_mod  # the fast path module stays importable without foley


@needs_ffmpeg
def test_under_a_bed_the_designer_hears_the_voice_and_the_bed_comes_back(
    tmp_path, tone_voices
):
    """braidio#262: the fast path matches the graph path (#227). With a music
    bed the designer is handed the voice stem, not the bedded episode, and the
    bed is laid back under its design. The stand-in design is silence as long as
    what it heard, so any sound in the episode is the bed laid back; the stems'
    temporary folder is gone afterwards."""
    import subprocess

    from _audio import AUDIBLE_RMS, decode, rms

    heard = []

    class _SilentDesigner(_Designer):
        def __call__(self, segments, **kwargs):
            heard.append(Path(kwargs["audio"]))
            design = super().__call__(segments, **kwargs)
            design.weave.audio = kwargs["audio"]  # the writer silences this file
            return design

    def _silence_of(woven, dst):
        subprocess.run(
            ["ffmpeg", "-y", "-i", woven.audio, "-af", "volume=0", str(dst)],
            check=True,
            capture_output=True,
        )

    bed = braidio.MusicBed(
        str(tone(tmp_path / "bed.wav", 110, 20.0)),
        gain_db=0.0,
        lead_in_s=0.0,
        fade_in_s=0.05,
        fade_out_s=0.05,
    )
    song = tone(tmp_path / "song.mp3", 660, 3.0)
    out = braidio.render_production(
        braidio.Script(
            title="t", id_slug="bed", beats=[braidio.Narration("Rain fell.")]
        ),
        source=_ToneSource(song),
        out_path=tmp_path / "ep.mp3",
        normalize=False,
        end_fade_s=0.0,
        end_silence_s=0.0,
        music_bed=bed,
        sound_design=SoundDesign(designer=_SilentDesigner(), writer=_silence_of),
        tts_dir=tmp_path / "tts",
    )
    (voice,) = heard
    assert voice.name == "voice.mp3" and voice != out
    assert not voice.parent.exists()  # the stems' folder is removed
    assert rms(decode(out), 0.1, 0.9) > AUDIBLE_RMS  # the bed is back
    record = json.loads(out.with_suffix(SOUND_SIDECAR_SUFFIX).read_text())
    assert "braidio#262" in record["bed"]


@needs_ffmpeg
def test_a_failed_design_under_a_bed_leaves_no_stems_behind(
    tmp_path, tone_voices, monkeypatch
):
    """The stems' temporary folder is removed whatever fails after it is made."""
    import braidio.render as render_mod

    made = []
    real = render_mod._stem_paths

    def recording(out):
        made.append(real(out))
        return made[-1]

    monkeypatch.setattr(render_mod, "_stem_paths", recording)

    def broken(segments, **kwargs):
        raise RuntimeError("the designer fell over")

    bed = braidio.MusicBed(str(tone(tmp_path / "bed.wav", 110, 20.0)), gain_db=0.0)
    with pytest.raises(RuntimeError, match="fell over"):
        braidio.render_production(
            braidio.Script(
                title="t", id_slug="bed", beats=[braidio.Narration("Rain.")]
            ),
            source=_ToneSource(tone(tmp_path / "song.mp3", 660, 3.0)),
            out_path=tmp_path / "ep.mp3",
            normalize=False,
            music_bed=bed,
            sound_design=SoundDesign(designer=broken, writer=_mark),
            tts_dir=tmp_path / "tts",
        )
    ((voice, _bed),) = made
    assert not voice.parent.exists()


def test_master_levels_read_the_designs_profile_and_fall_back():
    """A bed laid back under a design is mastered where the design asked."""
    profile = SimpleNamespace(target_lufs=-14.0, true_peak_db=-2.0)
    assert sd.master_levels(profile) == (-14.0, -2.0)
    assert sd.master_levels("no-such-profile", default_lufs=-18.0) == (-18.0, -1.0)
    foley = pytest.importorskip("foley")
    p = foley.resolve_master("streaming")
    assert sd.master_levels("streaming") == (p.target_lufs, p.true_peak_db)
