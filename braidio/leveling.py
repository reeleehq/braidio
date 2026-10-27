"""Level one spoken part to a loudness target with a single measured gain.

Every narration take, dialogue exchange and source clip is levelled before it
is woven, so a quiet clip sits beside a loud take. ffmpeg's one-pass
``loudnorm`` would do that *dynamically*: it rides the gain through the part,
flattening a voice's own dynamics (a whisper, an emphasis) and pumping quiet
passages. :func:`level_part` instead measures the part once and applies one
gain, then a true-peak limiter holds the ceiling. The measurement and the
limiter are :mod:`mixing.loudness` (the fleet's one loudness component);
this module only decodes, applies and encodes.

>>> peak_ceiling(DEFAULT_TRUE_PEAK_DB)  # doctest: +ELLIPSIS
0.84...
"""

from __future__ import annotations

import subprocess
from contextlib import closing
from pathlib import Path

import numpy as np
from mixing.decode import decode_float_chunks
from mixing.loudness import level_group, limit_peaks
from mixing.util import ffmpeg_exe

#: Integrated loudness a spoken part is levelled to, in LUFS.
DEFAULT_TARGET_LUFS = -16.0
#: True-peak ceiling of a levelled part, in dBTP.
DEFAULT_TRUE_PEAK_DB = -1.5
#: Sample rate every levelled part is written at, so the weave concatenates
#: parts of one rate whatever each source was.
OUTPUT_SAMPLE_RATE = 44100

_PCM_FORMAT = "f32le"  # how float samples travel to the encoder


def peak_ceiling(true_peak_db: float) -> float:
    """The linear ceiling (full scale 1.0) of a ``true_peak_db`` dBTP limit."""
    return 10.0 ** (true_peak_db / 20.0)


def _decode(src: Path) -> tuple[np.ndarray, int]:
    rate, chunks = decode_float_chunks(src)
    with closing(chunks):
        parts = list(chunks)
    return (np.concatenate(parts) if parts else np.zeros((0, 1), np.float32)), rate


def _encode(samples: np.ndarray, rate: int, dst: Path, *, sample_rate: int) -> None:
    subprocess.run(
        [
            ffmpeg_exe(),
            "-y",
            "-f",
            _PCM_FORMAT,
            "-ar",
            str(rate),
            "-ac",
            str(samples.shape[1]),
            "-i",
            "-",
            "-ar",
            str(sample_rate),  # the resample, as its own step
            str(dst),
        ],
        input=np.ascontiguousarray(samples, dtype="<f4").tobytes(),
        check=True,
        capture_output=True,
    )


def level_part(
    src: str | Path,
    dst: str | Path,
    *,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak_db: float = DEFAULT_TRUE_PEAK_DB,
    sample_rate: int = OUTPUT_SAMPLE_RATE,
) -> Path:
    """Write ``src`` to ``dst`` at ``target_lufs`` with one gain, true-peak limited.

    The gain is :func:`mixing.loudness.level_group` over the whole part (so the
    part keeps its own dynamics; silence gets none), the limiter
    :func:`mixing.loudness.limit_peaks` at ``true_peak_db``. Gains are applied
    in float, so nothing clips before the limiter. ``dst`` is written at
    ``sample_rate``, in the format its suffix names.
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    samples, rate = _decode(src)
    gain_db, _ = level_group([samples], rate, target_lufs=target_lufs)
    leveled = limit_peaks(
        samples * 10.0 ** (gain_db / 20.0),
        ceiling=peak_ceiling(true_peak_db),
        true_peak=True,
        fs=rate,
    )
    _encode(leveled, rate, dst, sample_rate=sample_rate)
    return dst
