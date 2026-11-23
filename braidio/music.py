"""Music bed — an instrumental underscore laid under the whole production, ducked.

The bed is the spanning generalization of a per-clip ``under`` placement: instead
of one clip beneath one talk beat, an app-supplied **instrumental** asset runs
beneath the entire timeline at reduced gain, entering after speech onset and
fading at the ends (the conventions from
``docs/research/commentary-formats-and-styles.md``: bed 10–15 dB under voice,
~1.5s fade-in, *posted* after the start; beds must be instrumental).

braidio ships **no music** — the caller supplies the asset (a licensed/owned
instrumental). A :class:`~braidio.formats.Format`'s ``music_bed`` *intensity*
(continuous / light / sparse / none) picks a sensible gain via
:data:`BED_GAIN_BY_INTENSITY`; :func:`render_format` builds the bed for you when
given a ``bed_asset``.

**Fade-to-spotlight** (:mod:`braidio.structure`) is expressed here as bed
*regions*: :func:`bed_regions` cuts the bed's span into the stretches around the
spotlit windows, each with its own fades, and :func:`prepare_bed_regions` renders
them so the weave mixes each in at its own start. With no spotlit window the bed
is one region rendered by :func:`prepare_bed` — the unchanged single-file path.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Bed gain (dB under the mixed voice) by a Format's music_bed intensity.
# None means "no bed" for that intensity.
BED_GAIN_BY_INTENSITY: dict[str, float | None] = {
    "continuous": -20.0,
    "light": -24.0,
    "sparse": -28.0,
    "none": None,
}

# Shortest bed span ffmpeg is asked to render (guards a degenerate length).
_MIN_BED_LENGTH_S = 0.05
# How quickly the bed drops out before a spotlit exhibit ("cut music out
# immediately before the single most important line/clip" — the research).
_DEFAULT_SPOTLIGHT_FADE_S = 0.6


@dataclass(frozen=True)
class MusicBed:
    """An instrumental underscore spanning the production, mixed under the talk.

    ``asset_path`` is an app-supplied instrumental. ``gain_db`` sets how far under
    the voice it sits; ``lead_in_s`` posts the bed *after* speech onset so its
    entrance feels motivated; ``start_s`` is the in-point into the asset;
    ``loop`` repeats the asset to cover a timeline longer than it.
    ``spotlight_fade_s`` is how fast the bed drops out before a spotlit exhibit
    (it resumes after the exhibit over ``fade_in_s``).
    """

    asset_path: str
    gain_db: float = -22.0
    fade_in_s: float = 1.5
    fade_out_s: float = 2.0
    lead_in_s: float = 2.0
    start_s: float = 0.0
    loop: bool = True
    spotlight_fade_s: float = _DEFAULT_SPOTLIGHT_FADE_S


@dataclass(frozen=True)
class BedRegion:
    """One stretch of the timeline the bed plays over, with its own fades.

    ``start_s`` / ``end_s`` are timeline offsets (the bed's own ``lead_in_s`` is
    already applied). The bed keeps its place in the asset across a gap: a
    region seeks into the asset to where the music *would* have been.
    """

    start_s: float
    end_s: float
    fade_in_s: float
    fade_out_s: float

    @property
    def length_s(self) -> float:
        """Length in seconds (``end_s - start_s``)."""
        return self.end_s - self.start_s


def _merged_windows(windows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sort ``(start, end)`` windows and merge the ones that touch or overlap."""
    merged: list[tuple[float, float]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def bed_regions(
    bed: MusicBed, total_s: float, spotlights: list[tuple[float, float]]
) -> list[BedRegion]:
    """Cut the bed's span ``[lead_in_s, total_s]`` around the ``spotlights``.

    Pure function. Each spotlight ``(start, end)`` (timeline seconds) opens a
    gap: the bed fades *out* over ``bed.spotlight_fade_s`` to be silent by
    ``start``, and fades back *in* over ``bed.fade_in_s`` from ``end``. The first
    region keeps the bed's own fade-in and the last its fade-out; a region that
    would be empty (a spotlight at the very top, two spotlights back to back) is
    dropped. With no spotlights the result is the single whole-span region.
    """
    span_start, span_end = bed.lead_in_s, total_s
    edges: list[float] = [span_start]
    for start, end in _merged_windows(spotlights):
        edges.append(min(max(start, span_start), span_end))
        edges.append(min(max(end, span_start), span_end))
    edges.append(span_end)
    regions: list[BedRegion] = []
    last = len(edges) // 2 - 1
    for k in range(len(edges) // 2):
        start, end = edges[2 * k], edges[2 * k + 1]
        if end - start <= 0:
            continue
        regions.append(
            BedRegion(
                start_s=start,
                end_s=end,
                fade_in_s=bed.fade_in_s,
                fade_out_s=bed.fade_out_s if k == last else bed.spotlight_fade_s,
            )
        )
    return regions


def bed_for_intensity(asset_path: str, intensity: str, **overrides) -> MusicBed | None:
    """Build a :class:`MusicBed` at the gain for a Format ``music_bed`` intensity.

    Returns ``None`` for ``"none"`` (or an unknown intensity), so callers can do
    ``bed = bed_for_intensity(asset, fmt.music_bed)`` and skip when falsy.
    """
    gain = BED_GAIN_BY_INTENSITY.get(intensity)
    if gain is None:
        return None
    return MusicBed(asset_path=asset_path, gain_db=gain, **overrides)


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH (brew install ffmpeg).")


def _render_bed_span(
    bed: MusicBed,
    out_path: str | Path,
    *,
    seek_s: float,
    length_s: float,
    fade_in_s: float,
    fade_out_s: float,
    sample_rate: int,
) -> Path:
    """Render ``length_s`` of ``bed`` from ``seek_s`` into the asset, with fades,
    ``gain_db`` and stereo baked in. The one ffmpeg call behind every bed span."""
    _require_ffmpeg()
    length = max(_MIN_BED_LENGTH_S, length_s)
    fo_start = max(0.0, length - fade_out_s)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pre_input: list[str] = []
    if bed.loop:
        pre_input += ["-stream_loop", "-1"]
    if seek_s > 0:
        pre_input += ["-ss", f"{seek_s:.3f}"]
    af = (
        f"afade=t=in:st=0:d={fade_in_s},"
        f"afade=t=out:st={fo_start:.3f}:d={fade_out_s},"
        f"volume={bed.gain_db}dB,"
        f"aformat=sample_rates={sample_rate}:channel_layouts=stereo"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            *pre_input,
            "-i",
            str(bed.asset_path),
            "-t",
            f"{length:.3f}",
            "-af",
            af,
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def prepare_bed(
    bed: MusicBed, target_s: float, out_path: str | Path, *, sample_rate: int = 44100
) -> Path:
    """Render ``bed`` to a ready-to-mix underscore of length ``target_s - lead_in_s``.

    Seeks to ``bed.start_s`` (looping if needed), trims to the covered length, and
    bakes in fades + ``gain_db`` + stereo. The caller mixes the result delayed by
    ``bed.lead_in_s``. Returns ``out_path``.
    """
    return _render_bed_span(
        bed,
        out_path,
        seek_s=bed.start_s,
        length_s=target_s - bed.lead_in_s,
        fade_in_s=bed.fade_in_s,
        fade_out_s=bed.fade_out_s,
        sample_rate=sample_rate,
    )


def prepare_bed_regions(
    bed: MusicBed,
    regions: list[BedRegion],
    out_dir: str | Path,
    *,
    stem: str,
    sample_rate: int = 44100,
) -> list[tuple[Path, float]]:
    """Render each :class:`BedRegion` to ``out_dir/<stem>-<k>.mp3``.

    Returns ``[(path, start_s), …]`` — the caller mixes each file in delayed by
    its ``start_s``. A region seeks into the asset to where the bed would have
    been had it played through (so the music resumes in place after a
    spotlight), wrapping around the asset's length when the bed loops. A
    non-looping bed that has already run out by a region's start has nothing
    to play there: that region is dropped (seeking past the end would yield an
    empty file the mix can't open).
    """
    from braidio.weave import duration_s

    asset_len = duration_s(bed.asset_path)
    rendered: list[tuple[Path, float]] = []
    for k, region in enumerate(regions):
        seek = bed.start_s + (region.start_s - bed.lead_in_s)
        if bed.loop:
            seek %= asset_len
        elif seek >= asset_len:
            continue
        path = _render_bed_span(
            bed,
            Path(out_dir) / f"{stem}-{k}.mp3",
            seek_s=seek,
            length_s=region.length_s,
            fade_in_s=region.fade_in_s,
            fade_out_s=region.fade_out_s,
            sample_rate=sample_rate,
        )
        rendered.append((path, region.start_s))
    return rendered
