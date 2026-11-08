"""Turn a rendered production into a Ken Burns film over still images.

braidio's delegation table sends *video render* to ``reelee`` -- and still does
for anything with generated footage, characters or shots. This module is the one
case reelee cannot serve: reelee imports braidio, so braidio cannot import
reelee. What it needs instead is a leaf dependency, and pan/zoom over stills is
exactly that: :mod:`burns` owns the motion, this module owns *where the cuts go*,
which is braidio-specific knowledge because only braidio knows where a beat ends.

**Optional layer.** Nothing here is imported by ``braidio/__init__`` eagerly and
no function-level dependency is imported at module scope, so ``import
braidio.video`` succeeds with nothing extra installed. :data:`HAS_VIDEO` reports
whether the render path's dependencies (``braidio[video]`` -> ``burns``,
``pillow``) are actually present; :func:`plan_spans` and :func:`assign_stills`
are pure and work regardless.

The seams, and what each defaults to:

===================== ====================================== =========================
Seam                  v1 default                             Replaced by
===================== ====================================== =========================
where stills come     caller supplies paths; braidio fetches  any image source in the
from                  nothing (same rule as ``SegmentSource``) consuming app
camera motion         ``burns.content_aware_path_for``        any ``path_for=``
                      (saliency-framed, so a push does not    callable returning a
                      drift off a face)                       ``BurnsPath``
still -> frame        blurred-fill composite, nothing         any ``prepare=``
                      cropped away                            callable
===================== ====================================== =========================

Deliberately *not* seams: sentence splitting, the credits-card layout, the
panel-length bounds. They are written directly, with constants at module top.

Why the blurred fill: a pool of found stills mixes portrait and landscape, and
cover-cropping a tall image into a wide frame throws most of it away. Compositing
onto a blurred, darkened enlargement of the image itself keeps the whole picture
and never shows dead black bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

#: Sidecar suffix recording what a prepared canvas was made from.
_STAMP_SUFFIX = ".src"

__all__ = [
    "HAS_VIDEO",
    "Footage",
    "Panel",
    "Span",
    "assign_stills",
    "credits_card",
    "missing_dependencies",
    "plan_spans",
    "prepare_still",
    "save_atomically",
    "render_video",
    "concat_argv",
    "segment_argv",
    "frame_counts",
    "runs",
]

#: Default frame. 16:9 at 1080p is what every consumer player and upload wants.
DEFAULT_SIZE = (1920, 1080)
DEFAULT_FPS = 30

#: A still holds the screen this long. Under the floor it reads as a flicker;
#: over the ceiling a static photograph starts to feel stalled even while moving.
MIN_PANEL_S = 5.0
MAX_PANEL_S = 9.0

#: Blurred-fill background: blur hard and darken, so it reads as depth rather
#: than as a second picture competing with the foreground.
_BG_BLUR_PX = 42
_BG_BRIGHTNESS = 0.45

#: Alternating camera moves, so consecutive panels never travel the same way.
_STYLES = ("push", "drift")


def missing_dependencies() -> list[str]:
    """Which ``braidio[video]`` dependencies are absent (empty when ready)."""
    missing = []
    for mod, dist in (("burns", "burns"), ("PIL", "pillow")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(dist)
    return missing


HAS_VIDEO = not missing_dependencies()


def _require() -> None:
    missing = missing_dependencies()
    if missing:
        raise ImportError(
            "braidio.video needs " + ", ".join(missing) + " — install braidio[video]"
        )


def _split_count(length: float, min_panel_s: float, max_panel_s: float) -> int:
    """How many panels a stretch of ``length`` should become.

    Ceiling division, so no panel exceeds ``max_panel_s`` -- ``round`` would let a
    20s beat become two 10s panels, over a 9s ceiling. But the ceiling yields to
    the floor: if splitting would make every part shorter than ``min_panel_s``, one
    slightly-too-long panel beats a row of flickers.

    Examples:
        >>> _split_count(20.0, 5.0, 9.0)   # 3 x 6.67, all under the ceiling
        3
        >>> _split_count(9.5, 5.0, 9.0)    # 2 x 4.75 would breach the floor
        1
        >>> _split_count(4.0, 5.0, 9.0)
        1
    """
    import math

    if length <= max_panel_s:
        return 1
    parts = math.ceil(length / max_panel_s)
    while parts > 1 and length / parts < min_panel_s:
        parts -= 1
    return parts


@dataclass(frozen=True)
class Span:
    """A stretch of screen time, before any image is chosen for it.

    ``beat_index`` / ``kind`` / ``label`` carry the beat this came from, so a
    caller assigning pictures can see what is being said over each span.
    """

    start: float
    end: float
    beat_index: int
    kind: str = ""
    label: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Footage:
    """Recorded video a panel plays as it is — a straight cut, no camera move.

    ``in_s`` is where in ``path`` the panel's span starts; the panel's own
    duration says how much of it plays. A screen recording, a clip of a talk,
    a phone video: anything ffmpeg reads.
    """

    path: str
    in_s: float = 0.0


@dataclass(frozen=True)
class Panel:
    """A :class:`Span` with a still and its camera move — or with footage.

    With ``footage`` set, the panel plays that video over its span and
    ``still``/``style``/``zoom`` are not rendered: ``still`` stays the panel's
    poster (what a storyboard or a picker shows for it).
    """

    start: float
    end: float
    still: str
    style: str = "push"
    zoom: float = 1.18
    label: str = ""
    footage: Footage | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


def plan_spans(
    timeline,
    *,
    min_panel_s: float = MIN_PANEL_S,
    max_panel_s: float = MAX_PANEL_S,
) -> list[Span]:
    """Cut ``timeline`` into contiguous spans of roughly one still each.

    Cuts land on **beat boundaries** wherever a beat is already a comfortable
    length, because that is where the narration actually changes subject. A beat
    longer than ``max_panel_s`` is divided into equal parts (so a 30-second
    passage becomes four panels rather than one stalled photograph); a beat
    shorter than ``min_panel_s`` is merged forward into the next one rather than
    producing a flicker.

    The result is gapless and ordered: ``spans[i].end == spans[i + 1].start``.

    Args:
        timeline: a :class:`~braidio.timeline.TimelineBreakdown`.
        min_panel_s: below this, a beat is merged into the following span.
        max_panel_s: above this, a beat is split into equal parts.

    Returns:
        Spans covering the whole production, in playback order.

    Examples:
        >>> from braidio.timeline import build_timeline
        >>> tl = build_timeline(kinds=["narration"], durations=[20.0])
        >>> [round(s.duration, 2) for s in plan_spans(tl)]
        [6.67, 6.67, 6.67]

        A short beat does not become its own panel:

        >>> tl = build_timeline(kinds=["narration", "narration"], durations=[2.0, 8.0])
        >>> len(plan_spans(tl))
        1
    """
    beats = sorted(timeline.beats, key=lambda b: b.start)
    if not beats:
        return []

    # The weave crossfades beats, so spans would overlap if taken verbatim. Walk a
    # single cursor instead: each span runs from wherever the last one ended.
    spans: list[Span] = []
    cursor = beats[0].start
    pending: list = []

    for i, beat in enumerate(beats):
        pending.append(beat)
        is_last = i == len(beats) - 1
        end = beats[i + 1].start if not is_last else beat.start + beat.duration
        length = end - cursor
        if length < min_panel_s and not is_last:
            continue  # too short to stand alone — carry it into the next beat

        head = pending[0]
        parts = _split_count(length, min_panel_s, max_panel_s)
        step = length / parts
        for p in range(parts):
            spans.append(
                Span(
                    start=cursor + p * step,
                    end=cursor + (p + 1) * step,
                    beat_index=head.index,
                    kind=head.kind,
                    label=head.label,
                )
            )
        cursor = end
        pending = []

    return spans


def assign_stills(
    spans: Iterable[Span],
    stills: Sequence[str],
    *,
    zoom: float = 1.18,
) -> list[Panel]:
    """Attach stills to ``spans``, cycling so none repeats back-to-back.

    This is the *mechanical* default. Which picture belongs over which sentence is
    an authoring decision — it needs to know what is being said — so a caller that
    cares builds :class:`Panel` objects directly and uses each span's ``label``
    and ``beat_index`` to choose. This function is what you want when the stills
    are interchangeable texture.

    Examples:
        >>> spans = [Span(0, 5, 0), Span(5, 10, 1), Span(10, 15, 2)]
        >>> [p.still for p in assign_stills(spans, ["a.jpg", "b.jpg"])]
        ['a.jpg', 'b.jpg', 'a.jpg']
        >>> [p.style for p in assign_stills(spans, ["a.jpg", "b.jpg"])]
        ['push', 'drift', 'push']
    """
    if not stills:
        raise ValueError("assign_stills needs at least one still")
    out = []
    for i, span in enumerate(spans):
        out.append(
            Panel(
                start=span.start,
                end=span.end,
                still=str(stills[i % len(stills)]),
                style=_STYLES[i % len(_STYLES)],
                zoom=zoom,
                label=span.label,
            )
        )
    return out


def _still_stamp(src, size: tuple[int, int]) -> str:
    """What a prepared canvas was made from: the source's bytes and the frame size."""
    import hashlib

    digest = hashlib.sha256(Path(src).read_bytes()).hexdigest()
    return f"{digest} {size[0]}x{size[1]}"


def _stamp_path(dst: Path) -> Path:
    return dst.with_name(dst.name + _STAMP_SUFFIX)


def prepare_still(
    src, dst, *, size: tuple[int, int] = DEFAULT_SIZE, refresh: bool = False
) -> Path:
    """Composite ``src`` onto a blurred fill of itself at exactly ``size``.

    Nothing is cropped away and no dead black bar appears, whatever the source
    aspect. Idempotent *for the same input*: ``dst`` is reused only when a
    sidecar (``<dst>.src``) records that it was made from these source bytes at
    this ``size``. A different still, or a different size, into the same ``dst``
    is rebuilt; so is a ``dst`` with no sidecar (its origin is unknown).
    ``refresh=True`` rebuilds unconditionally.

    Examples:
        >>> import tempfile
        >>> from pathlib import Path
        >>> from PIL import Image
        >>> d = Path(tempfile.mkdtemp())
        >>> Image.new("RGB", (40, 20), "red").save(d / "a.png")
        >>> Image.new("RGB", (40, 20), "blue").save(d / "b.png")
        >>> out = prepare_still(d / "a.png", d / "o.jpg", size=(64, 36))
        >>> before = out.read_bytes()
        >>> prepare_still(d / "a.png", d / "o.jpg", size=(64, 36)).read_bytes() == before
        True
        >>> prepare_still(d / "b.png", d / "o.jpg", size=(64, 36)).read_bytes() == before
        False
    """
    _require()
    from PIL import Image, ImageEnhance, ImageFilter

    dst = Path(dst)
    stamp = _still_stamp(src, size)
    stamp_file = _stamp_path(dst)
    if (
        not refresh
        and dst.exists()
        and stamp_file.exists()
        and stamp_file.read_text() == stamp
    ):
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)

    width, height = size
    img = Image.open(src).convert("RGB")

    cover = max(width / img.width, height / img.height)
    bg = img.resize(
        (round(img.width * cover), round(img.height * cover)), Image.LANCZOS
    )
    left, top = (bg.width - width) // 2, (bg.height - height) // 2
    bg = bg.crop((left, top, left + width, top + height))
    bg = bg.filter(ImageFilter.GaussianBlur(_BG_BLUR_PX))
    bg = ImageEnhance.Brightness(bg).enhance(_BG_BRIGHTNESS)

    contain = min(width / img.width, height / img.height)
    fg = img.resize(
        (round(img.width * contain), round(img.height * contain)), Image.LANCZOS
    )
    bg.paste(fg, ((width - fg.width) // 2, (height - fg.height) // 2))
    save_atomically(bg, dst, quality=94)
    stamp_file.write_text(stamp)
    return dst


def save_atomically(img, dst, **encode) -> Path:
    """``img.save(dst)`` through a sibling temp file and an atomic rename.

    A cached canvas is shared: the studio's move preview and a render can both
    ask for it at once, and a reader that opens a half-written JPEG would
    otherwise cache the truncation under its content key for good.
    """
    import os
    import uuid

    dst = Path(dst)
    tmp = dst.with_name(f".{dst.stem}.{uuid.uuid4().hex[:8]}.tmp{dst.suffix}")
    try:
        img.save(tmp, **encode)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)
    return dst


def credits_card(
    lines: Sequence[str],
    dst,
    *,
    heading: str = "Credits",
    footer: str = "",
    size: tuple[int, int] = DEFAULT_SIZE,
) -> Path:
    """Render an end card listing ``lines``, **fitted** so none is lost.

    Reusing a photograph under CC BY / CC BY-SA is conditional on crediting it, so
    for a film built from found images this card is part of the licence
    compliance, not decoration. Generate ``lines`` from whatever manifest recorded
    the fetches, so the card cannot drift from what was actually used.

    Because it is compliance, it must not *silently* drop an attribution — and a
    fixed 31px leading did exactly that past roughly thirty lines: the tail ran
    off the bottom of the frame and the footer drew over whatever was left. A
    forty-three-image film credited twenty-eight of them and looked fine.

    So the type is fitted to the content instead. Leading shrinks first, then the
    list goes to two columns, and only if it still will not fit does this raise —
    which is the right failure, because a caller who cannot see the problem cannot
    fix it. Pass a bigger ``size`` or split across two cards.
    """
    _require()
    from PIL import Image, ImageDraw, ImageFont

    def font(px: int):
        for candidate in (
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ):
            if Path(candidate).exists():
                return ImageFont.truetype(candidate, px)
        return ImageFont.load_default()

    width, height = size
    card = Image.new("RGB", (width, height), (12, 12, 14))
    draw = ImageDraw.Draw(card)
    draw.text((100, 62), heading, fill=(238, 238, 243), font=font(34))

    lines = list(lines)
    top, bottom = 140, height - (96 if footer else 40)
    available = bottom - top
    gutter = 40

    # Fit on BOTH axes, measuring the real text: a credit line is long ("<title>
    # — <artist> — <licence>"), so a layout that has the rows for it can still be
    # too narrow for it. Larger type first, and one column before two, since a
    # single column reads in the order the film used the pictures.
    layout = None
    for columns in (1, 2):
        column_width = (width - 200 - (columns - 1) * gutter) // columns
        for px in (21, 19, 17, 15, 13, 12):
            leading = round(px * 1.45)
            per_column = int(available // leading)
            if per_column * columns < len(lines):
                continue
            widest = max(
                (draw.textlength(line, font=font(px)) for line in lines), default=0
            )
            if widest > column_width:
                continue
            layout = (columns, px, leading, per_column, column_width)
            break
        if layout:
            break

    if layout is None:
        raise ValueError(
            f"credits_card: {len(lines)} lines will not fit {width}x{height} "
            f"without dropping some. Shorten the lines, pass a larger size=, or "
            f"split the roll across two cards — silently truncating an "
            f"attribution would break the licence this card exists to satisfy."
        )

    columns, px, leading, per_column, column_width = layout
    for index, line in enumerate(lines):
        column, row = divmod(index, per_column)
        draw.text(
            (100 + column * (column_width + gutter), top + row * leading),
            line,
            fill=(186, 186, 198),
            font=font(px),
        )

    if footer:
        draw.text((100, height - 78), footer, fill=(120, 120, 132), font=font(21))
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    card.save(dst, quality=94)
    return dst


def render_video(
    panels: Sequence[Panel],
    *,
    audio_path,
    out_path,
    size: tuple[int, int] = DEFAULT_SIZE,
    fps: int = DEFAULT_FPS,
    workdir=None,
    prepare: Callable[..., Path] | None = None,
    path_for: Callable[..., object] | None = None,
    runner: Callable[..., object] | None = None,
    footage_duration: Callable[[str], float] | None = None,
    **write_kwargs,
) -> Path:
    """Render ``panels`` as one film and mux ``audio_path`` under it.

    Stills get a Ken Burns move; panels with :class:`Footage` play their video
    as a straight cut. A film of stills only is one ``burns.ken_burns_film``
    pass rather than per-panel renders plus a concat: that avoids a re-encode
    seam at every cut and a frozen frame at every panel tail. A film with
    footage renders each run of consecutive stills that way (silent), cuts each
    run and each footage panel into a piece of exactly its frames, one at a time
    so memory stays flat (:func:`segment_argv`), and joins the pieces under the
    audio in one final encode (:func:`concat_argv`).

    Args:
        panels: the stills (or footage) and their screen time, in order.
        audio_path: the finished mix. Its length should match the panels; pad it
            first if the film ends on a credits card.
        out_path: mp4 to write.
        size, fps: frame geometry and rate.
        workdir: where prepared canvases are cached (default: next to ``out_path``).
        prepare: still -> frame-sized image. Default :func:`prepare_still`.
        path_for: ``(image_path, index, panel) -> BurnsPath``. Default is
            ``burns.content_aware_path_for``, which frames on the image's salient
            region so a slow push stays on the subject.
        runner: runs an ffmpeg argv (default :func:`subprocess.run` with
            ``check=True``); only the footage path shells out.
        footage_duration: a footage file's length in seconds (default: ffprobe);
            an in-point at or past it is refused before anything renders.
        **write_kwargs: forwarded to ``burns.ken_burns_film`` (the still runs;
            the footage path encodes its own pieces).

    Returns:
        The written mp4 path.
    """
    if not panels:
        raise ValueError("render_video needs at least one panel")

    out_path = Path(out_path)
    workdir = Path(workdir) if workdir else out_path.parent / "_frames"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def still_film(indices, saveas, audio):
        _require()
        from burns import content_aware_path_for, ken_burns_film

        prep = prepare or prepare_still

        def default_path_for(image, index, panel):
            return content_aware_path_for(
                str(image),
                index=index,
                output_aspect=size[0] / size[1],
                zoom=panel.zoom,
                mode="in" if panel.style == "push" else "auto",
            )

        path_of = path_for or default_path_for
        triples = []
        for i in indices:
            panel = panels[i]
            canvas = prep(
                panel.still,
                workdir / f"{i:03d}_{Path(panel.still).stem}.jpg",
                size=size,
            )
            triples.append((str(canvas), path_of(canvas, i, panel), panel.duration))
        return ken_burns_film(
            triples,
            saveas=str(saveas),
            fps=fps,
            audio_path=None if audio is None else str(audio),
            **write_kwargs,
        )

    if all(p.footage is None for p in panels):
        return still_film(range(len(panels)), out_path, audio_path)

    import subprocess
    import tempfile

    run = runner or (lambda argv: subprocess.run(argv, check=True))
    probe = footage_duration or _probe_duration
    _check_in_points(panels, probe)
    workdir.mkdir(parents=True, exist_ok=True)
    frames = frame_counts(panels, fps=fps)
    # Private to this render: two renders sharing a project's workdir never
    # pick up each other's pieces, and nothing is left behind.
    with tempfile.TemporaryDirectory(prefix="_cut_", dir=workdir) as tmp:
        tmp = Path(tmp)
        pieces = []
        for n, (kind, indices) in enumerate(runs(panels)):
            if kind == "footage":
                footage = panels[indices[0]].footage
                source, in_s = footage.path, footage.in_s
            else:
                source, in_s = tmp / f"stills_{n:04d}.mp4", 0.0
                still_film(indices, source, None)
            piece = tmp / f"piece_{n:04d}.mp4"
            # one piece at a time: memory stays flat however many cuts there are
            run(
                segment_argv(
                    source,
                    in_s,
                    sum(frames[i] for i in indices),
                    out_path=piece,
                    size=size,
                    fps=fps,
                )
            )
            pieces.append(piece)
        listing = tmp / "pieces.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in pieces))
        run(
            concat_argv(
                listing,
                audio_path=audio_path,
                out_path=out_path,
                total_frames=sum(frames),
                fps=fps,
            )
        )
    return out_path


def _probe_duration(path) -> float:
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return float(out.strip())


def _check_in_points(panels: Sequence[Panel], probe: Callable[[str], float]) -> None:
    """Refuse an in-point at or past the end of its footage.

    ffmpeg seeked past the end yields no frames at all, so nothing is held and
    the cut comes out short, sliding every later panel off its narration.
    (Footage that merely runs out *during* a panel is fine: its last frame is
    held.)
    """
    lengths: dict[str, float] = {}
    for i, panel in enumerate(panels):
        if panel.footage is None:
            continue
        path = panel.footage.path
        if path not in lengths:
            lengths[path] = probe(path)
        if panel.footage.in_s >= lengths[path]:
            raise ValueError(
                f"panel {i} ({panel.start:.2f}-{panel.end:.2f}s) starts its footage at "
                f"{panel.footage.in_s:.2f}s, but {path} is only {lengths[path]:.2f}s long"
            )


def runs(panels: Sequence[Panel]) -> list[tuple[str, list[int]]]:
    """Group consecutive panels into ``("footage" | "stills", indices)`` runs.

    Each footage panel is its own run (it has its own in-point); consecutive
    stills share one, rendered as a single Ken Burns pass.

    >>> P = lambda f=None: Panel(0, 1, "a.jpg", footage=f)
    >>> runs([P(), P(), P(Footage("v.mp4")), P(Footage("v.mp4", 3)), P()])
    [('stills', [0, 1]), ('footage', [2]), ('footage', [3]), ('stills', [4])]
    """
    out: list[tuple[str, list[int]]] = []
    for i, panel in enumerate(panels):
        if panel.footage is not None:
            out.append(("footage", [i]))
        elif out and out[-1][0] == "stills":
            out[-1][1].append(i)
        else:
            out.append(("stills", [i]))
    return out


def frame_counts(panels: Sequence[Panel], *, fps: int) -> list[int]:
    """Frames per panel, rounded on the ABSOLUTE timeline so they never drift.

    Rounding each duration alone would let a film of many short panels slide
    off its narration by a frame per cut; rounding each boundary keeps every
    cut within half a frame of where the audio says it is.

    >>> ps = [Panel(0, 1.01, "a"), Panel(1.01, 2.02, "b"), Panel(2.02, 3.03, "c")]
    >>> frame_counts(ps, fps=30)
    [30, 31, 30]
    """
    bounds = [round(panels[0].start * fps)] + [round(p.end * fps) for p in panels]
    return [b - a for a, b in zip(bounds, bounds[1:])]


def segment_argv(
    source,
    in_s: float,
    frames: int,
    *,
    out_path,
    size: tuple[int, int] = DEFAULT_SIZE,
    fps: int = DEFAULT_FPS,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """One cut: exactly ``frames`` frames of ``source`` from ``in_s``, as a piece.

    Seeked at the input (fast, and exact in modern ffmpeg), resampled to
    ``fps``, fitted inside ``size`` without cropping (letterboxed on black),
    and — should the source run out early — held on its last frame rather than
    cutting short, so the picture never slides off the narration. Pieces are
    near-lossless (they are re-encoded once more, by :func:`concat_argv`).

    >>> argv = segment_argv("rec.mp4", 2.5, 60, out_path="p.mp4", size=(1080, 1920))
    >>> argv[argv.index("-ss") + 1], argv[argv.index("-t") + 1]
    ('2.500', '3.000')
    >>> "trim=end_frame=60" in argv[argv.index("-vf") + 1]
    True
    """
    if frames <= 0:
        raise ValueError(f"a cut of {source} @ {in_s}s must have frames, got {frames}")
    w, h = size
    chain = (
        f"setpts=PTS-STARTPTS,fps={fps},"
        # out_range=tv: footage from JPEG frames (a screencast) is full range;
        # the delivered film is TV range, which every player shows correctly
        f"scale={w}:{h}:force_original_aspect_ratio=decrease:out_range=tv,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,"
        f"tpad=stop_mode=clone:stop=-1,trim=end_frame={frames},setpts=PTS-STARTPTS"
    )
    # a second of slack: the tail is held by tpad and cut by trim anyway
    return [
        ffmpeg, "-v", "error", "-y",
        "-ss", f"{in_s:.3f}", "-t", f"{frames / fps + 1.0:.3f}", "-i", str(source),
        "-vf", chain, "-an", "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "10", str(out_path),
    ]  # fmt: skip


def concat_argv(
    listing,
    *,
    audio_path,
    out_path,
    total_frames: int,
    fps: int = DEFAULT_FPS,
    ffmpeg: str = "ffmpeg",
    crf: int = 18,
) -> list[str]:
    """Join the pieces a concat ``listing`` names, under ``audio_path``, as the delivered mp4.

    >>> argv = concat_argv("l.txt", audio_path="a.wav", out_path="o.mp4", total_frames=90)
    >>> argv[argv.index("-t") + 1], argv[argv.index("-f") + 1]
    ('3.000', 'concat')
    """
    return [
        ffmpeg, "-v", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(audio_path),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-r", str(fps), "-c:a", "aac", "-b:a", "192k",
        "-t", f"{total_frames / fps:.3f}", "-movflags", "+faststart", str(out_path),
    ]  # fmt: skip


def with_credits(
    panels: Sequence[Panel], card: str | Path, *, duration_s: float
) -> list[Panel]:
    """Append a credits card to ``panels``, held for ``duration_s``.

    The audio must be padded to match — see the skill for the one-line ``ffmpeg``
    ``apad`` that does it.
    """
    last_end = panels[-1].end if panels else 0.0
    return [
        *panels,
        Panel(last_end, last_end + duration_s, str(card), style="drift", zoom=1.04),
    ]
