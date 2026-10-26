"""Weave narration + audio clips on a timeline (#21) — the reusable mix engine.

Two generic, Hamilton-agnostic primitives:

- :func:`extract_padded` — extract ``[start, end]`` from a source asset but
  **padded** by pre/post-roll (so the words are captured cleanly) with in/out
  fades on the padded edges. The padded, faded edges are what we tuck under the
  neighbouring narration.
- :func:`weave_timeline` — place ordered narration/clip parts on a timeline
  where each clip **overlaps its neighbours** by ``clip_edge_overlap_s`` and its
  faded padded edges duck under the speech, so **speech stays dominant**, then
  mix (``amix`` sum) and loudness-normalize.

This is the audio counterpart of a video timeline; it consumes plain file paths
+ numbers, no Genius/lyrics knowledge (that resolution lives in
``graph/align.py`` and the Hamilton adapters). Ducking here is achieved by
fade-shaped overlap; a dynamic sidechain duck (``duck_db``) is a documented
refinement (see #21 / the research).
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_LOUDNESS_RANGE = 11  # loudnorm LRA target for the mastered mix

#: Upper bound of the intelligible range for a speaker interruption: past it two
#: voices talking over each other stop being understood (Hamilton#22).
MAX_OVERLAP_TURN_S = 0.4


@dataclass(frozen=True)
class TurnOverlap:
    """How much the next speaker may start before the previous one finishes.

    The pure, render-time view of :class:`~braidio.weave_config.WeaveConfig`'s
    ``overlap_*`` knobs, so the layout and the mixer take one value instead of
    four loose floats. ``max_s == 0`` means no interruption (the historical
    butt-join with a crossfade). It lives here, not in ``weave_config``, so the
    weave stays free of the TTS/mixing imports that module carries.

    ``jitter`` shortens each overlap by a seeded random fraction up to that
    amount (so it is not mechanical); ``duck_db`` (≤ 0) lowers the outgoing voice
    over the overlap; ``seed`` makes both reproducible — ``WeaveConfig`` passes
    its ``voice_seed``, so one seed fixes the casting and the interruptions.

    The overlap is measured between *file* boundaries: the layout sees each
    part's duration only, so a beat that opens with its own lead silence
    (``Narration.lead_gap_s``) spends the overlap on that silence first — an
    incoming beat with a lead gap ≥ the overlap is not heard to interrupt.

    >>> TurnOverlap(max_s=0.5)
    Traceback (most recent call last):
        ...
    ValueError: TurnOverlap.max_s (WeaveConfig.overlap_turn_s) must be in [0, 0.4], got 0.5
    """

    max_s: float = 0.0
    jitter: float = 0.3
    duck_db: float = 0.0
    seed: int = 0

    def __post_init__(self) -> None:
        if not (0.0 <= self.max_s <= MAX_OVERLAP_TURN_S):
            raise ValueError(
                "TurnOverlap.max_s (WeaveConfig.overlap_turn_s) must be in "
                f"[0, {MAX_OVERLAP_TURN_S}], got {self.max_s}"
            )
        if not (0.0 <= self.jitter <= 1.0):
            raise ValueError(
                "TurnOverlap.jitter (WeaveConfig.overlap_jitter) must be in [0, 1], "
                f"got {self.jitter}"
            )
        if self.duck_db > 0:
            raise ValueError(
                "TurnOverlap.duck_db (WeaveConfig.overlap_duck_db) must be <= 0 "
                f"(it attenuates), got {self.duck_db}"
            )

    @property
    def is_on(self) -> bool:
        """Whether a change of speaker is laid out as an interruption."""
        return self.max_s > 0


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg/ffprobe not found on PATH (brew install ffmpeg).")


def duration_s(path: str | Path) -> float:
    _require_ffmpeg()
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def extract_padded(
    asset_path: str | Path,
    start_s: float,
    end_s: float,
    out_path: str | Path,
    *,
    pre_roll_s: float = 0.4,
    post_roll_s: float = 0.3,
    fade_in_s: float = 0.5,
    fade_out_s: float = 0.8,
    min_len_s: float = 2.2,
) -> Path:
    """Extract ``[start_s-pre_roll, end_s+post_roll]`` with in/out fades.

    The target words sit in the middle; the padded, faded head/tail are the
    parts that overlap (tuck under) neighbouring narration in the weave.

    Two guards keep **short** clips from sounding like they just swell in and
    out (no steady body):
    - the tail is extended so the clip is at least ``min_len_s`` long, giving the
      fade somewhere to breathe;
    - the fades are **adaptive** — capped to a fraction of the clip so they never
      swallow it (a 1.5 s clip gets ~0.3 s fades, not 0.5 s + 0.8 s).
    """
    _require_ffmpeg()
    start = max(0.0, start_s - pre_roll_s)
    end = end_s + post_roll_s
    # give a very short clip a steady body: extend the tail to a floor length
    if end - start < min_len_s:
        end = start + min_len_s
    dur = max(0.05, end - start)
    # adaptive fades: keep a steady middle at full level on short clips
    fi = max(0.03, min(fade_in_s, dur * 0.18))
    fo = max(0.03, min(fade_out_s, dur * 0.25))
    fo_start = max(0.0, dur - fo)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(asset_path),
            "-t",
            f"{dur:.3f}",
            "-af",
            f"afade=t=in:st=0:d={fi:.3f},afade=t=out:st={fo_start:.3f}:d={fo:.3f}",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


@dataclass(frozen=True)
class TimelineItem:
    """One part on the weave timeline.

    ``placement`` ``"sequential"`` (default — narration, and clean ``before`` /
    ``after`` clips) lays the part in its own slot. ``"under"`` overlays the part
    beneath the *following* sequential part (it does not consume its own slot),
    attenuated by ``duck_db`` — a ducked underlay (a clip talked over, or later a
    music bed).

    ``spotlight`` marks the part the music bed drops out for (fade-to-spotlight):
    the bed is silent over this part's span and resumes after it. Inert without
    a bed.
    """

    kind: str  # "narration" | "clip" | "sting" (a sting/pause lays out like narration)
    path: str
    placement: str = "sequential"  # "sequential" | "under"
    duck_db: float = 0.0  # attenuation applied when placement == "under" (e.g. -15)
    spotlight: bool = False  # the bed drops out over this part (see braidio.structure)
    # who talks (voice id); None = no speaker (clips, stings)
    speaker: str | None = None


def layout_placed(
    kinds: list[str],
    durs: list[float],
    placements: list[str],
    *,
    clip_edge_overlap_s: float,
    narration_crossfade_s: float,
    speakers: list[str | None] | None = None,
    overlap: TurnOverlap | None = None,
) -> list[float]:
    """Start offset (s) of each part, placement-aware. Pure function.

    Sequential parts advance a running cursor: a clip (or a part following a clip)
    starts ``clip_edge_overlap_s`` before the cursor so its faded edges tuck under
    the neighbour; narration-after-narration overlaps by ``narration_crossfade_s``.
    An ``"under"`` part starts *at* the cursor (concurrent with the next
    sequential part) and does **not** advance it — so it overlays what follows.
    Clamped ≥ 0.

    With ``speakers`` (one id per part, ``None`` = no speaker) and an ``overlap``
    that is on, each boundary :func:`interrupting_boundaries` names starts up to
    ``max_s`` early instead (an interruption), capped by both turns' lengths and
    shortened by a jitter drawn from ``Random(f"{seed}:{i}")`` — per part index,
    so editing one boundary does not reshuffle the others. Overlap is measured
    between file boundaries, lead silence included (see :class:`TurnOverlap`).

    While overlap is on, two floors keep it to two voices at once: a part laid
    after a non-clip part never starts before the end of any earlier sequential
    part but the last, and a narration part never starts before the end of any
    earlier narration but the most recent. Neither applies with overlap off, so
    the default layout is unchanged.

    >>> layout_placed(["narration"] * 3, [2.0, 0.3, 2.0], ["sequential"] * 3,
    ...     clip_edge_overlap_s=0.5, narration_crossfade_s=0.12,
    ...     speakers=["a", "b", "b"], overlap=TurnOverlap(0.4, 0.0))
    [0.0, 1.7, 2.0]
    """
    on = overlap is not None and overlap.is_on
    cuts = (
        set(interrupting_boundaries(kinds, placements, speakers, overlap))
        if on
        else set()
    )
    starts: list[float] = [0.0] * len(kinds)
    ends: list[float] = [0.0] * len(kinds)
    cursor = 0.0
    prev_seq_kind: str | None = None
    seq: list[int] = []  # indices of the sequential parts laid so far
    talk: list[int] = []  # indices of the narration parts laid so far
    for i, kind in enumerate(kinds):
        if placements[i] == "under":
            # overlay the following sequential part; cursor unchanged
            starts[i] = cursor
            ends[i] = cursor + durs[i]
            if kind == "narration":
                talk.append(i)
            continue
        floor = 0.0
        if prev_seq_kind is None:
            start = 0.0
        elif kind == "clip" or prev_seq_kind == "clip":
            start = max(0.0, cursor - clip_edge_overlap_s)
        else:
            lead = narration_crossfade_s
            if (seq[-1], i) in cuts:
                lead = max(lead, _jittered_overlap(overlap, i, durs[seq[-1]], durs[i]))
            if on:  # no third part under the two at this boundary
                floor = max((ends[j] for j in seq[:-1]), default=0.0)
            start = max(0.0, cursor - lead)
        if on and kind == "narration":  # no third voice, whatever lies between
            floor = max([floor] + [ends[j] for j in talk[:-1]])
        start = max(start, floor)
        starts[i], ends[i] = start, start + durs[i]
        cursor = ends[i]
        prev_seq_kind = kind
        seq.append(i)
        if kind == "narration":
            talk.append(i)
    return starts


def interrupting_boundaries(
    kinds: list[str],
    placements: list[str],
    speakers: list[str | None] | None,
    overlap: TurnOverlap | None,
) -> list[tuple[int, int]]:
    """The ``(prev, cur)`` sequential-part pairs where ``cur`` interrupts ``prev``.

    The one definition :func:`layout_placed` (where to start) and
    :func:`duck_regions` (what to duck) share: overlap is on, both parts are
    narration (a clip or sting never interrupts nor is interrupted), both have a
    speaker, and the speakers differ.

    >>> interrupting_boundaries(
    ...     ["narration", "narration", "clip", "narration", "narration"],
    ...     ["sequential"] * 5, ["a", "b", None, "b", "c"], TurnOverlap(0.3))
    [(0, 1), (3, 4)]
    """
    if not (overlap is not None and overlap.is_on and speakers):
        return []
    seq = [i for i, p in enumerate(placements) if p != "under"]

    def _talks(i: int) -> bool:
        return kinds[i] == "narration" and speakers[i] is not None

    return [
        (prev, cur)
        for prev, cur in zip(seq, seq[1:])
        if _talks(prev) and _talks(cur) and speakers[prev] != speakers[cur]
    ]


def _jittered_overlap(
    overlap: TurnOverlap, index: int, prev_dur: float, cur_dur: float
) -> float:
    """Part ``index``'s overlap: ``max_s`` shortened by a seeded fraction, capped
    by both turns' lengths. The draw is keyed on ``(seed, index)``, not on a
    shared stream, so it does not depend on how many boundaries came before."""
    draw = random.Random(f"{overlap.seed}:{index}").random()
    amount = overlap.max_s * (1.0 - overlap.jitter * draw)
    return min(amount, prev_dur, cur_dur)


def duck_regions(
    starts: list[float],
    durs: list[float],
    kinds: list[str],
    placements: list[str],
    speakers: list[str | None] | None,
    overlap: TurnOverlap | None,
) -> dict[int, tuple[float, float]]:
    """Per part, the ``(from_s, to_s)`` span *within its own audio* to duck.

    The tail of an outgoing voice that the next speaker talks over, at each of
    :func:`interrupting_boundaries`; empty when ``overlap.duck_db`` is 0. Pure,
    from the laid-out starts.
    """
    if not (overlap is not None and overlap.duck_db):
        return {}
    regions = {}
    for prev, cur in interrupting_boundaries(kinds, placements, speakers, overlap):
        if starts[cur] < starts[prev] + durs[prev]:
            regions[prev] = (starts[cur] - starts[prev], durs[prev])
    return regions


def layout_starts(
    kinds: list[str],
    durs: list[float],
    *,
    clip_edge_overlap_s: float,
    narration_crossfade_s: float,
) -> list[float]:
    """Start offset (s) of each part (all sequential). Thin wrapper over
    :func:`layout_placed` — kept for callers that don't use placement."""
    return layout_placed(
        kinds,
        durs,
        ["sequential"] * len(kinds),
        clip_edge_overlap_s=clip_edge_overlap_s,
        narration_crossfade_s=narration_crossfade_s,
    )


def weave_timeline(
    items: list[TimelineItem],
    out_path: str | Path,
    *,
    clip_edge_overlap_s: float = 0.5,
    narration_crossfade_s: float = 0.12,
    target_lufs: float = -16.0,
    true_peak: float = -1.0,
    sample_rate: int = 44100,
    bed=None,  # optional braidio.music.MusicBed — instrumental underscore under all
    overlap: TurnOverlap | None = None,
) -> Path:
    """Place items on a timeline and mix. Clips overlap neighbours by
    ``clip_edge_overlap_s`` (their faded edges tuck under narration); narration
    parts butt-join with a small crossfade. Returns ``out_path``.

    ``bed`` (a :class:`~braidio.music.MusicBed`) lays an instrumental underscore
    under the whole production: it's rendered to cover the timeline, attenuated,
    and mixed in posted by ``bed.lead_in_s``. Items marked ``spotlight`` open a
    gap in it (the bed is rendered as the regions around them — see
    :func:`braidio.music.bed_regions`); with none marked the bed is the single
    whole-span file. Falls back to a plain concat feel when
    ``clip_edge_overlap_s == 0`` and there's nothing to overlay.

    ``overlap`` (a :class:`TurnOverlap`, e.g. ``WeaveConfig.turn_overlap()``)
    lays a change of speaker as an interruption and, with ``duck_db``, lowers
    the outgoing voice's talked-over tail (see :func:`layout_placed`).
    """
    _require_ffmpeg()
    if not items:
        raise ValueError("weave_timeline needs at least one item")

    durs = [duration_s(it.path) for it in items]
    kinds = [it.kind for it in items]
    placements = [it.placement for it in items]
    speakers = [it.speaker for it in items]
    starts = layout_placed(
        kinds,
        durs,
        placements,
        clip_edge_overlap_s=clip_edge_overlap_s,
        narration_crossfade_s=narration_crossfade_s,
        speakers=speakers,
        overlap=overlap,
    )
    ducks = duck_regions(starts, durs, kinds, placements, speakers, overlap)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Build one amix graph: (duck →) delay each input to its start, then sum.
    inputs: list[str] = []
    for it in items:
        inputs += ["-i", it.path]
    filters: list[str] = []
    labels: list[str] = []
    for i, (start, it) in enumerate(zip(starts, items)):
        delay_ms = int(round(start * 1000))
        lbl = f"a{i}"
        # normalize every input to stereo @ sample_rate so amix keeps stereo
        # (narration is mono, song clips are stereo) — else it collapses to mono.
        chain = f"[{i}:a]aformat=sample_rates={sample_rate}:channel_layouts=stereo"
        # ducked underlay: attenuate before delaying so it sits beneath the talk.
        if it.placement == "under" and it.duck_db:
            chain += f",volume={it.duck_db}dB"
        if i in ducks:  # lower the outgoing voice while the next speaker cuts in
            lo, hi = ducks[i]
            chain += (
                f",volume={overlap.duck_db}dB:enable='between(t,{lo:.3f},{hi:.3f})'"
            )
        chain += f",adelay={delay_ms}:all=1[{lbl}]"
        filters.append(chain)
        labels.append(f"[{lbl}]")

    # Optional music bed: prepare it to cover the timeline, then mix it in posted.
    if bed is not None:
        from braidio.music import bed_regions, prepare_bed, prepare_bed_regions

        total_s = max(s + d for s, d in zip(starts, durs))
        spotlights = [
            (s, s + d) for s, d, it in zip(starts, durs, items) if it.spotlight
        ]
        if not spotlights:
            # one whole-span bed file, posted by its lead-in (the plain path)
            bed_spans = [
                (
                    prepare_bed(
                        bed,
                        total_s,
                        out.parent / f"_bed_{out.stem}.mp3",
                        sample_rate=sample_rate,
                    ),
                    bed.lead_in_s,
                )
            ]
        else:
            # fade-to-spotlight: the bed as the regions around the spotlit parts
            bed_spans = prepare_bed_regions(
                bed,
                bed_regions(bed, total_s, spotlights),
                out.parent,
                stem=f"_bed_{out.stem}",
                sample_rate=sample_rate,
            )
        for k, (bed_path, start_s) in enumerate(bed_spans):
            idx = len(items) + k
            inputs += ["-i", str(bed_path)]
            start_ms = int(round(start_s * 1000))
            filters.append(
                f"[{idx}:a]aformat=sample_rates={sample_rate}:channel_layouts=stereo,"
                f"adelay={start_ms}:all=1[bed{k}]"
            )
            labels.append(f"[bed{k}]")

    n_inputs = len(labels)
    mix = (
        "".join(labels) + f"amix=inputs={n_inputs}:normalize=0:dropout_transition=0[m]"
    )
    norm = f"[m]loudnorm=I={target_lufs}:TP={true_peak}:LRA={_LOUDNESS_RANGE}[out]"
    filtergraph = ";".join(filters + [mix, norm])

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filtergraph,
            "-map",
            "[out]",
            "-ar",
            str(sample_rate),
            "-ac",
            "2",
            "-b:a",
            "192k",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out
