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
from braidio.conversation import (
    ConversationCast,
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


def _timed_take(inputs, *, drop=()):
    """What the fake API answers: audio + where each input is in it.

    Input 1 of a multi-input call is reported as TWO segments (a turn may span
    several); ``drop`` omits the segments of those inputs."""
    from elevenlabs.types import (
        AudioWithTimestampsAndVoiceSegmentsResponseModel,
        VoiceSegment,
    )

    pcm, segs, t = array("h", [0] * _samples(LEAD_S)), [], LEAD_S
    for i, inp in enumerate(inputs):
        if i:
            pcm.extend([0] * _samples(GAP_S))
            t += GAP_S
        dur = len(inp["text"]) * S_PER_CHAR
        pcm.extend(_tone(HZ[inp["voice_id"]], _samples(dur)))
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
        t += dur
    pcm.extend([0] * _samples(TAIL_S))
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
    rec = {"keys": [], "calls": [], "drop": (), "mp3": False}

    class _Dialogue:
        def convert_with_timestamps(self, **kw):
            rec["calls"].append(kw)
            take = _timed_take(kw["inputs"], drop=rec["drop"])
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
