"""The woven master keeps the mix's dynamics (braidio#130).

``weave_timeline`` used to master with one-pass ``loudnorm``, which rides the
gain through the mix: a quiet passage between two loud ones is pulled up, undoing
the per-part levelling of braidio#124. It now masters with one measured gain and
a true-peak limit (:func:`mixing.loudness.level_file`). These tests measure the
decoded output, so they fail on the old master, not merely on its arguments.
"""

from __future__ import annotations

from math import log10
from pathlib import Path

from braidio.weave import TimelineItem, weave_timeline

from _audio import RATE, decode, lavfi, needs_ffmpeg, rms, tone

LOUD_S, QUIET_S = 4.0, 8.0
QUIET_DB = -20.0  # how far under the loud parts the middle one sits
EDGE_S = 0.5  # stay clear of each part's edges when measuring it


def _quiet_tone(path: Path, duration_s: float) -> Path:
    src = f"sine=frequency=440:sample_rate={RATE},volume={QUIET_DB}dB"
    return lavfi(path, src, duration_s)


def _render(tmp_path: Path, *, target_lufs: float = -16.0) -> Path:
    items = [
        TimelineItem("narration", str(tone(tmp_path / "a.wav", 440, LOUD_S))),
        TimelineItem("narration", str(_quiet_tone(tmp_path / "q.wav", QUIET_S))),
        TimelineItem("narration", str(tone(tmp_path / "b.wav", 440, LOUD_S))),
    ]
    return weave_timeline(
        items,
        tmp_path / "out.wav",
        narration_crossfade_s=0.0,
        target_lufs=target_lufs,
    )


@needs_ffmpeg
def test_a_quiet_passage_between_loud_ones_stays_quiet(tmp_path):
    pcm = decode(_render(tmp_path))
    loud = rms(pcm, EDGE_S, LOUD_S - EDGE_S)
    quiet = rms(pcm, LOUD_S + EDGE_S, LOUD_S + QUIET_S - EDGE_S)
    # one gain for the whole mix: the 20 dB gap survives (loudnorm closed it to
    # within its LRA target, so the middle came out well above -20 dB)
    assert abs(20 * log10(quiet / loud) - QUIET_DB) < 1.0


@needs_ffmpeg
def test_the_master_reaches_the_target_under_the_ceiling(tmp_path):
    import numpy as np
    from mixing.decode import decode_float_chunks
    from mixing.loudness import file_loudness, true_peak_db

    out = _render(tmp_path, target_lufs=-20.0)
    assert abs(file_loudness(out).integrated_lufs - -20.0) < 1.0
    rate, chunks = decode_float_chunks(out)
    assert true_peak_db(np.concatenate(list(chunks)), rate) <= -1.0 + 0.3
