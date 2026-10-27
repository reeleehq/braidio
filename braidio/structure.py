"""Structural music — stings at scene breaks, fade-to-spotlight on exhibits.

The music bed (:mod:`braidio.music`) is the *continuous* music layer. This module
is the *structural* one: the cues that tell a listener where they are, because
audio has no visual white space (``misc/docs/research/commentary-formats-and-
styles.md``, "Music as structure"):

- a **sting** — a short musical marker — at a :class:`~braidio.script.SceneBreak`
  ("new section");
- **fade-to-spotlight** — the bed drops *out* before a key exhibit so the clip
  lands in silence instead of competing with underscore, and resumes after it.

Both are driven by a :class:`MusicStructure`: the format's defaults
(``scene_marker``, ``spotlight_clips``) plus the production's :class:`Sting`
asset. It is the single seam :func:`braidio.render.render_production` takes
(``structure=``); ``None`` means "no structure" and reproduces the plain weave
exactly. A beat overrides the defaults with ``SceneBreak.marker`` /
``SegmentBeat.spotlight``.

braidio ships **no music**: like the bed, the sting is an app-supplied asset
(``render_format(..., sting_asset=…)``). A scene break with no sting to play is
still audible — as :attr:`MusicStructure.pause_s` of silence — so the structure
is never silently lost.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from braidio.script import SCENE_MARKERS, SceneBreak, SegmentBeat

# A sting is levelled to the production target, then sat this far under the
# voice so it marks the boundary without shouting over the talk.
_DEFAULT_STING_GAIN_DB = -6.0
_DEFAULT_STING_MAX_LEN_S = 3.0
_DEFAULT_STING_FADE_OUT_S = 0.5
_DEFAULT_STING_GAP_AFTER_S = 0.3
# Silence at a break that has no sting to play: the boundary as white space.
_DEFAULT_BREAK_PAUSE_S = 0.8
# Levelling for a sting part (matches the renderer's true-peak ceiling).
_STING_TRUE_PEAK = -1.5


@dataclass(frozen=True)
class Sting:
    """A short musical marker played at a scene break.

    ``asset_path`` is an app-supplied sound (a hit, a riser, a few notes of the
    theme). It is trimmed to ``max_len_s`` with a ``fade_out_s`` tail, levelled
    to the production's loudness target, sat ``gain_db`` under the voice, and
    followed by ``gap_after_s`` of breathing room before the talk resumes.
    """

    asset_path: str
    gain_db: float = _DEFAULT_STING_GAIN_DB
    max_len_s: float = _DEFAULT_STING_MAX_LEN_S
    fade_out_s: float = _DEFAULT_STING_FADE_OUT_S
    gap_after_s: float = _DEFAULT_STING_GAP_AFTER_S


@dataclass(frozen=True)
class MusicStructure:
    """How a production marks its structure with music — the render seam.

    ``scene_marker`` is the default for a :class:`~braidio.script.SceneBreak`
    whose ``marker`` is ``None`` (``"sting"`` / ``"none"``). ``spotlight_clips``
    is the default for a :class:`~braidio.script.SegmentBeat` whose
    ``spotlight`` is ``None`` — ``True`` drops the bed under every exhibit.
    ``sting`` is the production's sting asset; with none supplied a
    ``"sting"``-marked break falls back to ``pause_s`` of silence.

    The all-defaults value is inert for a script with no scene breaks and no
    spotlight-marked clips: a format that declares no structure renders exactly
    as it did without this layer.
    """

    sting: Sting | None = None
    scene_marker: str = "sting"
    spotlight_clips: bool = False
    pause_s: float = _DEFAULT_BREAK_PAUSE_S

    def __post_init__(self) -> None:
        if self.scene_marker not in SCENE_MARKERS:
            raise ValueError(
                f"MusicStructure.scene_marker must be one of {SCENE_MARKERS}, "
                f"got {self.scene_marker!r}"
            )

    # The ``*_of`` methods take the per-beat override as plain data, because the
    # graph path (braidio.transforms) holds it as a body field rather than a
    # beat object; the ``*_for`` methods are the beat-taking front doors. One
    # resolution rule, two callers.

    def marker_of(self, marker: str | None) -> str:
        """The marker a break with this per-beat override renders with."""
        return marker if marker is not None else self.scene_marker

    def marker_for(self, beat: SceneBreak) -> str:
        """The marker a scene break renders with (its override, else the default)."""
        return self.marker_of(beat.marker)

    def plays_sting_of(self, marker: str | None) -> bool:
        """Whether this per-beat marker plays the sting (and one is supplied)."""
        return self.sting is not None and self.marker_of(marker) == "sting"

    def plays_sting(self, beat: SceneBreak) -> bool:
        """Whether ``beat`` plays the sting (marked ``"sting"`` *and* one is supplied)."""
        return self.plays_sting_of(beat.marker)

    def spotlight_of(self, spotlight: bool | None) -> bool:
        """Whether this per-beat override is spotlit (``None`` = the format default)."""
        return spotlight if spotlight is not None else self.spotlight_clips

    def spotlight_for(self, beat: SegmentBeat) -> bool:
        """Whether ``beat`` is spotlit (its override, else the format default)."""
        return self.spotlight_of(beat.spotlight)


DEFAULT_STRUCTURE = MusicStructure()


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH (brew install ffmpeg).")


def prepare_sting(
    sting: Sting,
    out_path: str | Path,
    *,
    target_lufs: float,
    sample_rate: int = 44100,
) -> Path:
    """Render ``sting`` to a ready-to-place part: trimmed, faded, levelled, padded.

    The result is already at production level (``target_lufs`` then
    ``sting.gain_db``), so the caller places it as-is rather than normalizing
    it again. The level is one measured gain with a true-peak limit
    (:func:`braidio.leveling.level_part`, as every spoken part gets), not a
    gain ridden through the sting. Returns ``out_path``.
    """
    _require_ffmpeg()
    from braidio.leveling import level_part
    from braidio.weave import duration_s

    length = min(duration_s(sting.asset_path), sting.max_len_s)
    fo_start = max(0.0, length - sting.fade_out_s)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    def _ffmpeg(src: Path, af: str, dst: Path) -> Path:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-af", af, str(dst)],
            check=True,
            capture_output=True,
        )
        return dst

    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".sting-") as work:
        work = Path(work)
        shaped = _ffmpeg(
            Path(sting.asset_path),
            f"atrim=end={length:.3f},afade=t=out:st={fo_start:.3f}:d={sting.fade_out_s}",
            work / "shaped.wav",
        )
        leveled = level_part(
            shaped,
            work / "leveled.wav",
            target_lufs=target_lufs,
            true_peak_db=_STING_TRUE_PEAK,
            sample_rate=sample_rate,
        )
        # The pad is a filter, not an output `-t`, and comes after the level:
        # the breathing room must render, and silence must not be measured.
        _ffmpeg(
            leveled,
            f"volume={sting.gain_db}dB,apad=pad_dur={sting.gap_after_s},"
            f"aformat=sample_rates={sample_rate}:channel_layouts=stereo",
            out,
        )
    return out


def prepare_pause(
    seconds: float, out_path: str | Path, *, sample_rate: int = 44100
) -> Path:
    """Render ``seconds`` of stereo silence — a scene break with no sting to play."""
    _require_ffmpeg()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={sample_rate}:cl=stereo",
            "-t",
            f"{seconds:.3f}",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out
