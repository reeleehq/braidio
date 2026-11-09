"""Per-line dialogue takes (braidio#145): a timed take, one line re-rendered.

The ElevenLabs client is a fake that returns real audio (a distinct sine tone
per voice, written as WAV in Python) with voice segments that say exactly where
each tone is — so every assertion is made on decoded PCM against known
positions. No network, no spend.
"""

from __future__ import annotations

import base64
import io
import json
import math
import wave
from array import array

import pytest

from _audio import AUDIBLE_RMS, RATE, SILENT_RMS, decode, needs_ffmpeg, rms

import braidio
from braidio.conversation import ConversationCast
from braidio.dialogue_lines import (
    DialogueTake,
    DialogueTimingError,
    line_cost,
    render_dialogue_lines,
    rerender_line,
)
from braidio.cost import RATE_ENV_VAR, tts_cost_usd

SENTINEL = "sk-SENTINEL-145-must-never-persist"
CAST = ConversationCast(roles={"A": "voiceA", "B": "voiceB"})
TURNS = [
    ("A", "So the bass line is the hook."),
    ("B", "Wait, really? Not the chorus?"),
    ("A", "Listen again."),
]
FMT = "wav_44100"
HZ = {"voiceA": 330.0, "voiceB": 550.0}
LEAD_S, GAP_S, TAIL_S = 0.10, 0.20, 0.15  # silence around and between turns
S_PER_CHAR = 0.01  # a turn's spoken length: 10 ms per character (441 samples)
MP3_SLACK_S = 0.03  # one mp3 frame (1152 samples) of encoder padding
ROOM_DIV = 8  # room tone at 1/8 of a voice's level (amplitude 1000)


def _samples(t: float) -> int:
    return round(t * RATE)


def _wav(pcm: array) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _tone(hz: float, n: int) -> array:
    return array(
        "h", (int(8000 * math.sin(2 * math.pi * hz * k / RATE)) for k in range(n))
    )


def _timed_take(inputs, *, drop=(), decay_s=0.0, room_hz=None):
    """What the fake API answers: audio + where each input is in it.

    Input 1 of a multi-input call is reported as TWO segments (a turn may span
    several); ``drop`` omits the segments of those inputs. ``decay_s``: each
    voice keeps sounding that long past its reported segment end (speech
    decays past the last aligned character), eating into the pause after it.
    ``room_hz``: a quiet tone under the whole file, as room tone."""
    from elevenlabs.types import (
        AudioWithTimestampsAndVoiceSegmentsResponseModel,
        VoiceSegment,
    )

    pcm, segs, t = array("h", [0] * _samples(LEAD_S)), [], LEAD_S
    for i, inp in enumerate(inputs):
        if i:
            pcm.extend([0] * _samples(GAP_S - decay_s))
            t += GAP_S - decay_s
        dur = len(inp["text"]) * S_PER_CHAR
        pcm.extend(_tone(HZ[inp["voice_id"]], _samples(dur + decay_s)))
        cuts = [(t, t + dur / 2), (t + dur / 2, t + dur)] if i == 1 else [(t, t + dur)]
        if i not in drop:
            segs += [
                VoiceSegment(
                    voice_id=inp["voice_id"],
                    start_time_seconds=a,
                    end_time_seconds=b,
                    character_start_index=0,
                    character_end_index=len(inp["text"]),
                    dialogue_input_index=i,
                )
                for a, b in cuts
            ]
        t += dur + decay_s
    pcm.extend([0] * _samples(TAIL_S))
    if room_hz is not None:
        room = _tone(room_hz, len(pcm))
        pcm = array("h", (x + r // ROOM_DIV for x, r in zip(pcm, room)))
    return AudioWithTimestampsAndVoiceSegmentsResponseModel(
        audio_base_64=base64.b64encode(_wav(pcm)).decode(),
        voice_segments=segs,
    )


def _as_mp3(take):
    """The same take, as the mp3 bytes ElevenLabs' default format returns."""
    import subprocess

    wav = base64.b64decode(take.audio_base_64)
    mp3 = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "wav",
            "-i",
            "-",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-f",
            "mp3",
            "-",
        ],
        input=wav,
        capture_output=True,
        check=True,
    ).stdout
    return take.model_copy(update={"audio_base_64": base64.b64encode(mp3).decode()})


@pytest.fixture
def api(monkeypatch, tmp_path):
    """The fake ElevenLabs: records each client's key and each call's inputs.

    The default dialogue cache is pointed into ``tmp_path``: a call left on
    ``cache=True`` must never write the user's real cache (nor hit it)."""
    from braidio.tts import DIALOGUE_CACHE_ENV_KEY

    monkeypatch.setenv(DIALOGUE_CACHE_ENV_KEY, str(tmp_path / "default-cache"))
    rec = {"keys": [], "calls": [], "drop": (), "mp3": False, "fake": {}, "edit": None}

    class _Dialogue:
        def convert_with_timestamps(self, **kw):
            rec["calls"].append(kw)
            take = _timed_take(kw["inputs"], drop=rec["drop"], **rec["fake"])
            if rec["edit"] is not None:  # a test's own twist on the answer
                take = rec["edit"](kw, take)
            return _as_mp3(take) if rec["mp3"] else take

    class _Client:
        def __init__(self, *, api_key=None, **_):
            rec["keys"].append(api_key)
            self.text_to_dialogue = _Dialogue()

    monkeypatch.setattr("elevenlabs.client.ElevenLabs", _Client)
    return rec


def _expected_spans(turns):
    t, spans = LEAD_S, []
    for i, (_, text) in enumerate(turns):
        t += GAP_S if i else 0.0
        dur = len(text) * S_PER_CHAR
        spans.append((t, dur))
        t += dur
    return spans


def _render(tmp_path, **kw):
    return render_dialogue_lines(
        TURNS,
        CAST,
        out_path=tmp_path / "take.wav",
        output_format=FMT,
        cache=tmp_path / "cache",
        **kw,
    )


@needs_ffmpeg
def test_lines_are_timed_by_the_voice_segments(tmp_path, api):
    take = _render(tmp_path)
    assert [(ln.index, ln.role, ln.text) for ln in take.lines] == [
        (i, r, t) for i, (r, t) in enumerate(TURNS)
    ]
    for ln, (off, dur) in zip(take.lines, _expected_spans(TURNS)):
        assert ln.offset_s == pytest.approx(off) and ln.duration_s == pytest.approx(dur)
    # one pass, all turns, each in its role's voice
    assert len(api["calls"]) == 1
    assert api["calls"][0]["inputs"] == [
        {"voice_id": CAST.roles[r], "text": t} for r, t in TURNS
    ]


def test_a_cache_hit_returns_the_same_lines_without_a_call(tmp_path, api):
    first, live = _render(tmp_path, return_cache_status=True)
    again, cached = _render(tmp_path, return_cache_status=True)
    assert (live, cached) == (False, True)
    assert len(api["calls"]) == 1  # the hit made no API call
    assert again.lines == first.lines
    _render(tmp_path, refresh=True)  # a re-roll does call
    assert len(api["calls"]) == 2


def test_missing_timings_are_named_not_guessed(tmp_path, api):
    api["drop"] = (2,)
    with pytest.raises(DialogueTimingError, match=r"turn\(s\) \[2\]"):
        _render(tmp_path)


def test_bad_role_and_unspliceable_format_refused_before_spending(tmp_path, api):
    with pytest.raises(KeyError, match="'C'"):
        render_dialogue_lines([("C", "hi")], CAST, out_path=tmp_path / "x.wav")
    with pytest.raises(ValueError, match="pcm_44100"):
        render_dialogue_lines(
            TURNS, CAST, out_path=tmp_path / "x.pcm", output_format="pcm_44100"
        )
    assert api["calls"] == [] and api["keys"] == []


@needs_ffmpeg
def test_rerender_line_replaces_only_that_line(tmp_path, api):
    take = _render(tmp_path)
    before = decode(take.path)
    old = take.lines[1]
    new_text = "Wait. Really?"  # shorter: the take gets shorter
    new = rerender_line(take, 1, text=new_text, out_path=tmp_path / "edited.wav")

    # only that line's characters were submitted, in its role's voice
    assert api["calls"][-1]["inputs"] == [{"voice_id": "voiceB", "text": new_text}]

    after = decode(new.path)
    delta = (len(new_text) - len(old.text)) * S_PER_CHAR
    lo, hi = _samples(old.offset_s), _samples(old.end_s)
    # the audio before the line is untouched, sample for sample
    assert after[:lo] == before[:lo]
    # the new line is its own voice segment (not the solo take's silences)
    assert new.lines[1].duration_s == pytest.approx(len(new_text) * S_PER_CHAR)
    assert after[lo : lo + _samples(new.lines[1].duration_s)] == _tone(
        HZ["voiceB"], _samples(len(new_text) * S_PER_CHAR)
    )
    # everything after it is the old audio, shifted by the change in duration
    assert after[hi + _samples(delta) :] == before[hi:]
    assert len(after) - len(before) == _samples(delta)
    assert new.lines[2].offset_s == pytest.approx(take.lines[2].offset_s + delta)
    assert new.lines[0] == take.lines[0]
    assert new.turns[1] == ("B", new_text) and new.lines[1].text == new_text
    # the original file is left alone when out_path is given
    assert decode(take.path) == before


@needs_ffmpeg
def test_rerender_in_place_and_from_a_stored_take(tmp_path, api):
    take = _render(tmp_path)
    stored = json.loads(json.dumps(take.to_dict()))
    restored = DialogueTake.from_dict(stored)
    assert restored == take
    before = len(decode(take.path))
    new = rerender_line(restored, 2, text="Listen again, closely.")
    assert new.path == take.path  # default: in place
    longer = (len("Listen again, closely.") - len(TURNS[2][1])) * S_PER_CHAR
    assert new.lines[2].duration_s == pytest.approx(take.lines[2].duration_s + longer)
    assert len(decode(new.path)) - before == _samples(longer)
    assert new.lines[:2] == take.lines[:2]


@needs_ffmpeg
def test_rerender_line_in_the_default_mp3_format(tmp_path, api):
    """mp3 re-encodes, so not sample-exact — but the durations and the voices
    must still land where the timings say (one codec frame of slack)."""
    api["mp3"] = True
    take = render_dialogue_lines(
        TURNS, CAST, out_path=tmp_path / "take.mp3", cache=False
    )
    before = decode(take.path)
    new = rerender_line(take, 1, text="Wait. Really?", out_path=tmp_path / "ed.mp3")
    after = decode(new.path)
    delta = new.lines[1].duration_s - take.lines[1].duration_s
    assert delta == pytest.approx(
        (len("Wait. Really?") - len(TURNS[1][1])) * S_PER_CHAR
    )
    assert (len(after) - len(before)) / RATE == pytest.approx(delta, abs=MP3_SLACK_S)
    for ln in new.lines:  # each line is voiced where it says, the gaps are not
        mid = ln.offset_s + ln.duration_s / 2
        assert rms(after, mid - 0.02, mid + 0.02) > AUDIBLE_RMS
        assert rms(after, ln.offset_s - 0.08, ln.offset_s - 0.02) < SILENT_RMS


def test_line_cost_quotes_that_line_at_the_casts_model(tmp_path, api, monkeypatch):
    monkeypatch.delenv(RATE_ENV_VAR, raising=False)
    take = _render(tmp_path)
    own = line_cost(take, 1)
    assert own.characters == len(TURNS[1][1])
    assert own.usd == tts_cost_usd(TURNS[1][1], model_id=CAST.model_id)
    assert own.lines[0].model_id == CAST.model_id
    assert line_cost(take, 1, text="Hm.").characters == 3
    with pytest.raises(IndexError):
        line_cost(take, 3)


@needs_ffmpeg
def test_the_callers_key_reaches_the_client_and_is_never_persisted(tmp_path, api):
    take = render_dialogue_lines(
        TURNS,
        CAST,
        out_path=tmp_path / "out" / "take.wav",
        output_format=FMT,
        cache=tmp_path / "cache",
        api_key=SENTINEL,
    )
    rerender_line(take, 0, text="So: the bass.", api_key=SENTINEL)
    assert api["keys"] == [SENTINEL, SENTINEL]
    assert SENTINEL not in json.dumps(take.to_dict())
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert any(p.suffix == ".json" for p in written)  # the timed cache entries
    for p in written:
        assert SENTINEL.encode() not in p.read_bytes(), p


def test_text_to_dialogue_cache_key_is_unchanged(tmp_path, monkeypatch):
    """The refactor shared helpers with the timed call; the plain take's key
    must be what it always was, or every cached production re-bills."""
    from mixing import _cache

    import braidio.tts as tts

    class _Client:
        def __init__(self, **_):
            self.text_to_dialogue = type(
                "D", (), {"convert": lambda self, **kw: [b"TAKE"]}
            )()

    monkeypatch.setattr("elevenlabs.client.ElevenLabs", _Client)
    turns = [("v1", "hello there"), ("v2", "hey")]
    tts.text_to_dialogue(turns, cache=tmp_path, seed=7, settings={"stability": 0.45})
    key = _cache.sha256_key(
        "text_to_dialogue",
        json.dumps(
            [{"voice_id": v, "text": t} for v, t in turns],
            sort_keys=True,
            ensure_ascii=False,
        ),
        tts.DIALOGUE_MODEL_ID,
        "mp3_44100_128",
        json.dumps({"stability": 0.45}, sort_keys=True),
        "7",
    )
    assert _cache.read_cache(tmp_path, key, suffix=".audio") == b"TAKE"


def test_the_line_api_is_exported():
    for name in (
        "render_dialogue_lines",
        "rerender_line",
        "line_cost",
        "DialogueTake",
        "DialogueLine",
        "DialogueTimingError",
        "text_to_dialogue_with_timestamps",
        "DialogueSegment",
    ):
        assert hasattr(braidio, name), name


# --- review fixes (braidio#145): every test below failed on the first cut ---


def _snr_db(got, ref) -> float:
    err = math.sqrt(sum((x - y) ** 2 for x, y in zip(got, ref)) / len(ref))
    sig = math.sqrt(sum(x * x for x in ref) / len(ref))
    return math.inf if err == 0 else 20 * math.log10(sig / err)


def _max_step(pcm, lo: int, hi: int) -> int:
    return max(abs(pcm[k] - pcm[k - 1]) for k in range(lo, hi))


@needs_ffmpeg
def test_a_one_line_take_spanning_its_whole_file_rerenders(tmp_path, api):
    """No piece of the take survives the cut: the splice is the new line alone."""

    def whole_file(kw, take):
        n = sum(len(i["text"]) for i in kw["inputs"]) * S_PER_CHAR
        pcm = _tone(330.0, _samples(n))
        seg = take.voice_segments[0].model_copy(
            update={"start_time_seconds": 0.0, "end_time_seconds": n}
        )
        return take.model_copy(
            update={
                "audio_base_64": base64.b64encode(_wav(pcm)).decode(),
                "voice_segments": [seg],
            }
        )

    api["edit"] = whole_file
    take = render_dialogue_lines(
        [("A", "hi")], CAST, out_path=tmp_path / "one.wav", output_format=FMT
    )
    new = rerender_line(take, 0, text="hello", out_path=tmp_path / "two.wav")
    after = decode(new.path)
    assert new.lines[0].offset_s == 0.0
    assert new.lines[0].duration_s == pytest.approx(0.05)
    assert len(after) == _samples(0.05)
    assert rms(after, 0.01, 0.04) > AUDIBLE_RMS


@needs_ffmpeg
def test_mp3_edits_do_not_compound_generational_loss(tmp_path, api):
    """Six in-place edits of line 1: line 0 is as good after the sixth as after
    the first, because every deliverable is encoded once from the master."""
    api["mp3"] = True
    take = render_dialogue_lines(
        TURNS, CAST, out_path=tmp_path / "take.mp3", cache=False
    )
    lo, hi = _samples(take.lines[0].offset_s), _samples(take.lines[0].end_s)
    ref = decode(take.path)[lo:hi]
    snrs, cur = [], take
    for k in range(6):
        cur = rerender_line(cur, 1, text="Wait, really?" + "?" * (k % 2), cache=False)
        snrs.append(_snr_db(decode(cur.path)[lo:hi], ref))
    assert snrs[0] > 20
    assert snrs[-1] >= snrs[0] - 0.5, snrs
    assert cur.master.suffix == ".flac" and cur.master.is_file()


@needs_ffmpeg
def test_a_stale_take_is_refused_before_spending(tmp_path, api):
    take = _render(tmp_path)
    newer = rerender_line(take, 0, text="So: the bass line is the hook.")
    calls = len(api["calls"])
    with pytest.raises(braidio.StaleTakeError, match="changed"):
        rerender_line(take, 2, text="Hear it.")  # the take before the edit
    newer.master.unlink()
    with pytest.raises(braidio.StaleTakeError, match="missing"):
        rerender_line(newer, 2, text="Hear it.")
    assert len(api["calls"]) == calls


@needs_ffmpeg
def test_the_new_lines_decay_is_kept_and_the_old_ones_is_gone(tmp_path, api):
    """Voices decay past their last aligned character. Cutting in the pause,
    not on the segment edge, keeps the new line's decay and drops the old's.
    (The new text puts the new tone half a cycle out of phase with the old.)"""
    decay = 0.03
    api["fake"] = {"decay_s": decay}
    take = _render(tmp_path)
    new_text = "Wait. Really?!"
    new = rerender_line(take, 1, text=new_text, out_path=tmp_path / "ed.wav")
    after = decode(new.path)
    lo = _samples(new.lines[1].offset_s)
    n = _samples(len(new_text) * S_PER_CHAR + decay)
    assert after[lo : lo + n] == _tone(HZ["voiceB"], n)  # decay included
    gap_lo = new.lines[1].end_s + decay + 0.002
    assert rms(after, gap_lo, new.lines[2].offset_s - 0.002) < SILENT_RMS


@needs_ffmpeg
def test_joins_through_room_tone_are_faded(tmp_path, api):
    """A cut through room tone joins two unrelated waveforms; the fade at
    each join keeps the step there to the order of the tone's own. (At 210 Hz
    the take's room tone is far from zero at the first join, the line's is at
    zero: a hard cut there steps by ~600.)"""
    api["fake"] = {"room_hz": 210.0}
    take = _render(tmp_path)
    before = decode(take.path)
    natural = _max_step(before, _samples(0.01), _samples(0.09))  # room tone only
    new = rerender_line(take, 1, text="Wait. Really?!", out_path=tmp_path / "ed.wav")
    after = decode(new.path)
    lines = new.lines
    for join in (
        (lines[0].end_s + lines[1].offset_s) / 2,
        (lines[1].end_s + lines[2].offset_s) / 2,
    ):
        k = _samples(join)
        assert _max_step(after, k - 50, k + 50) <= 2 * natural, join


@needs_ffmpeg
def test_overlapping_turns_are_split_at_the_overlap_midpoint(tmp_path, api):
    def overlap(kw, take):
        if len(kw["inputs"]) == 1:
            return take
        segs = list(take.voice_segments)
        segs[0] = segs[0].model_copy(
            update={"end_time_seconds": segs[1].start_time_seconds + 0.1}
        )
        return take.model_copy(update={"voice_segments": segs})

    api["edit"] = overlap
    take = _render(tmp_path)
    (off1, _) = _expected_spans(TURNS)[1]
    mid = (off1 + 0.1 + off1) / 2
    assert take.lines[0].end_s == pytest.approx(mid)
    assert take.lines[1].offset_s == pytest.approx(mid)
    new = rerender_line(take, 0, text="So the bass.", out_path=tmp_path / "ed.wav")
    assert new.lines[1].offset_s >= new.lines[0].end_s - 1e-9
    after = decode(new.path)
    ln = new.lines[1]
    assert rms(after, ln.offset_s + 0.12, ln.offset_s + 0.16) > AUDIBLE_RMS


def test_a_turn_timed_inside_another_is_refused(tmp_path, api):
    def inside(kw, take):
        segs = list(take.voice_segments)
        a, b = segs[0], segs[1]
        segs[0] = a.model_copy(update={"end_time_seconds": b.end_time_seconds + 0.5})
        return take.model_copy(update={"voice_segments": segs})

    api["edit"] = inside
    with pytest.raises(DialogueTimingError, match="turn 1 is timed inside"):
        _render(tmp_path)


@needs_ffmpeg
def test_empty_text_is_refused_before_spending(tmp_path, api):
    with pytest.raises(ValueError, match=r"turn\(s\) \[1\]"):
        render_dialogue_lines(
            [("A", "hi"), ("B", "  ")], CAST, out_path=tmp_path / "x.wav"
        )
    assert api["calls"] == []
    take = _render(tmp_path)
    with pytest.raises(ValueError, match="no text"):
        rerender_line(take, 1, text="")
    assert len(api["calls"]) == 1


@needs_ffmpeg
def test_a_rerender_without_a_segment_for_the_line_raises(tmp_path, api):
    take = _render(tmp_path)
    before = decode(take.path)
    api["drop"] = (0,)
    with pytest.raises(DialogueTimingError, match="without a voice segment"):
        rerender_line(take, 1, text="Wait. Really?")
    assert decode(take.path) == before  # nothing was spliced
    assert not [p for p in take.path.parent.iterdir() if p.name.startswith(".")]


@needs_ffmpeg
def test_from_dict_checks_the_stored_take(tmp_path, api):
    d = _render(tmp_path).to_dict()
    for broken, match in (
        ({"cast": {**d["cast"], "roles": {"A": "voiceA"}}}, "no voice for role"),
        ({"lines": [d["lines"][1], d["lines"][0], d["lines"][2]]}, "must have index"),
        (
            {
                "lines": [
                    d["lines"][0],
                    {**d["lines"][1], "offset_s": 0.2},
                    d["lines"][2],
                ]
            },
            "starts before line 0 ends",
        ),
    ):
        with pytest.raises(ValueError, match=match):
            DialogueTake.from_dict({**d, **broken})


@needs_ffmpeg
def test_the_callers_key_is_not_in_the_timed_cache_key(tmp_path, api):
    _render(tmp_path, api_key="key-one")
    _, cached = _render(tmp_path, api_key="key-two", return_cache_status=True)
    assert cached and len(api["calls"]) == 1


@needs_ffmpeg
def test_rerender_sends_the_seed_and_the_casts_model_and_settings(tmp_path, api):
    cast = ConversationCast(
        roles=CAST.roles, model_id="eleven_v3", settings={"stability": 0.3}
    )
    take = render_dialogue_lines(
        TURNS, cast, out_path=tmp_path / "t.wav", output_format=FMT, seed=3
    )
    rerender_line(take, 1, text="Wait. Really?", seed=7)
    first, line = api["calls"]
    for call in (first, line):
        assert call["model_id"] == "eleven_v3"
        assert call["settings"] == {"stability": 0.3}
    assert (first["seed"], line["seed"]) == (3, 7)


@needs_ffmpeg
def test_the_deliverable_is_encoded_at_the_formats_sample_rate(tmp_path, api):
    """The fake answers at 44.1 kHz whatever is asked; an edit's deliverable
    must still be at the rate ``output_format`` names."""
    from mixing.probe import probe

    take = render_dialogue_lines(
        TURNS, CAST, out_path=tmp_path / "t.wav", output_format="wav_22050"
    )
    new = rerender_line(take, 1, text="Wait. Really?")
    assert probe(new.path, count_packets=False).sample_rate == 22050
    after = decode(new.path)
    for ln in new.lines:
        mid = ln.offset_s + ln.duration_s / 2
        assert rms(after, mid - 0.02, mid + 0.02) > AUDIBLE_RMS


@needs_ffmpeg
def test_rerender_refuses_an_unspliceable_take_before_spending(tmp_path, api):
    from dataclasses import replace

    take = replace(_render(tmp_path), output_format="pcm_44100")
    with pytest.raises(ValueError, match="pcm_44100"):
        rerender_line(take, 1, text="Wait. Really?")
    assert len(api["calls"]) == 1


@needs_ffmpeg
def test_a_line_timed_past_its_audio_is_clamped_to_it(tmp_path, api):
    """A segment end past the solo file is clamped: the line's recorded
    duration is the audio it has, so the pause after it keeps its length."""

    def overshoot(kw, take):
        if len(kw["inputs"]) > 1:
            return take
        seg = take.voice_segments[0]
        late = seg.model_copy(update={"end_time_seconds": seg.end_time_seconds + 1.0})
        return take.model_copy(update={"voice_segments": [late]})

    api["edit"] = overshoot
    take = _render(tmp_path)
    new_text = "Wait. Really?"
    new = rerender_line(take, 1, text=new_text, out_path=tmp_path / "ed.wav")
    # the solo is lead + voice + tail: what follows its start is voice + tail
    assert new.lines[1].duration_s == pytest.approx(len(new_text) * S_PER_CHAR + TAIL_S)
    after = decode(new.path)
    assert len(after) / RATE == pytest.approx(new.lines[2].end_s + TAIL_S, abs=1e-4)


@needs_ffmpeg
def test_the_synthesize_seam_voices_the_line_without_the_client(tmp_path, api):
    from braidio.tts import DialogueSegment

    take = _render(tmp_path)
    seen = {}
    d = len("Hm.") * S_PER_CHAR

    def synth(voice_id, text, **kw):
        seen.update(kw, voice_id=voice_id, text=text)
        pcm = array("h", [0] * _samples(LEAD_S))
        pcm.extend(_tone(HZ[voice_id], _samples(d)))
        pcm.extend([0] * _samples(TAIL_S))
        return _wav(pcm), [DialogueSegment(0, voice_id, LEAD_S, LEAD_S + d)], False

    new = rerender_line(take, 1, text="Hm.", seed=5, api_key="k", synthesize=synth)
    assert len(api["keys"]) == 1  # only the take's own render built a client
    assert (seen["voice_id"], seen["text"], seen["seed"], seen["api_key"]) == (
        "voiceB",
        "Hm.",
        5,
        "k",
    )
    assert seen["settings"] == CAST.settings and seen["model_id"] == CAST.model_id
    assert new.lines[1].duration_s == pytest.approx(d)


@needs_ffmpeg
def test_a_last_line_timed_past_the_take_ends_with_the_take(tmp_path, api):
    def late_end(kw, take):
        segs = list(take.voice_segments)
        segs[-1] = segs[-1].model_copy(
            update={"end_time_seconds": segs[-1].end_time_seconds + TAIL_S + 0.05}
        )
        return take.model_copy(update={"voice_segments": segs})

    api["edit"] = late_end
    take = _render(tmp_path)
    total = len(decode(take.path)) / RATE
    assert take.lines[-1].end_s == pytest.approx(total)
    new = rerender_line(take, 2, text="Listen again!", out_path=tmp_path / "ed.wav")
    assert new.lines[-1].end_s <= len(decode(new.path)) / RATE + 1e-9


@needs_ffmpeg
def test_the_insert_window_is_exact_and_silent_outside_the_insert(tmp_path):
    """braidio#209: the one thing the splice still owns is which window of the
    re-rendered line goes in; outside the insert it is silence."""
    from braidio.dialogue_lines import _insert_window

    n = RATE // 2  # half a second of tone
    src = tmp_path / "solo.wav"
    src.write_bytes(_wav(_tone(440.0, n)))
    window = _insert_window(src, (-0.25, 0.75), rate=RATE)
    assert len(window) == RATE  # exactly keep1 - keep0 long
    quarter = RATE // 4
    assert float(abs(window[: quarter - 50]).max()) < 1e-4  # leading silence
    assert float(abs(window[quarter + 100 : quarter + n - 100]).max()) > 0.1  # the tone
    assert float(abs(window[quarter + n + 50 :]).max()) < 1e-4  # trailing silence
    with pytest.raises(ValueError, match="nothing to insert"):
        _insert_window(src, (0.5, 0.5), rate=RATE)


@needs_ffmpeg
def test_a_stored_take_round_trips_with_end_s_and_ignores_a_legacy_turns_key(
    tmp_path, api
):
    take = _render(tmp_path)
    d = take.to_dict()
    assert [ln["end_s"] for ln in d["lines"]] == pytest.approx(
        [ln.end_s for ln in take.lines]
    )
    assert DialogueTake.from_dict({**d, "turns": [["Z", "stale"]]}) == take
    assert take.turns == tuple((ln.role, ln.text) for ln in take.lines)


@needs_ffmpeg
def test_rerendering_the_first_line_keeps_its_lead_and_shifts_the_rest(tmp_path, api):
    take = _render(tmp_path)
    before = decode(take.path)
    new_text = "Hi"
    new = rerender_line(take, 0, text=new_text, out_path=tmp_path / "first.wav")
    after = decode(new.path)
    delta = (len(new_text) - len(take.lines[0].text)) * S_PER_CHAR
    lead = _samples(take.lines[0].offset_s)
    assert after[:lead] == before[:lead]  # the lead-in is untouched
    assert len(after) - len(before) == _samples(delta)
    assert new.lines[1].offset_s == pytest.approx(take.lines[1].offset_s + delta)


@needs_ffmpeg
def test_two_deliverables_in_one_folder_do_not_share_a_master(tmp_path, api):
    """take.wav and take.mp3 used to share take.master.flac (review of #145)."""
    a = _render(tmp_path)  # take.wav
    other = render_dialogue_lines(
        TURNS, CAST, out_path=tmp_path / "take.mp3", cache=tmp_path / "cache"
    )
    assert a.master != other.master
    assert a.master.name.startswith(a.path.name)


def test_scratch_files_are_unique_per_call(tmp_path):
    from braidio.dialogue_lines import _scratch

    one, two = (_scratch(tmp_path / "take.wav", "-line0", ".wav") for _ in range(2))
    assert one != two and one.exists() and two.exists()
    assert one.parent == tmp_path and one.name.startswith(".take-line0-")
