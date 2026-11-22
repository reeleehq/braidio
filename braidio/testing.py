"""Test helpers for hosts that build on braidio, importable without pytest (braidio#229).

:func:`timed_dialogue_stand_in` replaces the one paid call under
:func:`braidio.render_dialogue_lines` and :func:`braidio.rerender_line`, so a host
can test its own code offline with braidio's real take, master and splice running
(ffmpeg is still needed for those)::

    fake, calls = braidio.testing.timed_dialogue_stand_in()
    monkeypatch.setattr("braidio.dialogue_lines.text_to_dialogue_with_timestamps", fake)
"""

from __future__ import annotations

import io
import math
import wave
from array import array

#: Seconds of speech per character, the pause around and between turns, the
#: sample rate, the tone amplitude, and the base pitch and pitch step per voice.
SECONDS_PER_CHAR = 0.01
PAUSE_S = 0.2
SAMPLE_RATE = 44100
AMPLITUDE = 8000
BASE_HZ, HZ_STEP, HZ_STEPS = 220.0, 110.0, 5


def _tone_wav(spans: list[tuple[float, float, float]], total_s: float) -> bytes:
    """A mono WAV of ``total_s``: a sine at ``hz`` over each ``(start, end, hz)``."""
    pcm = array("h", [0] * round(total_s * SAMPLE_RATE))
    for start, end, hz in spans:
        for k in range(round(start * SAMPLE_RATE), round(end * SAMPLE_RATE)):
            pcm[k] = int(AMPLITUDE * math.sin(2 * math.pi * hz * k / SAMPLE_RATE))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def timed_dialogue_stand_in():
    """A stand-in for braidio's ONE paid dialogue call: ``(fake, calls)``.

    ``fake`` has the signature of :func:`braidio.tts.text_to_dialogue_with_timestamps`
    and answers with a sine tone per input (one pitch per voice, 10 ms per
    character, 0.2 s pauses) as WAV, plus the
    :class:`~braidio.tts.DialogueSegment`\\ s that time each input. Each call is
    appended to ``calls`` as ``{"inputs": [(voice_id, text), ...], "refresh": bool}``.

    >>> fake, calls = timed_dialogue_stand_in()
    >>> audio, segments = fake([("v", "hello")])
    >>> [(s.input_index, s.voice_id) for s in segments], calls[0]["inputs"]
    ([(0, 'v')], [('v', 'hello')])
    """
    from braidio.tts import DialogueSegment

    calls: list[dict] = []

    def fake(inputs, *, refresh=False, return_cache_status=False, **_kw):
        pairs = [
            (i["voice_id"], i["text"]) if isinstance(i, dict) else tuple(i)
            for i in inputs
        ]
        calls.append({"inputs": pairs, "refresh": refresh})
        spans, segments, t = [], [], PAUSE_S
        for i, (voice_id, text) in enumerate(pairs):
            end = t + len(text) * SECONDS_PER_CHAR
            hz = BASE_HZ + HZ_STEP * (sum(map(ord, voice_id)) % HZ_STEPS)
            spans.append((t, end, hz))
            segments.append(
                DialogueSegment(input_index=i, voice_id=voice_id, start_s=t, end_s=end)
            )
            t = end + PAUSE_S
        audio = _tone_wav(spans, t)
        return (audio, segments, False) if return_cache_status else (audio, segments)

    return fake, calls
