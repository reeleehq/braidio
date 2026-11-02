"""Level one spoken part to a loudness target with a single measured gain.

Every narration take, dialogue exchange and source clip is levelled before it
is woven, so a quiet clip sits beside a loud take. ffmpeg's one-pass
``loudnorm`` would do that *dynamically*: it rides the gain through the part,
flattening a voice's own dynamics (a whisper, an emphasis) and pumping quiet
passages. :func:`level_part` instead measures the part once and applies one
gain, then a true-peak limiter holds the ceiling. All of that is
:func:`mixing.loudness.level_file` (the fleet's one loudness component,
streaming); this module only names braidio's defaults for a spoken part.
"""

from __future__ import annotations

from pathlib import Path

from mixing.loudness import level_file

#: Integrated loudness a spoken part is levelled to, in LUFS.
DEFAULT_TARGET_LUFS = -16.0
#: True-peak ceiling of a levelled part, in dBTP.
DEFAULT_TRUE_PEAK_DB = -1.5
#: Sample rate every levelled part is written at, so the weave concatenates
#: parts of one rate whatever each source was.
OUTPUT_SAMPLE_RATE = 44100


def level_part(
    src: str | Path,
    dst: str | Path,
    *,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak_db: float = DEFAULT_TRUE_PEAK_DB,
    sample_rate: int = OUTPUT_SAMPLE_RATE,
) -> Path:
    """Write ``src`` to ``dst`` at ``target_lufs`` with one gain, true-peak limited.

    :func:`mixing.loudness.level_file`: one gain measured over the whole part
    (so it keeps its own dynamics; silence gets none), applied in float, then
    a true-peak limiter at ``true_peak_db``. ``dst`` is written at
    ``sample_rate``, in the format its suffix names; the resample happens as
    the part is decoded, so the ceiling holds at the rate written.

    Bounds worth knowing:

    - The gain is capped at ``mixing.loudness.MAX_GAIN_DB`` (with a warning):
      a part far below the target comes out quieter than it, rather than as
      amplified noise (``loudnorm`` would have pulled it all the way up).
    - A lossy ``dst`` (mp3) can read a few tenths of a dB over the ceiling. A
      levelled part is an intermediate: the final master limits again.
    - Memory is bounded by the decode chunk, not the part's length.
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    level_file(
        src,
        dst,
        target_lufs=target_lufs,
        ceiling_db=true_peak_db,
        sample_rate=sample_rate,
    )
    return dst
