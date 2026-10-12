"""``video_cut.render`` and ``video_cut.finish`` — panels → the delivered mp4.

Two **local-CPU** Transforms, not one, and that is deliberate (plan §5): the
motion pass is minutes of frame generation, the text pass is seconds, and
editing a label must re-run only the cheap one. The split also enforces
tituli's correctness rule — text composites onto the *finished motion* and is
never burned into a still, because text in a still pans and zooms with the
picture.

- ``video_cut.render`` — the panel track + the stills it points at + the
  episode audio → a ``video-cut/v1`` at ``stage="motion"``: one
  ``burns.ken_burns_film`` pass over the prepared canvases, muxed under the
  episode audio. Its ``cache_key`` covers everything that reaches a pixel —
  the audio, the frame geometry, which resolver framed the moves, and per
  panel the still's *bytes*, its crop, its span and its move — and **nothing
  editorial**: a still's ``subject``, ``labelled`` or rights are not in it.
- ``video_cut.finish`` — a motion cut + the label tracks + the stills'
  editorial decisions → a ``video-cut/v1`` at ``stage="delivered"``: tituli
  labels and cards composited in one ffmpeg pass, an SRT sidecar from the
  beats' own text (never ASR), optionally burnt in, and a credits roll
  appended when asked for. Its ``cache_key`` covers the motion cut's bytes and
  everything the text pass reads.

Both follow the ``segment_extraction.ffmpeg`` pattern: a zero-call
``falaw.Plan`` plus a skeleton carrying its own ``cache_key`` through
``nw.transforms.cache_key``, ``fresh_equivalent`` for the idempotent re-run,
``cached_output`` for the compare-and-skip — with one refinement the audio
tiers do not need. A still's editorial fields live on the same node as its
bytes, so a caption typo stales the motion cut through nw's transitive
verdict even though its key (and its frames) are unchanged. :func:`_reuse`
answers that case by **re-verifying the existing cut in place** — re-recording
its trace under its own id — rather than writing a duplicate node per typo;
the cut list does not grow, and the verdict clears. A hit whose file has
been deleted is never adopted: the product is the file.

**The camera move is resolved at render time, against the image**, by
``burns.resolve_move`` — a panel stores an *intent* (``move``, ``zoom``,
``focus``, ``seed``), not a path pinned to one image's pixels, so swapping the
still re-frames the move. A stored ``path`` (``BurnsPath.to_dict()``) is the
explicit override for a hand-corrected move. A stored path authored for a
different delivery aspect is **converted, then refit** (:func:`path_on_canvas`):
its rectangles are relative to the *prepared canvas* of that delivery, and
``prepare_still`` letterboxes the still differently at another aspect, so burns'
canvas-relative ``on_aspect_mismatch="refit"`` alone would frame a different
spot on the still (measured: a 3x landscape push re-cut vertical ended ~60%
blurred fill). Each keyframe is first mapped old canvas -> still -> new canvas
(the :func:`focus_on_canvas` treatment, for paths) and only then handed to
burns' refit. The conversion needs the still's size, so a cross-aspect path
over a still that records none is still **refused at plan time**
(:func:`_check_stored_paths`). burns shipped the resolver in
0.0.15 (thorwhalen/burns#20), which is braidio's declared minimum, so
:func:`resolve_move` is a thin pass-through rather than a shim with a second
implementation to drift from burns'. *Which behavioural version of burns
resolved it* is still part of the motion cache key
(:func:`resolver_identity`, keyed on ``burns.RESOLVER_IMPL_VERSION``): a
framing-affecting change to burns' resolver must still re-stale every motion
cut instead of being served under a framing the panel body no longer
describes.

Concurrency: ``execute`` is synchronous and carries no in-flight receipt.
Two enqueued renders of one plan both render; the job layer that enqueues
them (reelee's, plan §9) owns that idempotency, as it does for every
Transform.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import uuid
from pathlib import Path

from falaw import Plan
from lacing import Annotation
from nw import BaseTransform, TransformInputs, TransformResult, register_transform
from nw.transforms._provenance import derive_provenance

from braidio.bodies._render_nodes import EPISODE_RENDER_V1
from braidio.bodies._video import (
    LABEL_TRACK_V1,
    STILL_V1,
    VIDEO_CUT_V1,
    VIDEO_PANEL_V1,
    VideoCutBodyV1,
    credit_line,
)
from braidio.rights import Profile
from braidio.transforms._common import (
    TIER_EPISODE_RENDER,
    TIER_LABEL_TRACK,
    TIER_STILL,
    TIER_VIDEO_CUT,
    TIER_VIDEO_PANEL,
    adopt_output,
    cached_output,
    file_url,
    fresh_equivalent,
    graph_index,
    media_artifact,
    media_path,
    node_ref,
    planned_value,
    resolve_parents,
    require_tier,
    safe_duration,
    url_to_path,
)
from braidio.video import DEFAULT_FPS, DEFAULT_SIZE

RENDER_NAME = "video_cut.render"
FINISH_NAME = "video_cut.finish"

#: Where a cut's files land inside the project.
_CUTS_DIR = ("data", "cuts")
#: Default hold of the credits roll, when one is asked for (``credits_s``).
DEFAULT_CREDITS_S = 8.0
#: Default caption wrap (a player's two lines).
DEFAULT_CAPTION_CHARS = 42
#: Default delivery for tituli's reserved zones (the subtitle band + controls).
DEFAULT_DELIVERY = "youtube"
#: The tituli slot each label-track kind lands in when the body names none.
_KIND_SLOTS = {
    "title": "center",
    "context": "top-left",
    "note": "top-left",
    "tag": "bottom-left",
}
#: The whole still, as a normalized box — mapped onto the canvas it is the
#: ``content_box`` burns confines saliency to.
_WHOLE_STILL = (0.0, 0.0, 1.0, 1.0)
#: Below this normalized short side (a ~100:1 panorama strip) the still's box is
#: too thin for burns' saliency after its downscale; it is then not passed.
_MIN_CONTENT_SIDE = 0.02
#: Weight of a per-still label — lighter than any card (whose default is 2).
_LABEL_WEIGHT = 1
#: The slot a per-still label lands in (tituli's ``schedule_labels`` default).
_LABEL_SLOT = "top-left"
#: Chars of a content digest used in a canvas / crop file name.
_NAME_DIGEST_CHARS = 16
#: JPEG quality a cropped still is re-encoded at (PIL's default 75 would put a
#: lossy generation under prepare_still's q94 canvas).
_CROP_JPEG_QUALITY = 96
#: Bump when prepare_still's output changes for the same bytes and size
#: (blur, darkening, resampling): canvases are cached by name in a shared
#: workdir, and the motion key alone cannot see a canvas algorithm change.
_CANVAS_VERSION = "1"
#: Staging area for a finish's intermediates — a SUBdirectory, so a crash
#: between stages leaves nothing the delivery lister (which walks only the
#: files directly in data/cuts) would present as a cut.
_STAGING_DIR = "_staging"


# --- the move ---------------------------------------------------------------


#: burns' resolver version that takes ``content_box`` and honours the zoom on a
#: detailed photograph (thorwhalen/burns#21).
_MIN_RESOLVER_IMPL = 2
#: braidio's own share of the framing: saliency confined to the still's box on
#: its prepared canvas. Part of the motion key, since it moves pixels.
_CANVAS_FRAMING = "content_box@1"
#: Bump when :func:`path_on_canvas` frames a cross-aspect stored path
#: differently. Part of the key of only those cuts that convert one.
_PATH_REFIT_VERSION = "1"


def resolver_identity() -> str:
    """Which code frames a named move — part of the motion cache key.

    Keyed on burns' own :data:`burns.RESOLVER_IMPL_VERSION` — the
    behavioural identity burns bumps on any change that moves a rendered
    pixel. burns 0.0.15 introduced both it and ``resolve_move`` in the same
    release, so there is no burns that has one and not the other: a burns
    without them is refused here, at PLAN time, rather than planning fine
    and raising ``AttributeError`` from ``execute`` after every canvas has
    been prepared. (``mixing``, a core dependency, pulls ``burns`` with no
    floor, so an older burns is reachable without the ``video`` extra.)
    """
    import burns

    version = getattr(burns, "RESOLVER_IMPL_VERSION", None)
    if (
        version is None
        or (isinstance(version, int) and version < _MIN_RESOLVER_IMPL)
        or not hasattr(burns, "resolve_move")
    ):
        raise RuntimeError(
            f"{RENDER_NAME}: the installed burns "
            f"({getattr(burns, '__version__', 'unknown version')}) has no "
            f"resolve_move / RESOLVER_IMPL_VERSION>={_MIN_RESOLVER_IMPL}; "
            "braidio's video cuts need burns>=0.0.16 (pip install 'braidio[video]')."
        )
    return f"burns.moves@{version}/{_CANVAS_FRAMING}"


def resolve_move(
    move,
    *,
    image,
    aspect: float,
    zoom=1.18,
    focus=None,
    seed=0,
    on_aspect_mismatch: str = "raise",
    content_box=None,
):
    """A ``BurnsPath`` for an authored ``move`` over ``image``.

    Thin pass-through to ``burns.resolve_move(move, *, image, aspect, zoom,
    focus, seed, on_aspect_mismatch) -> BurnsPath``, where ``move`` is a name
    from ``MOVES`` **or** an explicit ``BurnsPath`` / its ``to_dict()``
    payload (the two front doors on one path), ``focus`` is a normalized
    ``(x, y, w, h)`` tuple overriding the saliency frame, and ``seed`` chooses
    what ``"auto"`` becomes and nothing else.

    ``on_aspect_mismatch`` keeps burns' own default, ``"raise"``. burns'
    ``"refit"`` preserves each rectangle's centre and zoom relative to the
    *canvas*; braidio's canvases letterbox the still differently per aspect,
    so a refit path lands on a different part of the still (see the module
    docstring). Pass ``"refit"`` only for a path whose canvas IS the still, or
    one already converted by :func:`path_on_canvas`.

    ``content_box`` is the still's box on the canvas: burns runs saliency only
    inside it, so the blurred fill around a letterboxed still (and the hard
    seam between them) is never read as subject (thorwhalen/burns#21).
    """
    import burns

    return burns.resolve_move(
        move,
        image=image,
        aspect=aspect,
        zoom=zoom,
        focus=focus,
        seed=seed,
        on_aspect_mismatch=on_aspect_mismatch,
        content_box=content_box,
    )


def focus_on_canvas(focus, *, image_size, canvas_size):
    """A focus rect authored on the image, in the prepared canvas's coordinates.

    :func:`braidio.video.prepare_still` letterboxes the still onto a blurred
    fill at frame size, so a normalized rect on the image is not the same
    rect on the canvas whenever the aspects differ. The move is resolved on
    the canvas (that is what burns samples), so the focus travels with it.

    >>> focus_on_canvas((0.0, 0.0, 1.0, 1.0), image_size=(100, 100), canvas_size=(200, 100))
    (0.25, 0.0, 0.5, 1.0)
    """
    iw, ih = image_size
    cw, ch = canvas_size
    contain = min(cw / iw, ch / ih)
    fw, fh = iw * contain, ih * contain
    ox, oy = (cw - fw) / 2.0, (ch - fh) / 2.0
    x, y, w, h = (float(v) for v in focus)
    return ((ox + x * fw) / cw, (oy + y * fh) / ch, w * fw / cw, h * fh / ch)


def focus_on_still(rect, *, image_size, canvas_size):
    """The inverse of :func:`focus_on_canvas`: a canvas rect, in still coordinates.

    Parts of ``rect`` over the blurred fill land outside ``[0, 1]``.

    >>> focus_on_still((0.25, 0.0, 0.5, 1.0), image_size=(100, 100), canvas_size=(200, 100))
    (0.0, 0.0, 1.0, 1.0)
    """
    bx, by, bw, bh = focus_on_canvas(
        _WHOLE_STILL, image_size=image_size, canvas_size=canvas_size
    )
    x, y, w, h = (float(v) for v in rect)
    return ((x - bx) / bw, (y - by) / bh, w / bw, h / bh)


def _on_picture(rect):
    """``rect`` (still coordinates) cut down to the still itself, or ``rect``
    unchanged when it does not overlap the still at all."""
    x, y, w, h = rect
    x0, y0 = max(x, 0.0), max(y, 0.0)
    x1, y1 = min(x + w, 1.0), min(y + h, 1.0)
    if x1 <= x0 or y1 <= y0:
        return rect
    return (x0, y0, x1 - x0, y1 - y0)


#: Slack, in still fractions, for "this window showed no fill": a path
#: authored on a 1366x768 canvas rounds its edges by about a pixel.
_ON_PICTURE_TOL = 1e-3


def _slide_into(center: float, side: float, lo: float, hi: float) -> float:
    """``center`` of a window of ``side`` slid inside ``[lo, hi]`` if it fits
    there, else centred on that span (the still shows whole on that axis)."""
    if side >= hi - lo:
        return (lo + hi) / 2.0
    return min(max(center, lo + side / 2.0), hi - side / 2.0)


def path_on_canvas(stored: dict, *, image_size, canvas_size) -> dict:
    """A stored path authored at its ``output_aspect``, re-aimed at ``canvas_size``.

    ``stored`` is a ``BurnsPath.to_dict()`` whose rectangles are fractions of
    the canvas :func:`braidio.video.prepare_still` made for the delivery it was
    authored at (that canvas's shape is ``output_aspect``; its pixel size does
    not matter to fractions). Each keyframe goes old canvas -> still, keeps
    only the part that shows the still (the fill around it was never the
    subject), and becomes, on the new canvas, the smallest window of the new
    delivery's shape that contains that part, centred where the author looked
    and slid to stay on the picture wherever it fits there.

    A new shape must either add to the author's view or trim it. The rule is
    **fill only where the author showed fill**: a keyframe that was all
    picture is capped to the picture (trimming the author's view on its long
    axis rather than adding blurred bands to a push); one that already showed
    fill — a whole-canvas start, say — keeps all of what it showed. Either
    way the camera looks at the same part of the still, rather than where
    burns' canvas-relative refit alone would land.

    The result keeps the authored ``output_aspect``: burns' ``"refit"`` then
    rebuilds each window at the new aspect from the centre and zoom chosen
    here (:func:`path_for_panel` passes it), so burns still owns the window.

    A 3x push on the middle of a square still, authored landscape, re-cut
    vertical. The author's window showed the middle 59% of the still across
    and its middle third down, all of it picture; 59% across at 9:16 would
    be taller than the still, so the window is capped to the still's height
    and shows its middle 56% across, top to bottom:

    >>> landscape = {"keyframes": [{"t": 0.0, "rect": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}},
    ...     {"t": 1.0, "rect": {"x": 1 / 3, "y": 1 / 3, "w": 1 / 3, "h": 1 / 3}}],
    ...     "output_aspect": 16 / 9}
    >>> out = path_on_canvas(landscape, image_size=(100, 100), canvas_size=(90, 160))
    >>> [round(v, 4) for v in out["keyframes"][1]["rect"].values()]
    [0.2188, 0.2188, 0.5625, 0.5625]
    """
    old_canvas = (float(stored["output_aspect"]), 1.0)
    cw, ch = canvas_size
    bx, by, bw, bh = focus_on_canvas(
        _WHOLE_STILL, image_size=image_size, canvas_size=canvas_size
    )

    def convert(rect: dict) -> dict:
        on_still = focus_on_still(
            (rect["x"], rect["y"], rect["w"], rect["h"]),
            image_size=image_size,
            canvas_size=old_canvas,
        )
        on_picture = _on_picture(on_still)
        x, y, w, h = focus_on_canvas(
            on_picture, image_size=image_size, canvas_size=canvas_size
        )
        # a window of the canvas's own shape is square in canvas fractions
        side = min(max(w, h), 1.0)
        showed_fill = any(
            abs(a - b) > _ON_PICTURE_TOL for a, b in zip(on_still, on_picture)
        )
        if not showed_fill:
            side = min(side, bw, bh)
        cx = _slide_into(x + w / 2.0, side, bx, bx + bw)
        cy = _slide_into(y + h / 2.0, side, by, by + bh)
        return {"x": cx - side / 2.0, "y": cy - side / 2.0, "w": side, "h": side}

    keyframes = [{**kf, "rect": convert(kf["rect"])} for kf in stored["keyframes"]]
    return {**stored, "keyframes": keyframes}


def _crosses_aspect(stored, aspect: float) -> bool:
    """Whether a stored path names an ``output_aspect`` other than ``aspect``
    (as strictly as burns' own check, see :data:`_ASPECT_REL_TOL`)."""
    authored = (stored or {}).get("output_aspect")
    return authored is not None and not math.isclose(
        float(authored), aspect, rel_tol=_ASPECT_REL_TOL
    )


def path_for_panel(panel_body: dict, *, image, aspect: float, image_size=None):
    """The ``BurnsPath`` a panel body asks for over ``image``.

    One code path, two front doors: a stored ``path`` (a hand-corrected
    move) is handed to :func:`resolve_move` as the move itself — one
    authored for another delivery aspect is first re-aimed by
    :func:`path_on_canvas` and refit, which needs ``image_size`` (without it,
    burns refuses the mismatch); otherwise the named ``move`` resolves against the image with
    the panel's ``zoom`` / ``focus`` / ``seed``. A stored path that names no
    ``output_aspect`` takes the cut's, so a path authored before the delivery
    size was chosen still fills the frame. ``image_size`` is the size of the
    *still* shown, when ``image`` is a prepared canvas of a different aspect:
    it maps the focus onto the canvas (see :func:`focus_on_canvas`) and tells
    burns which part of the canvas is picture, so saliency never reads the
    blurred fill. Pass it whenever ``image`` is a canvas — a caller previewing
    a move (the studio) gets the same framing as the render only if it does.
    """
    canvas_size = None
    if image_size is not None:
        from PIL import Image

        with Image.open(image) as img:
            canvas_size = img.size
    on_mismatch = "raise"
    stored = panel_body.get("path")
    if stored:
        move = dict(stored)
        if move.get("output_aspect") is None:
            move["output_aspect"] = aspect
        elif canvas_size is not None and _crosses_aspect(move, aspect):
            move = path_on_canvas(move, image_size=image_size, canvas_size=canvas_size)
            on_mismatch = "refit"
    else:
        move = str(panel_body.get("move", "auto"))
    focus = panel_body.get("focus")
    if focus is not None:
        focus = (focus["x"], focus["y"], focus["w"], focus["h"])
    content_box = None
    if canvas_size is not None:
        content_box = focus_on_canvas(
            _WHOLE_STILL, image_size=image_size, canvas_size=canvas_size
        )
        if min(content_box[2], content_box[3]) < _MIN_CONTENT_SIDE:
            # a strip too thin for saliency to read: frame on the canvas, as
            # before content_box existed, rather than refuse the render
            content_box = None
        if focus is not None:
            focus = focus_on_canvas(
                focus, image_size=image_size, canvas_size=canvas_size
            )
    return resolve_move(
        move,
        image=image,
        aspect=aspect,
        zoom=float(panel_body.get("zoom", 1.18)),
        focus=focus,
        seed=int(panel_body.get("seed", 0)),
        on_aspect_mismatch=on_mismatch,
        content_box=content_box,
    )


# --- shared resolution ------------------------------------------------------


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _interval_s(ann: Annotation) -> tuple[float, float]:
    iv = ann.reference.interval
    return iv.start.to_seconds(), iv.end.to_seconds()


def _ordered_panels(panels) -> list[Annotation]:
    return sorted(panels, key=lambda p: int(p.body.get("order", 0)))


def _panels_from_ids(ids, index: dict) -> list[Annotation]:
    out = []
    for sid in ids:
        panel = index.get(uuid.UUID(str(sid)))
        if panel is None or panel.tier != TIER_VIDEO_PANEL:
            raise ValueError(f"video cut: panel {sid} is not in the graph")
        out.append(panel)
    return _ordered_panels(out)


def _still_of(panel: Annotation, index: dict) -> Annotation:
    still = index.get(uuid.UUID(str(panel.body["still_id"])))
    if still is None or still.tier != TIER_STILL:
        raise ValueError(
            f"video cut: panel {panel.id} points at still {panel.body['still_id']}, "
            "which is not a still in the graph"
        )
    return still


def _episode_of(panels, index: dict) -> Annotation:
    episodes = {
        p.id: p
        for panel in panels
        for p in resolve_parents(panel, index)
        if p.tier == TIER_EPISODE_RENDER
    }
    if len(episodes) != 1:
        raise ValueError(
            f"video cut: the panels derive from {len(episodes)} episodes; a cut is "
            "made against exactly one"
        )
    return next(iter(episodes.values()))


def _local_path(project, ann: Annotation, what: str) -> Path:
    """``ann``'s media on this machine: by content, then by recorded url."""
    return media_path(project.root, ann.body, what=f"video cut: {what} {ann.id}")


def _cuts_dir(project) -> Path:
    return project.root.joinpath(*_CUTS_DIR)


def _probe_geometry(video: Path) -> tuple[int | None, int | None, int | None]:
    """``(width, height, fps)`` of the first video stream, or ``None``s."""
    if shutil.which("ffprobe") is None:
        return None, None, None
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,r_frame_rate",
                "-of",
                "csv=p=0",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        w, h, rate = out.split(",")[:3]
        num, _, den = rate.partition("/")
        fps = round(float(num) / float(den or 1))
        return int(w), int(h), int(fps)
    except Exception:  # noqa: BLE001 — geometry is advisory
        return None, None, None


def _complete(skel: Annotation, artifact, out_path: Path, **extra) -> Annotation:
    width, height, fps = _probe_geometry(out_path)
    return skel.model_copy(
        update={
            "body": {
                **skel.body,
                "artifact_id": artifact.asset_id,
                "url": file_url(out_path),
                "duration_s": artifact.duration_s or 0.0,
                "width": width or skel.body.get("width"),
                "height": height or skel.body.get("height"),
                "fps": fps or skel.body.get("fps"),
                **extra,
            }
        }
    )


def _hit_file_exists(project, hit: Annotation) -> bool:
    """Whether the cut's OWN file is there — not merely its bytes somewhere.

    Deliberately the recorded url, not :func:`media_path`: a cut is a
    deliverable at that path (Downloads lists ``data/cuts``), so a cut whose
    file was deleted must re-render and put it back, even when a catalog blob
    of the same bytes survives (braidio#90 review).
    """
    url = hit.body.get("url")
    return bool(url) and url_to_path(url).exists()


def _reverify(project, hit: Annotation) -> Annotation:
    """Re-record ``hit``'s verifying trace under its own id.

    The early-cutoff case done by hand: a parent's digest moved (a still's
    caption), the plan re-derived to the same value, so the existing node is
    the current one — it only needs its trace refreshed, not a twin.
    """
    from lacing import RationalTime

    refreshed = hit.model_copy(
        update={
            "provenance": hit.provenance.model_copy(
                update={"generated_at_time": RationalTime.now()}
            )
        }
    )
    project.graph.remove_annotation(hit.id)
    project.graph.add_annotation(refreshed)
    return refreshed


def _reuse(project, skel: Annotation):
    """The idempotent / compare-and-skip half every render Transform shares.

    Both doors check that the file is still there: a node — fresh or cached —
    whose mp4 was deleted out of band is not a cut, it is a record of one,
    and the next stage would fail on it.
    """
    existing = fresh_equivalent(project, skel)
    if existing is not None and not _hit_file_exists(project, existing):
        existing = None
    if existing is None:
        hit = cached_output(project, TIER_VIDEO_CUT, skel.body["cache_key"])
        if hit is not None and _hit_file_exists(project, hit):
            same_parents = set(hit.provenance.was_derived_from) == set(
                skel.provenance.was_derived_from
            )
            if same_parents and planned_value(hit) == planned_value(skel):
                existing = _reverify(project, hit)
            else:
                existing = adopt_output(skel, hit)
                project.graph.add_annotation(existing)
    if existing is None:
        return None
    return TransformResult(annotations=(existing,), artifacts=(), cost_usd_actual=0.0)


# --- video_cut.render -------------------------------------------------------


def _render_settings(params: dict) -> dict:
    size = [int(v) for v in params.get("size", DEFAULT_SIZE)]
    return {"size": size, "fps": int(params.get("fps", DEFAULT_FPS))}


def _refuse_unlicensed_under_published(profile: str, stills) -> None:
    """A published cut may not carry a still whose licence is unrecorded
    (plan §10: the rights position is surfaced, never assumed)."""
    if profile != Profile.PUBLISHED.value:
        return
    unlicensed = sorted(
        str(s.body["key"]) for s in stills if not (s.body.get("license") or "").strip()
    )
    if unlicensed:
        raise ValueError(
            f"video cut: profile 'published' refuses stills with no recorded "
            f"licence: {unlicensed}. Record `license` on each, or cut under "
            "'personal'."
        )


#: Relative tolerance on a stored path's ``output_aspect`` against the cut's —
#: burns' own (``burns.moves._ASPECT_REL_TOL``). Any looser and a path this
#: check passes would still raise ``MoveError`` from execute, after every
#: canvas is prepared (a 1366x768 path is 0.00087 off 1920x1080).
_ASPECT_REL_TOL = 1e-9


def _records_size(still: Annotation) -> bool:
    """Whether a still records the size :func:`path_on_canvas` needs (the
    render reads the exact size, after any crop, from the picture itself)."""
    return bool(still.body.get("width") and still.body.get("height"))


def _check_stored_paths(panels, settings: dict, stills) -> None:
    """A stored ``path`` for another aspect that cannot be converted fails the
    PLAN, before any canvas is prepared.

    Converting it (:func:`path_on_canvas`) needs the still's size, because the
    path's rectangles are relative to the prepared canvas of the delivery it
    was authored for and ``prepare_still`` letterboxes the still differently at
    another aspect. Without the conversion, burns' canvas-relative refit
    frames a different part of the still (measured: a 3x push on a landscape
    subject re-cut vertical ends ~60% blurred fill; post-hoc review of
    thorwhalen/braidio#81), so a still that records no size refuses instead.
    """
    w, h = settings["size"]
    aspect = w / h
    for panel, still in zip(panels, stills):
        if panel.body.get("footage"):
            continue  # its move is not rendered, so its path frames nothing
        stored = panel.body.get("path") or {}
        if _crosses_aspect(stored, aspect) and not _records_size(still):
            raise ValueError(
                f"{RENDER_NAME}: panel {panel.id} stores a path authored for aspect "
                f"{float(stored['output_aspect']):.4f}; this cut is {w}x{h} "
                f"({aspect:.4f}), and its still {still.id} records no width/height "
                "to convert the path with. Record the still's size, re-author the "
                "path for this delivery, or clear it and let the move resolve."
            )


def _motion_cache_key(transform, *, audio_id, settings, panels, stills) -> str:
    from nw.transforms import cache_key as transform_cache_key

    parts = [str(audio_id), _json(settings), resolver_identity()]
    aspect = settings["size"][0] / settings["size"][1]
    for panel, still in zip(panels, stills):
        start, end = _interval_s(panel)
        footage = panel.body.get("footage")
        if footage:
            # Only what reaches a pixel: the recording, where it starts, and the
            # span. A move edit on footage renders nothing, so it re-renders
            # nothing. (A still panel's key below is exactly what it always was.)
            parts += [
                "footage",
                str(footage["artifact_id"]),
                f"{float(footage.get('in_s', 0)):.3f}",
                f"{start:.3f}",
                f"{end:.3f}",
            ]
            continue
        parts += [
            str(still.body["artifact_id"]),
            _json(still.body.get("crop")),
            f"{start:.3f}",
            f"{end:.3f}",
            str(panel.body.get("move")),
            str(panel.body.get("zoom")),
            _json(panel.body.get("focus")),
            _json(panel.body.get("path")),
            str(panel.body.get("seed")),
        ]
        if _crosses_aspect(panel.body.get("path"), aspect):
            # a converted path: keys only these cuts, so every same-aspect key
            # is exactly what it was (and a #81-era naive refit is not served)
            parts.append(f"path-refit@{_PATH_REFIT_VERSION}")
    return transform_cache_key(transform, "video-cut-motion", *parts)


def _crop_still(
    src: Path, crop: dict | None, workdir: Path, *, artifact_id: str, suffix: str = ""
) -> Path:
    """``src`` cropped to ``crop`` (a RectV1 dump), cached by bytes + crop."""
    if not crop:
        return src
    from PIL import Image

    tag = hashlib.sha256(f"{artifact_id}|{_json(crop)}".encode()).hexdigest()
    ext = suffix or src.suffix.lower() or ".png"
    dst = workdir / f"crop_{tag[:_NAME_DIGEST_CHARS]}{ext}"
    if dst.exists():
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        img = img.convert("RGB")
        w, h = img.size
        box = (
            round(crop["x"] * w),
            round(crop["y"] * h),
            round((crop["x"] + crop["w"]) * w),
            round((crop["y"] + crop["h"]) * h),
        )
        encode = (
            {"quality": _CROP_JPEG_QUALITY} if dst.suffix in (".jpg", ".jpeg") else {}
        )
        from braidio.video import save_atomically

        save_atomically(img.crop(box), dst, **encode)
    return dst


def _content_keyed_prepare(workdir: Path, size: tuple[int, int]):
    """A ``prepare=`` for :func:`braidio.video.render_video` that names each
    canvas by the source's **bytes** and the frame size — never by its
    ordinal or its filename stem. ``render_video``'s default naming
    (``{i:03d}_{stem}.jpg``) plus ``prepare_still``'s existing-file shortcut
    would otherwise serve a stale canvas for a swapped still with the same
    stem, or a landscape canvas to a vertical cut.
    """
    from braidio.video import prepare_still

    def prepare(src, _dst_ignored, *, size=size):
        digest = hashlib.sha256(Path(src).read_bytes()).hexdigest()[:_NAME_DIGEST_CHARS]
        dst = workdir / f"canvas_{digest}_{size[0]}x{size[1]}_v{_CANVAS_VERSION}.jpg"
        return prepare_still(src, dst, size=size)

    return prepare


def _frames_dir(project) -> Path:
    """Where prepared canvases and cropped stills are cached (shared by the
    render and :func:`panel_path`, so a preview warms the render's cache)."""
    return _cuts_dir(project) / "_frames"


def _panel_source(project, panel: Annotation, index: dict, workdir: Path) -> Path:
    """The picture a panel shows: its still's bytes, after the still's crop."""
    still = _still_of(panel, index)
    return _crop_still(
        _local_path(project, still, "still"),
        still.body.get("crop"),
        workdir,
        artifact_id=str(still.body["artifact_id"]),
        suffix=_recorded_suffix(still),
    )


def panel_footage(project, panel: Annotation):
    """The recorded video a footage panel plays (a ``braidio.video.Footage``),
    or ``None`` for a still panel.

    Ask this before :func:`panel_path`: a footage panel plays its recording as a
    straight cut, so no camera move is rendered over it, and a preview should
    play ``footage.path`` from ``footage.in_s`` rather than move over the poster.
    """
    from braidio.video import Footage

    footage = panel.body.get("footage")
    if not footage:
        return None
    path = media_path(
        project.root, footage, what=f"video cut: footage of panel {panel.id}"
    )
    return Footage(str(path), float(footage.get("in_s", 0.0)))


def _recorded_suffix(ann: Annotation) -> str:
    """The file extension the body's url records (a catalog blob has none)."""
    url = ann.body.get("url")
    return url_to_path(url).suffix.lower() if url else ""


def _path_on_canvas(panel_body: dict, canvas, source: Path, *, aspect: float):
    """The move for ``panel_body`` on ``canvas`` (the prepared ``source``)."""
    return path_for_panel(
        panel_body, image=canvas, aspect=aspect, image_size=_image_size(source)
    )


def panel_path(project, panel: Annotation, *, size: tuple[int, int]):
    """The ``BurnsPath`` the motion render will use for ``panel`` at ``size``.

    The one way to ask "what will the camera do on this panel?": the still's
    crop, the prepared canvas the render frames on (same file, same cache),
    and :func:`path_for_panel` with the still's size, so the focus and burns'
    ``content_box`` are mapped exactly as ``video_cut.render`` maps them. A
    preview built any other way drifts from the film the day either changes
    (the studio's move preview is the caller this exists for).

    ``size`` is the delivery ``(width, height)`` — the cut's
    ``settings["size"]``. Reads the still's bytes and writes the canvas into
    the project's frame cache.

    A footage panel renders no move: check :func:`panel_footage` first. For
    one, this still answers (the move over its poster), so a caller that
    predates footage keeps working, but the answer describes nothing in the film.
    """
    resolver_identity()  # refuses an old burns with the message the render gives
    size = (int(size[0]), int(size[1]))
    index = graph_index(project)
    _check_stored_paths([panel], {"size": size}, [_still_of(panel, index)])
    workdir = _frames_dir(project)
    workdir.mkdir(parents=True, exist_ok=True)
    source = _panel_source(project, panel, index, workdir)
    canvas = _content_keyed_prepare(workdir, size)(source, None)
    return _path_on_canvas(panel.body, canvas, source, aspect=size[0] / size[1])


@register_transform(RENDER_NAME)
class VideoCutRender(BaseTransform):
    """Panels (+ their stills + the episode audio) → ``video-cut/v1`` (motion)."""

    name = RENDER_NAME
    input_kinds = (VIDEO_PANEL_V1, STILL_V1, EPISODE_RENDER_V1)
    output_kind = VIDEO_CUT_V1
    is_batch = True

    def plan(
        self, project, inputs: TransformInputs, *, params=None
    ) -> tuple[Plan, tuple[Annotation, ...]]:
        params = dict(params or {})
        panels = _ordered_panels(
            [a for a in inputs.primary if a.tier == TIER_VIDEO_PANEL]
        )
        if not panels:
            raise ValueError(f"{RENDER_NAME}: needs at least one video-panel")
        index = graph_index(project)
        episode = _episode_of(panels, index)
        audio_id = episode.body.get("artifact_id")
        if not audio_id:
            raise ValueError(
                f"{RENDER_NAME}: episode {episode.id} has no rendered audio"
            )
        stills = [_still_of(p, index) for p in panels]
        profile = str(episode.body.get("profile", Profile.PERSONAL.value))
        _refuse_unlicensed_under_published(profile, stills)

        settings = _render_settings(params)
        _check_stored_paths(panels, settings, stills)
        cache_key = _motion_cache_key(
            self, audio_id=audio_id, settings=settings, panels=panels, stills=stills
        )
        unique_stills = list({s.id: s for s in stills}.values())
        full = TransformInputs(
            primary=tuple(panels),
            context={STILL_V1: tuple(unique_stills), EPISODE_RENDER_V1: (episode,)},
        )
        skeleton = Annotation(
            id=uuid.uuid4(),
            tier=TIER_VIDEO_CUT,
            reference=node_ref(TIER_VIDEO_CUT),
            body=VideoCutBodyV1(
                label=str(params.get("label", "motion")),
                stage="motion",
                profile=profile,
                audio_artifact_id=str(audio_id),
                panel_ids=tuple(str(p.id) for p in panels),
                cache_key=cache_key,
                settings=settings,
            ).model_dump(mode="json"),
            body_schema_uri=VIDEO_CUT_V1,
            provenance=derive_provenance(self, full, attributed_to="agent:braidio"),
        )
        return Plan(calls=()), (skeleton,)

    def execute(
        self,
        project,
        plan: Plan,
        skeleton: tuple[Annotation, ...],
        *,
        use_cache: bool = True,
        force: bool = False,
    ) -> TransformResult:
        import braidio.video as video  # runtime attr access: tests patch render_video

        skel = skeleton[0]
        if use_cache and not force:
            reused = _reuse(project, skel)
            if reused is not None:
                return reused

        index = graph_index(project)
        panels = _panels_from_ids(skel.body["panel_ids"], index)
        episode = require_tier(resolve_parents(skel, index), TIER_EPISODE_RENDER)
        audio_path = _local_path(project, episode, "episode")
        size = tuple(skel.body["settings"]["size"])
        fps = int(skel.body["settings"]["fps"])
        aspect = size[0] / size[1]

        workdir = _frames_dir(project)
        video_panels = []
        sources: list[Path] = []
        for panel in panels:
            src = _panel_source(project, panel, index, workdir)
            sources.append(src)
            start, end = _interval_s(panel)
            video_panels.append(
                video.Panel(
                    start,
                    end,
                    str(src),
                    zoom=float(panel.body["zoom"]),
                    footage=panel_footage(project, panel),
                )
            )

        def path_for(canvas, i, panel):
            return _path_on_canvas(panels[i].body, canvas, sources[i], aspect=aspect)

        out_path = _cuts_dir(project) / f"{skel.id}_motion.mp4"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        video.render_video(
            video_panels,
            audio_path=audio_path,
            out_path=out_path,
            size=size,
            fps=fps,
            workdir=workdir,
            prepare=_content_keyed_prepare(workdir, size),  # = panel_path's
            path_for=path_for,
        )
        artifact = media_artifact(
            out_path,
            kind="video",
            transform_name=self.name,
            derived_from=skel.provenance.was_derived_from,
            duration_s=safe_duration(out_path),
            mime="video/mp4",
        )
        completed = _complete(skel, artifact, out_path)
        project.graph.add_annotation(completed)
        return TransformResult(
            annotations=(completed,),
            artifacts=(artifact,),
            cost_usd_actual=0.0,
            cache_hit_savings_usd=0.0,
        )


def _image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as img:
        return img.size


# --- video_cut.finish -------------------------------------------------------


def _finish_settings(motion: Annotation, params: dict) -> dict:
    return {
        **dict(motion.body.get("settings") or {}),
        "delivery": str(params.get("delivery", DEFAULT_DELIVERY)),
        "captions": bool(params.get("captions", True)),
        "burn_captions": bool(params.get("burn_captions", False)),
        "caption_chars": int(params.get("caption_chars", DEFAULT_CAPTION_CHARS)),
        "credits_s": float(params.get("credits_s", 0.0)),
        "credits_heading": str(params.get("credits_heading", "Images")),
    }


def _captions_srt(project, episode: Annotation, *, max_chars: int) -> str:
    from braidio.captions import captions_for
    from braidio.transforms._episode import episode_script, episode_timeline

    return captions_for(
        episode_script(project, episode),
        episode_timeline(project, episode),
        max_chars=max_chars,
    )


def _card_payload(body: dict) -> tuple[dict, str]:
    """``(tituli payload, slot)`` for a label-track body."""
    kind = str(body["kind"])
    lines = [str(line) for line in body.get("lines", ())]
    headline = body.get("headline") or (lines[0] if lines else "")
    rest = lines if body.get("headline") else lines[1:]
    if kind == "title":
        payload = {"kind": "title", "text": headline, "subtitle": " ".join(rest)}
    elif kind == "tag":
        payload = {"kind": "lower_third", "text": headline, "role": " ".join(rest)}
    else:
        payload = {"kind": "note", "text": headline, "lines": rest}
    return payload, str(body.get("slot") or _KIND_SLOTS[kind])


def _overlays(panels, stills_by_id: dict, label_tracks):
    """The resolved tituli overlays for a finish: cards, then per-still labels.

    Pure, and run at **plan** time too: tituli's ``resolve`` raises on two
    equal-weight overlays contending for one slot (an authoring error), and
    that must fail the plan, not the render after it.

    **A disclaimer outranks a card** (thorwhalen/braidio#85). A ``disclaimed``
    panel shows a picture that is honest only while its label says what it is
    ("The Beach Boys in Central Park, 1971 — not this concert"), so its label
    is held for the whole panel, at every appearance, and any heavier card in
    the label's slot yields that stretch of time — cut around it, not the
    other way round. Left to tituli's ordinary rule (the heavier overlay owns
    the moment), the first viewing showed the picture under the tag alone,
    which is the false claim the label existed to prevent.
    """
    from tituli import UNLABELLED, Label, Span, TimedOverlay, resolve, schedule_labels

    cards = []
    for track in label_tracks:
        start, end = _interval_s(track)
        payload, slot = _card_payload(track.body)
        cards.append(
            TimedOverlay(
                None,
                start,
                end,
                slot=slot,
                weight=int(track.body.get("weight", 2)),
                payload=payload,
            )
        )
    spans, disclaimed = [], []
    for panel in panels:
        start, end = _interval_s(panel)
        span = Span(start, end, key=str(panel.body["still_id"]))
        (disclaimed if panel.body.get("disclaimed") else spans).append(span)

    def label_for(span):
        body = stills_by_id[span.key].body
        if not body["labelled"]:
            return UNLABELLED
        return Label(str(body["subject"]), _short_attribution(body), key=body["key"])

    cards = _yield_to_disclaimers(cards, disclaimed)
    labels = schedule_labels(
        spans, label_for, suppressed_by=cards, weight=_LABEL_WEIGHT
    )
    if disclaimed:
        labels += schedule_labels(
            disclaimed,
            label_for,
            weight=_LABEL_WEIGHT,
            hold_s=float("inf"),
            repeat_gap_s=0.0,
        )
    return resolve([*cards, *labels])


def _yield_to_disclaimers(cards, disclaimed_spans):
    """``cards`` with the disclaimed spans cut out of every card in the label slot.

    A card that loses its middle becomes two pieces; a piece shorter than
    tituli's readable minimum is dropped (a flash is not a card).

    >>> from tituli import Span, TimedOverlay
    >>> card = TimedOverlay(None, 0.0, 20.0, slot=_LABEL_SLOT, weight=2)
    >>> [(c.start, c.end) for c in _yield_to_disclaimers([card], [Span(5.0, 9.0, key="s")])]
    [(0.0, 5.0), (9.0, 20.0)]
    >>> other = TimedOverlay(None, 0.0, 20.0, slot="bottom-left", weight=2)
    >>> _yield_to_disclaimers([other], [Span(5.0, 9.0, key="s")]) == [other]
    True
    """
    from tituli import TimedOverlay
    from tituli.schedule import MIN_READABLE_S

    out = list(cards)
    for span in disclaimed_spans:
        kept = []
        for c in out:
            if c.slot != _LABEL_SLOT or c.weight <= _LABEL_WEIGHT:
                kept.append(c)
                continue
            if not (c.start < span.end and span.start < c.end):
                kept.append(c)
                continue
            for a, b in ((c.start, span.start), (span.end, c.end)):
                if b - a >= MIN_READABLE_S:
                    kept.append(
                        TimedOverlay(
                            c.layout,
                            a,
                            b,
                            c.slot,
                            c.weight,
                            c.fade,
                            c.payload,
                            dict(c.meta),
                        )
                    )
        out = kept
    return out


def _card_named_by(text, overlays) -> str:
    """Name the label-track card whose text ``text`` is, for an error message.

    >>> from types import SimpleNamespace as NS
    >>> card = NS(payload={"kind": "title", "text": "T", "subtitle": "long sub"}, start=4.0)
    >>> _card_named_by("long sub", [card])
    "a 'title' card at 4.0s"
    >>> _card_named_by("nope", [card])
    'an overlay tituli could not identify'
    """
    if not text:
        return "an overlay tituli could not identify"
    for overlay in overlays:
        payload = overlay.payload
        if not isinstance(payload, dict):
            continue
        texts = [payload.get(k) for k in ("text", "subtitle", "role")]
        texts += list(payload.get("lines") or ())
        texts.append(" ".join(payload.get("lines") or ()))
        if text in texts:
            return f"a {payload.get('kind')!r} card at {float(overlay.start):.1f}s"
    return "an overlay tituli could not identify"


def _caption_overflow_error(exc, overlays, *, size, delivery: str):
    """Re-raise a ``tituli.compose.TextDoesNotFit`` naming the still and the
    delivery it happened on (thorwhalen/braidio#77 item 4).

    tituli's own message names the truncated text but not *which* still or
    *which* cut's aspect made it refuse — a caption that fits the same still's
    16:9 cut can still overflow a 9:16 one (type is sized as a fraction of
    frame height but has to fit the frame's width), so the author needs both
    to know what to shorten. Matches ``exc.text`` against each overlay's
    payload — a still-label overlay's is a ``tituli.Label`` whose ``key`` is
    the still's key, and tituli fits its subject and its attribution
    separately, so either can be the text that overflowed. A card's payload
    is a dict (see :func:`_card_payload`) and is named by kind and start time
    instead. Falls back to a generic phrase when nothing matches — a worse
    message beats a new exception masking the original.
    """
    from tituli.compose import TextDoesNotFit

    still_key = next(
        (
            overlay.payload.key
            for overlay in overlays
            if exc.text
            in (
                getattr(overlay.payload, "text", None),
                getattr(overlay.payload, "attribution", None),
            )
            and getattr(overlay.payload, "key", None)
        ),
        None,
    )
    where = f"still {still_key!r}" if still_key else _card_named_by(exc.text, overlays)
    w, h = size
    # Reconstruct through tituli's own constructor (not a bare message) so the
    # structured attributes (`.text`, `.tried_size`, `.lines_at_min`,
    # `.max_lines`) a programmatic catcher relies on survive unchanged; only
    # the printed message gains the still + delivery tituli has no way to know.
    augmented = TextDoesNotFit(
        exc.text, exc.tried_size, exc.lines_at_min, exc.max_lines
    )
    augmented.args = (
        f"on {where}, at this cut's delivery ({delivery}, {w}x{h}, aspect "
        f"{w / h:.3f}): {augmented.args[0]}",
    )
    return augmented


def _short_attribution(body: dict) -> str:
    """The lower third's credit — author and licence, as a person reads them.

    The SECOND place a licence reaches a viewer, and the one that is burnt into
    the picture. ``license`` is the canonical code a gate compares (``by-sa``,
    ``pdm``); ``license_label`` is the spelling (``CC BY-SA 4.0``, ``pd``), and
    it is the spelling that belongs on screen — "by" is not a licence
    identifier and satisfies no attribution condition. Falls back to the code
    when no label was recorded, because a terse credit beats none.

    Measured when this was wrong: 78 of the 82 labelled stills across the three
    finished productions changed, e.g. ``'iHeartRadioCA · CC BY 3.0'`` became
    ``'iHeartRadioCA · by'``.

    >>> _short_attribution({"author": "EliziR", "license": "by-sa",
    ...                     "license_label": "CC BY-SA 4.0"})
    'EliziR · CC BY-SA 4.0'
    >>> _short_attribution({"author": "Ralph Earl", "license": "pdm"})
    'Ralph Earl · pdm'
    """
    shown = (body.get("license_label") or "").strip() or body.get("license")
    parts = [body.get("author"), shown]
    return " · ".join(str(p).strip() for p in parts if p and str(p).strip())


def _credit_lines(panels, stills_by_id: dict) -> list[str]:
    """One credit per still, in order of first use — raising on a missing licence."""
    seen: list[str] = []
    for panel in panels:
        key = str(panel.body["still_id"])
        if key not in seen:
            seen.append(key)
    return [credit_line(stills_by_id[k].body) for k in seen]


def _append_credits(
    film: Path,
    lines: list[str],
    dst: Path,
    *,
    size,
    fps: int,
    hold_s: float,
    heading: str,
) -> Path:
    """``film`` with a tituli credits roll appended, the audio padded to match."""
    from tituli import Credits, credits_cards, credits_frame, render
    from tituli.video import ffmpeg_path, frames_to_video

    frame = credits_frame(size)
    cards = credits_cards(Credits.from_lines(lines, heading=heading), frame=frame)
    per_card = max(1, round(hold_s / len(cards) * fps))

    def frames():
        for card in cards:
            img = render(card, frame).convert("RGB")
            for _ in range(per_card):
                yield img

    roll = dst.with_name(dst.stem + "_credits.mp4")
    frames_to_video(frames(), roll, size=size, fps=fps)
    pad = per_card * len(cards) / fps
    try:
        subprocess.run(
            [
                ffmpeg_path(),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(film),
                "-i",
                str(roll),
                "-filter_complex",
                f"[0:a]apad=pad_dur={pad:.3f}[a];[0:v][1:v]concat=n=2:v=1:a=0[v]",
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(dst),
            ],
            check=True,
            capture_output=True,
        )
    finally:
        roll.unlink(missing_ok=True)
    return dst


def _require_current_motion(motion: Annotation, panels, stills, index: dict) -> None:
    """Refuse a motion cut whose frames would differ if rendered now.

    ``finish`` reads the panels by id from the *current* graph; a panel whose
    still was swapped since the motion was rendered would get the new still's
    label composited over the old still's frames. The check is the motion
    key itself, re-derived from the current panels and stills — which is
    exactly "would the pixels differ" and nothing editorial, so a caption
    edit still passes (that is the edit this pass exists for).
    """
    episode = require_tier(resolve_parents(motion, index), TIER_EPISODE_RENDER)
    expected = _motion_cache_key(
        VideoCutRender(),
        audio_id=motion.body["audio_artifact_id"],
        settings={
            "size": list(motion.body["settings"]["size"]),
            "fps": int(motion.body["settings"]["fps"]),
        },
        panels=panels,
        stills=stills,
    )
    if (
        motion.body.get("cache_key") != expected
        or episode.body.get("artifact_id") != (motion.body["audio_artifact_id"])
    ):
        raise ValueError(
            f"{FINISH_NAME}: motion cut {motion.id} is out of date — a panel, a "
            "still's bytes or crop, the episode audio, or the move resolver "
            f"(burns) changed since it was rendered. Re-run {RENDER_NAME} over the "
            "panels first; with an unchanged burns that is a cache hit."
        )


@register_transform(FINISH_NAME)
class VideoCutFinish(BaseTransform):
    """A motion cut (+ label tracks, panels, stills) → ``video-cut/v1`` (delivered)."""

    name = FINISH_NAME
    input_kinds = (VIDEO_CUT_V1, LABEL_TRACK_V1, VIDEO_PANEL_V1, STILL_V1)
    output_kind = VIDEO_CUT_V1

    def plan(
        self, project, inputs: TransformInputs, *, params=None
    ) -> tuple[Plan, tuple[Annotation, ...]]:
        import nw
        from nw.transforms import cache_key as transform_cache_key

        params = dict(params or {})
        motion = inputs.primary[0]
        if motion.tier != TIER_VIDEO_CUT or motion.body.get("stage") != "motion":
            raise ValueError(
                f"{FINISH_NAME}: the first primary input must be a motion cut"
            )
        if not motion.body.get("artifact_id"):
            raise ValueError(
                f"{FINISH_NAME}: motion cut {motion.id} is not rendered yet"
            )
        explicit_tracks = [a for a in inputs.primary[1:] if a.tier == TIER_LABEL_TRACK]
        tracks = explicit_tracks or list(
            nw.annotations_at_tier(project.root, TIER_LABEL_TRACK)
        )
        tracks = sorted(tracks, key=_interval_s)

        index = graph_index(project)
        panels = _panels_from_ids(motion.body["panel_ids"], index)
        stills = {str(p.body["still_id"]): _still_of(p, index) for p in panels}
        _require_current_motion(
            motion, panels, [stills[str(p.body["still_id"])] for p in panels], index
        )
        episode = require_tier(resolve_parents(motion, index), TIER_EPISODE_RENDER)
        # The rights position is the episode's as it stands now, not as it was
        # when the motion was cut: a production re-profiled to `published` since
        # must be checked (and stamped) here too.
        profile = str(episode.body.get("profile", Profile.PERSONAL.value))
        _refuse_unlicensed_under_published(profile, stills.values())
        settings = _finish_settings(motion, params)
        _overlays(panels, stills, tracks)  # raises on an overlay collision, here

        parts = [str(motion.body["artifact_id"]), _json(settings), profile]
        for track in tracks:
            parts += [_json(track.body), _json(_interval_s(track))]
        for panel in panels:
            still = stills[str(panel.body["still_id"])]
            parts += [
                _json(_interval_s(panel)),
                still.body["key"],
                str(still.body["labelled"]),
                str(still.body.get("subject")),
                _short_attribution(still.body),
            ]
            if panel.body.get("disclaimed"):
                # only when set, so every key minted before the field stays put
                parts.append("disclaimed")
        if settings["captions"]:
            parts.append(
                _captions_srt(project, episode, max_chars=settings["caption_chars"])
            )
        if settings["credits_s"] > 0:
            # raises now, at plan time, if a still has no licence to credit
            parts += _credit_lines(panels, stills)
        cache_key = transform_cache_key(self, "video-cut-finish", *parts)

        full = TransformInputs(
            primary=(motion, *tracks),
            context={
                VIDEO_PANEL_V1: tuple(panels),
                STILL_V1: tuple(stills.values()),
            },
        )
        skeleton = Annotation(
            id=uuid.uuid4(),
            tier=TIER_VIDEO_CUT,
            reference=node_ref(TIER_VIDEO_CUT),
            body=VideoCutBodyV1(
                label=str(params.get("label", motion.body.get("label", "cut"))),
                stage="delivered",
                profile=profile,
                audio_artifact_id=str(motion.body["audio_artifact_id"]),
                panel_ids=tuple(motion.body.get("panel_ids", ())),
                cache_key=cache_key,
                settings=settings,
            ).model_dump(mode="json"),
            body_schema_uri=VIDEO_CUT_V1,
            provenance=derive_provenance(self, full, attributed_to="agent:braidio"),
        )
        return Plan(calls=()), (skeleton,)

    def execute(
        self,
        project,
        plan: Plan,
        skeleton: tuple[Annotation, ...],
        *,
        use_cache: bool = True,
        force: bool = False,
    ) -> TransformResult:
        skel = skeleton[0]
        if use_cache and not force:
            reused = _reuse(project, skel)
            if reused is not None:
                return reused

        index = graph_index(project)
        parents = resolve_parents(skel, index)
        motion = next(
            p
            for p in parents
            if p.tier == TIER_VIDEO_CUT and p.body.get("stage") == "motion"
        )
        tracks = sorted(
            (p for p in parents if p.tier == TIER_LABEL_TRACK), key=_interval_s
        )
        panels = _panels_from_ids(skel.body["panel_ids"], index)
        stills = {str(p.body["still_id"]): _still_of(p, index) for p in panels}
        episode = require_tier(resolve_parents(motion, index), TIER_EPISODE_RENDER)
        settings = dict(skel.body["settings"])
        size = tuple(settings["size"])
        fps = int(settings["fps"])

        cuts = _cuts_dir(project)
        staging = cuts / _STAGING_DIR
        staging.mkdir(parents=True, exist_ok=True)
        stages: list[Path] = []
        try:
            out_path, extra = _composite(
                project,
                skel,
                stages,
                cuts=cuts,
                staging=staging,
                motion_path=_local_path(project, motion, "motion cut"),
                panels=panels,
                stills=stills,
                tracks=tracks,
                episode=episode,
                settings=settings,
                size=size,
                fps=fps,
                transform_name=self.name,
            )
        finally:
            # a crash mid-chain leaves nothing behind that could be listed as a cut
            for stage in stages:
                if stage.exists():
                    stage.unlink()

        artifact = media_artifact(
            out_path,
            kind="video",
            transform_name=self.name,
            derived_from=skel.provenance.was_derived_from,
            duration_s=safe_duration(out_path),
            mime="video/mp4",
        )
        completed = _complete(skel, artifact, out_path, **extra)
        project.graph.add_annotation(completed)
        return TransformResult(
            annotations=(completed,),
            artifacts=(artifact,),
            cost_usd_actual=0.0,
            cache_hit_savings_usd=0.0,
        )


def _composite(
    project,
    skel: Annotation,
    stages: list,
    *,
    cuts: Path,
    staging: Path,
    motion_path: Path,
    panels,
    stills: dict,
    tracks,
    episode: Annotation,
    settings: dict,
    size,
    fps: int,
    transform_name: str,
) -> tuple[Path, dict]:
    """The text pass, stage by stage, under ``staging``; returns
    ``(out_path, extra body fields)``. Every intermediate is appended to
    ``stages`` as it is made, so the caller can remove them whether or not
    the chain finished."""
    current = motion_path
    extra: dict = {}
    overlays = _overlays(panels, stills, tracks)
    if overlays:
        from tituli.compose import TextDoesNotFit
        from tituli.video import overlay

        staged = staging / f"{skel.id}_labels.mp4"
        try:
            overlay(
                current,
                overlays,
                staged,
                size=size,
                delivery=settings["delivery"],
                workdir=staging / "_overlays",
            )
        except TextDoesNotFit as exc:
            raise _caption_overflow_error(
                exc, overlays, size=size, delivery=settings["delivery"]
            ) from exc
        current = staged
        stages.append(staged)

    if settings["captions"]:
        srt = _captions_srt(project, episode, max_chars=settings["caption_chars"])
        srt_path = cuts / f"{skel.id}.srt"
        srt_path.write_text(srt, encoding="utf-8")
        captions = media_artifact(
            srt_path,
            kind="text",
            transform_name=transform_name,
            derived_from=skel.provenance.was_derived_from,
            mime="application/x-subrip",
        )
        extra["captions_artifact_id"] = captions.asset_id
        if settings["burn_captions"]:
            from mixing.video import write_subtitles_in_video

            staged = staging / f"{skel.id}_captioned.mp4"
            write_subtitles_in_video(str(current), str(srt_path), str(staged))
            current = staged
            stages.append(staged)

    if settings["credits_s"] > 0:
        staged = staging / f"{skel.id}_credited.mp4"
        _append_credits(
            current,
            _credit_lines(panels, stills),
            staged,
            size=size,
            fps=fps,
            hold_s=settings["credits_s"],
            heading=settings["credits_heading"],
        )
        current = staged
        stages.append(staged)

    out_path = cuts / f"{skel.id}.mp4"
    if current == motion_path:
        shutil.copyfile(
            current, out_path
        )  # nothing to composite: the motion IS the cut
    else:
        shutil.move(str(current), out_path)
    return out_path, extra
