"""``braidio.leveling.level_part``: one measured gain per spoken part, true-peak limited.

These measure the written file, not the call: a levelling that rode the gain
through the part (one-pass ``loudnorm``, what this replaced) passes a
"reaches the target" check while failing the "keeps its dynamics" one.
"""

import shutil

import numpy as np
import pytest
from scipy.io import wavfile

from braidio.leveling import DEFAULT_TRUE_PEAK_DB, OUTPUT_SAMPLE_RATE, level_part

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

_RATE = 48000


def _tone(seconds, amplitude, *, freq=440.0, rate=_RATE):
    t = np.arange(int(seconds * rate)) / rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _write(path, samples, rate=_RATE):
    wavfile.write(path, rate, samples)
    return path


def _read(path):
    from mixing.decode import decode_float_chunks

    rate, chunks = decode_float_chunks(path)
    return np.concatenate(list(chunks)), rate


def _lufs(samples, rate):
    from mixing.loudness import integrated_lufs

    return integrated_lufs(samples, rate)


def test_a_quiet_part_reaches_the_target(tmp_path):
    src = _write(tmp_path / "quiet.wav", _tone(3.0, 0.02))
    out, rate = _read(level_part(src, tmp_path / "out.wav", target_lufs=-20.0))
    assert _lufs(out, rate) == pytest.approx(-20.0, abs=0.3)


def test_one_gain_keeps_the_parts_own_dynamics(tmp_path):
    # a whisper then an emphasis, 14 dB apart: one gain keeps them 14 dB apart,
    # where a dynamic normaliser pulls them together
    whisper, emphasis = _tone(4.0, 0.02), _tone(4.0, 0.1)
    src = _write(tmp_path / "dyn.wav", np.concatenate([whisper, emphasis]))
    out, rate = _read(level_part(src, tmp_path / "out.wav", target_lufs=-20.0))
    half = len(out) // 2
    before = _lufs(emphasis[:, None], _RATE) - _lufs(whisper[:, None], _RATE)
    after = _lufs(out[half:], rate) - _lufs(out[:half], rate)
    assert after == pytest.approx(before, abs=0.5)


def test_the_true_peak_ceiling_holds_when_the_gain_would_clip(tmp_path):
    from mixing.loudness import true_peak_db

    # quiet body with one sharp peak: the gain to the target pushes the peak past 0 dBFS
    body = _tone(3.0, 0.01)
    body[_RATE : _RATE + 200] = 0.5
    src = _write(tmp_path / "peaky.wav", body)
    out, rate = _read(level_part(src, tmp_path / "out.wav", target_lufs=-14.0))
    assert true_peak_db(out, rate) <= DEFAULT_TRUE_PEAK_DB + 0.3


def test_every_part_is_written_at_one_rate(tmp_path):
    src = _write(tmp_path / "lo.wav", _tone(2.0, 0.1, rate=22050), rate=22050)
    _, rate = _read(level_part(src, tmp_path / "out.wav"))
    assert rate == OUTPUT_SAMPLE_RATE


def test_silence_stays_silent(tmp_path):
    src = _write(tmp_path / "silent.wav", np.zeros(_RATE * 2, np.float32))
    out, _ = _read(level_part(src, tmp_path / "out.mp3"))
    assert np.abs(out).max() < 1e-3
