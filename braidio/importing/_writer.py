"""Write a normalized production manifest into a real braidio project graph.

The verb is :func:`import_production`. It takes a
:class:`~braidio.importing._manifest.ProductionManifest` and a destination
project root and writes the stills, the episode audio (one node per distinct
mix — Two Silences renders a different mix per cut), the panel track for each
cut, the editorial cards, and the cut records with their ``published`` links.

**Idempotent by construction.** Every annotation id is a UUIDv5 of the
production slug plus a stable key, so a second run addresses the same nodes
rather than writing a second track beside the first. A node whose value is
unchanged is *left alone* rather than rewritten, because rewriting moves
``generated_at_time`` and would stale a rendered cut for no reason.

Four rules this module enforces rather than documents, each of which was a
measured defect in the three finished productions:

1. **Every imported panel is ``push_in`` — unless the manifest says its moves
   were rendered** (``moves="rendered"``, see :func:`_panel_move`). The source encodes a ``push`` /
   ``drift`` alternation, and it was a **no-op**: ``drift`` reached burns as
   ``mode="auto"``, whose only use of the index was ``push = index % 2 == 1``,
   and every project set style by the same ordinal it passed as the index — so
   both halves landed on push-in and all 265 panels of all three films push in
   (thorwhalen/braidio#72). Writing ``auto`` today would hand that panel to
   ``burns.choose_move``, which picks from a weighted pool and yields a real
   directional drift about 40% of the time: a substantially different film,
   arrived at by an import that believed it was being faithful. The
   never-realised intent is recorded as :data:`MOVE_IMPORT_NOTE` on the project
   and on every cut, so drift can be introduced **deliberately** later.
2. **The library's zoom default is asserted, not trusted.** 178 Two Silences
   panels carry a zoom that was never recorded in data — it was
   ``braidio.video.Panel``'s dataclass default, read off the source. If that
   default has moved, the extracted number is already wrong and this is the
   last place anyone would look, so :func:`assert_recorded_zoom_default` fails
   the import loudly.
3. **Every licence is stored as its canonical code, and an unknown one fails.**
   All 15 spellings across the three productions resolve to ``by`` / ``by-sa``
   / ``cc0`` / ``pdm`` through ``illustration.licensing.normalize_license``, so
   a failure means something genuinely new rather than a gap in the table.
   ``StillBodyV1.license`` gets the **code**, because that is what a gate
   compares and an unrecognised spelling survives normalization unchanged and
   therefore fails a published-profile check *silently* — fewer hits, no error.
   The human spelling is not discarded: it goes to ``license_label``, which
   ``credit_line`` prefers, so a credit still reads "CC BY-SA 4.0" while a gate
   still reads ``by-sa`` (thorwhalen/illustration#24).
4. **A context card is written with ``weight >= 2``.** ``video_cut.finish``
   refuses an overlay collision at plan time, and a weight-1 card over a
   labelled still is one, so a weight-1 context card would make the cut
   unrenderable at the point where it is least obvious why.
5. **Every registered artifact's catalog row carries the host's route, never a
   ``file://`` path.** An imported artifact that is not registered is a 404 on
   every surface (see :mod:`braidio.importing._catalog`), and one registered
   with a local path puts the importing machine's home directory into a served
   record. Registration is on by default; a cross-device destination refuses
   rather than silently copying the production a second time.
6. **A rendered take's ``cache_key`` cannot be mistaken for a computed one.**
   Every imported take carries a key prefixed :data:`IMPORTED_CACHE_KEY_PREFIX`,
   which no ``nw.transforms.cache_key`` digest can ever equal — the same
   bargain as a cut's ``cache_key=None``. braidio did not render these, so
   nothing may serve one as though it had.

**The delivered cut comes into the project; the motion pass does not.** The
plan's original line — cuts "referenced in place, not copied", to fit the
server's disk — is superseded, because a reference to a path outside the
project is not retrievable through *any* surface, and on the server that path
does not exist at all. The result was three finished films a reader could read
and could not watch. So ``materialize_cuts`` defaults to ``"delivered"``: every
cut record's delivered mp4 is brought in and registered, and *every* cut counts
— one production's four cuts are four different edits, not four versions of
one, and they are the point of that production. The **motion** pass stays out
by default: it is the text-free intermediate a re-render can resume from,
evidence rather than a deliverable, and carrying it roughly doubles the weight.
Measured, delivered-only across the three productions is about 1.1 GB, which
fits; ``"all"`` adds the motion passes and ``"none"`` restores the old
reference-in-place behaviour and *says* in the report that those cuts are
unretrievable.

What it deliberately does **not** do: mark anything publishable the source did
not (every cut keeps its recorded profile, and the rights finding is carried as
project-level data), or invent a beat it cannot witness — a beat whose authored
text did not survive is imported with an **empty** ``text`` and counted, never
with the timeline's 48-character snippet, which a re-synthesis would cheerfully
speak.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from braidio.importing._catalog import (
    CatalogBackendMismatch,  # noqa: F401  (re-exported: callers catch it)
    CatalogReport,
    assert_local_backend,
    CrossDeviceCatalog,  # noqa: F401  (re-exported: callers catch it)
    hash_file,
    register_artifact,
)
from braidio.importing._manifest import (
    BeatRecord,
    CutRecord,
    LabelRecord,
    ProductionManifest,
    StillRecord,
)

#: Namespace for every id this importer mints. Fixed forever: change it and a
#: re-import stops addressing the nodes the last one wrote, which is precisely
#: the "two tracks instead of one" failure idempotency exists to prevent.
IMPORT_NAMESPACE = uuid.UUID("6b1f8c7e-4a2d-5e93-9c11-b7a0d2e4f501")

#: The move every imported panel gets. See rule 1 in the module docstring.
IMPORTED_MOVE = "push_in"

#: braidio.video.Panel's zoom default as it stood when the three productions
#: were extracted. Asserted at import; see rule 2.
RECORDED_PANEL_ZOOM_DEFAULT = 1.18


def _panel_move(manifest: ProductionManifest, move: str) -> str:
    """The move a panel is imported with: rule 1, or the rendered move as is.

    Under ``moves="rendered"`` the manifest's move is the burns move the
    delivered cut was framed with, so it is the truth and is written. ``auto``
    is refused there: it names no move, only a draw that ``burns.choose_move``
    makes from a seed, and the producer already knows what that draw was.

    >>> from types import SimpleNamespace as NS
    >>> _panel_move(NS(moves="source"), "auto")
    'push_in'
    >>> _panel_move(NS(moves="rendered"), "pull_out")
    'pull_out'
    """
    if manifest.moves != "rendered":
        return IMPORTED_MOVE
    from burns import MOVES

    if move == "auto" or move not in MOVES:
        raise ImportError_(
            f"moves='rendered' needs the move each panel was rendered with, one "
            f"of {', '.join(m for m in MOVES if m != 'auto')}; got {move!r}"
        )
    return move


MOVE_IMPORT_NOTE = (
    "Every panel imported as push_in. The source alternated push/drift by "
    "ordinal, but the alternation was a no-op on the render path: drift "
    "reached burns as mode='auto', whose only use of the index was "
    "push = (index % 2 == 1), and the same ordinal drove both — so all panels "
    "pushed in. See thorwhalen/braidio#72. burns.choose_move now picks from a "
    "weighted pool, so writing 'auto' would invent a drift the film never had. "
    "The intent to vary the motion is recorded here and is now expressible "
    "deliberately, per panel, through the move vocabulary."
)

#: Rights positions that are NOT a licence to publish. The importer never
#: promotes a cut; the position is carried so a surface can show it.
_NON_PUBLIC_POSITIONS = frozenset({"private", "unlisted"})

#: Prefix on every imported take's / extraction's ``cache_key``. A computed key
#: is a 64-character SHA-256 digest (``nw.transforms.cache_key``), so a key
#: carrying this prefix can never equal one — which is the point: braidio did
#: not render these, and ``cached_output`` scans a tier for a key match. A
#: colliding key would let a later render adopt a shipped take as its own
#: output, which is the audio equivalent of serving a shipped mp4 from a cache.
IMPORTED_CACHE_KEY_PREFIX = "imported:"

#: What ``materialize_cuts`` accepts. See the module docstring for the argument
#: behind the default — briefly: a delivered cut is the deliverable and every
#: one of them counts, a motion pass is a resumable intermediate.
_MATERIALIZE_CHOICES = frozenset({"delivered", "all", "none"})

#: Timeline beat kinds that are **spoken narration**. The renderer writes the
#: delivery *style* in place of ``"narration"`` when a beat declared one
#: (``_member_role``), so this cannot be a single literal — anything that is
#: not a clip, a break or a dialogue is narration, and an unknown kind is
#: refused rather than guessed (see :func:`_beat_role`).
#: ``"archive"`` is deliberately NOT here. It looks like a clip and is not:
#: Two Silences' nine archive beats are narration in an archival register —
#: they carry no ``source`` span and their labels are the spoken text. Filing
#: them as clips loses nine replaceable segments per production and writes
#: nine segment-extractions whose ``(start_s, end_s)`` are invented.
_CLIP_KINDS = frozenset({"clip", "segment"})
_BREAK_KINDS = frozenset({"scene-break", "sting", "scene_break"})
_DIALOGUE_KINDS = frozenset({"dialogue"})
_NARRATION_KINDS = frozenset({"narration"})

#: The whole reason a take is addressable, stated where the code is. Replacing
#: one is NOT a cheap in-place edit: see :data:`NARRATION_REPLACEMENT_NOTE`.
NARRATION_REPLACEMENT_NOTE = (
    "Replacing a narration take requires a RE-WEAVE of the episode, not an "
    "in-place swap. Every panel and card is pinned to the mix by a MediaRef "
    "whose asset_id is the content hash of the mix's bytes and whose interval "
    "is an offset into it, so a new take changes the mix's bytes (new hash, "
    "every reference dangling) and its own duration (every later beat, panel "
    "and card shifts). The graph says so: each cut's episode node derives from "
    "its members, and every panel derives from that episode, so replacing one "
    "take reads as stale through the whole picture track. What the import buys "
    "is that the segment is now a thing you can point at, play and replace at "
    "all — the re-weave is the cost of using it, and a surface must quote it "
    "as a re-render of the cut rather than as an edit."
)

_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


class ImportError_(Exception):
    """Raised when a manifest cannot be imported faithfully."""


@dataclass
class ImportReport:
    """What one import did, and what it could not settle.

    Returned rather than logged, so a CLI, a test and a future MCP tool all
    read the same answer.
    """

    production: str
    project_root: Path
    title: str
    rights_position: str
    stills_written: int = 0
    stills_unchanged: int = 0
    episodes: int = 0
    #: Footage files (recorded video footage panels cut from) written.
    footage: int = 0
    panels_by_cut: dict[str, int] = field(default_factory=dict)
    labels_by_cut: dict[str, int] = field(default_factory=dict)
    cuts_written: list[str] = field(default_factory=list)
    published_links: dict[str, str] = field(default_factory=dict)
    #: Stills with no ``title`` — they credit WITHOUT one, silently, so the
    #: count is surfaced rather than left to be discovered in a credit roll.
    untitled_stills: list[str] = field(default_factory=list)
    #: Stills whose ``attribution`` names no licence: rendering it verbatim
    #: would fail the licence condition. ``credit_line`` composes instead.
    bare_attributions: list[str] = field(default_factory=list)
    license_codes: dict[str, str] = field(default_factory=dict)
    beat_ids_renumbered: int = 0
    #: Media files copied into the project, and the bytes that cost. The
    #: project owns its bytes rather than linking to a shared source tree —
    #: see :func:`_place_into` for why the link is the wrong trade here.
    media_copied: int = 0
    bytes_copied: int = 0
    #: Members written per cut, by tier-ish role: narration / clip / break.
    beats_by_cut: dict[str, int] = field(default_factory=dict)
    #: Playable narration takes written per cut.
    takes_by_cut: dict[str, int] = field(default_factory=dict)
    #: ``"<cut>/<index>"`` for every narration beat whose authored text did
    #: not survive. Imported with an EMPTY text, never the snippet — so the
    #: count is the only place the loss is visible. See the module docstring.
    beats_without_text: list[str] = field(default_factory=list)
    #: Takes named by the manifest whose audio file is not on disk.
    takes_missing: list[str] = field(default_factory=list)
    #: ``"<cut>/<index>"`` for every clip whose span in the SOURCE recording
    #: was never persisted. Written with ``source_span_recorded=False`` rather
    #: than an invented ``(0.0, duration)``, which would be a false claim
    #: about where third-party material was cut from.
    segments_without_source: list[str] = field(default_factory=list)
    catalog: CatalogReport = field(default_factory=CatalogReport)
    gaps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def panels_total(self) -> int:
        return sum(self.panels_by_cut.values())

    @property
    def takes_total(self) -> int:
        return sum(self.takes_by_cut.values())

    def to_dict(self) -> dict[str, Any]:
        """A JSON-able summary (what the CLI's ``--json`` prints)."""
        return {
            "production": self.production,
            "project_root": str(self.project_root),
            "title": self.title,
            "rights_position": self.rights_position,
            "stills_written": self.stills_written,
            "stills_unchanged": self.stills_unchanged,
            "episodes": self.episodes,
            "footage": self.footage,
            "panels_by_cut": dict(self.panels_by_cut),
            "panels_total": self.panels_total,
            "labels_by_cut": dict(self.labels_by_cut),
            "cuts_written": list(self.cuts_written),
            "published_links": dict(self.published_links),
            "untitled_stills": list(self.untitled_stills),
            "bare_attributions": list(self.bare_attributions),
            "license_codes": dict(self.license_codes),
            "beat_ids_renumbered": self.beat_ids_renumbered,
            "media_copied": self.media_copied,
            "bytes_copied": self.bytes_copied,
            "beats_by_cut": dict(self.beats_by_cut),
            "takes_by_cut": dict(self.takes_by_cut),
            "takes_total": self.takes_total,
            "beats_without_text": list(self.beats_without_text),
            "takes_missing": list(self.takes_missing),
            "segments_without_source": list(self.segments_without_source),
            "catalog": self.catalog.to_dict(),
            "gaps": list(self.gaps),
            "notes": list(self.notes),
        }


# --- the four enforced rules ------------------------------------------------


def assert_recorded_zoom_default(expected: float = RECORDED_PANEL_ZOOM_DEFAULT) -> None:
    """Fail unless ``braidio.video.Panel``'s zoom default is still ``expected``.

    The one failure that is invisible afterwards. 178 Two Silences panels carry
    a zoom nothing recorded — it was this default, read off the library at
    extraction time. 1.18 against 1.14 is near-invisible on one panel and
    diverges over ten minutes.

    >>> assert_recorded_zoom_default()
    >>> assert_recorded_zoom_default(1.14)
    Traceback (most recent call last):
        ...
    braidio.importing._writer.ImportError_: braidio.video.Panel.zoom is 1.18...
    """
    import dataclasses

    from braidio.video import Panel

    actual = next(f for f in dataclasses.fields(Panel) if f.name == "zoom").default
    if abs(float(actual) - float(expected)) > 1e-9:
        raise ImportError_(
            f"braidio.video.Panel.zoom is {actual!r}, not the {expected!r} the "
            "extraction read off it. Panels whose zoom was never recorded in "
            "data took this default, so the manifest's zoom is now wrong and "
            "nothing downstream would notice. Re-extract against the current "
            "library, or pass the default that was in force."
        )


def normalized_licenses(stills: Iterable[StillRecord]) -> dict[str, str]:
    """``{still key: canonical code}``, raising on anything unrecognised.

    The gate, not the value: what gets written into the still body is the
    *recorded* spelling, because "CC BY-SA 4.0" is what a credit should read
    and "by-sa" is not. An unrecognised code survives ``normalize_license``
    unchanged and would therefore silently fail a downstream allowlist with no
    error at all — which is why an unknown fails here instead.
    """
    try:
        from illustration.licensing import normalize_license
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ImportError_(
            "importing a production needs `illustration` for licence "
            "normalization (pip install 'braidio[importing]'). It is the SSOT "
            "for the four provider dialects; a second copy of that table here "
            "is how a licence gate silently starts dropping images."
        ) from exc

    known = {"by", "by-sa", "by-nc", "by-nd", "by-nc-sa", "by-nc-nd", "cc0", "pdm"}
    codes: dict[str, str] = {}
    unknown: list[str] = []
    for still in stills:
        raw = (still.license or "").strip()
        if not raw:
            continue
        code = normalize_license(raw)
        codes[still.key] = code
        if code not in known:
            unknown.append(f"{still.key}: {raw!r} -> {code!r}")
    if unknown:
        raise ImportError_(
            "licence codes illustration.licensing.normalize_license does not "
            "recognise:\n  " + "\n  ".join(unknown) + "\nAll 15 spellings across "
            "the three finished productions resolve cleanly, so this is a "
            "genuinely new code rather than a gap in the table. Add it to "
            "illustration's LICENSE_ALIASES (a deliberate decision, never a "
            "convenience) before importing."
        )
    return codes


def _check_card_weights(cut: CutRecord) -> None:
    """Refuse a context card too light to survive ``video_cut.finish``."""
    light = [
        f"{label.kind}@{label.start:.1f}s (weight {label.weight})"
        for label in cut.labels
        if label.kind in ("context", "note") and label.weight < 2
    ]
    if light:
        raise ImportError_(
            f"cut {cut.label!r}: context cards need weight >= 2, got "
            + ", ".join(light)
            + ". video_cut.finish resolves overlays at PLAN time and a "
            "weight-1 card over a labelled still is a collision, so the cut "
            "would be refused before a frame is rendered."
        )


# --- graph plumbing ---------------------------------------------------------


def _mint(production: str, *parts: Any) -> uuid.UUID:
    """A stable annotation id for ``production`` and a key. See idempotency."""
    return uuid.uuid5(IMPORT_NAMESPACE, "/".join([production, *map(str, parts)]))


def _provenance(derived_from: Iterable[uuid.UUID] = ()):
    from lacing import Provenance, RationalTime

    return Provenance(
        was_generated_by="braidio:import_production",
        was_attributed_to="agent:braidio",
        was_derived_from=list(derived_from),
        generated_at_time=RationalTime.now(),
        activity="import",
    )


def _identity(ann) -> str:
    """Everything about ``ann`` a re-import would be rewriting, as one string.

    Deliberately excludes ``provenance.generated_at_time`` — that moves on
    every run by definition, and comparing it would make every re-import a
    rewrite, which stales every rendered cut downstream for no reason.
    """
    return json.dumps(
        {
            "tier": ann.tier,
            "body": ann.body,
            "schema": ann.body_schema_uri,
            "reference": ann.reference.model_dump(mode="json"),
            "parents": sorted(str(p) for p in ann.provenance.was_derived_from),
        },
        sort_keys=True,
        default=str,
    )


def _upsert(project, ann, index: dict) -> bool:
    """Write ``ann`` unless an identical node already sits under its id.

    Returns True when the graph changed. Replacement is remove-then-add: the
    store enforces id uniqueness, and a removal takes the annotation's
    verifying traces with it (nw#36) so no inert sidecar is left behind.
    """
    existing = index.get(ann.id)
    if existing is not None:
        if _identity(existing) == _identity(ann):
            return False
        project.graph.remove_annotation(ann.id)
    project.graph.add_annotation(ann)
    index[ann.id] = ann
    return True


def _node_ref(tier: str, ann_id: uuid.UUID):
    """A **deterministic** :class:`lacing.NodeRef` for a non-media node.

    braidio's own ``transforms._common.node_ref`` mints a fresh uuid into the
    ``scene_path`` on every call, which is right for a Transform (each run is a
    new node) and wrong for an import: the reference would differ on every run,
    so :func:`_identity` would never compare equal and a re-import would
    rewrite every still, episode and cut — moving ``generated_at_time`` and
    staling rendered cuts for no reason at all. Keying the path on the node's
    own (already deterministic) id makes a no-op re-import a genuine no-op.
    """
    from lacing import NodeRef, TimeInterval

    return NodeRef(
        scene_path=f"{tier}/{ann_id}",
        interval=TimeInterval.from_seconds(0.0, 1.0, rate=1000),
    )


def _media_ref(asset_id: str, start: float, end: float):
    from lacing import MediaRef, RationalTime, TimeInterval

    rate = 1000
    return MediaRef(
        asset_id=str(asset_id),
        interval=TimeInterval(
            RationalTime(int(round(start * rate)), rate),
            RationalTime(int(round(end * rate)), rate),
        ),
    )


def _artifact_for(path: Path, *, kind: str, mime: str | None = None, duration_s=None):
    from lacing import Artifact

    return Artifact.from_path(
        path,
        kind=kind,
        was_generated_by="braidio:import_production",
        was_attributed_to="agent:braidio",
        duration_s=duration_s,
        mime=mime,
    )


def _safe_name(key: str) -> str:
    """``'acoustic-guitar#1'`` -> ``'acoustic-guitar_1'``.

    >>> _safe_name("acoustic-guitar#1")
    'acoustic-guitar_1'
    >>> _safe_name("central-park-decay#3")
    'central-park-decay_3'
    """
    return _UNSAFE_IN_FILENAME.sub("_", key).strip("_")


def _rect_from_ltrb(box) -> Optional[dict[str, float]]:
    """A fractional ``(left, top, right, bottom)`` crop as a ``RectV1`` mapping.

    The one geometry conversion in this importer, and it is load-bearing. Both
    productions that crop record LTRB (``l, t, r, b = CROPS[slot]``; Hamilton's
    "a fractional (left, top, right, bottom) box"), while ``RectV1`` — and
    ``braidio.transforms._video_cut._crop_still``, which applies it — is
    ``(x, y, w, h)``. Passing the tuple through unchanged mis-frames every
    cropped still, and ``RectV1``'s unit-square check catches only the one crop
    whose left+right happens to exceed 1: nine of the ten here would be
    silently wrong.

    >>> _rect_from_ltrb((0.0, 0.0, 1.0, 0.88))
    {'x': 0.0, 'y': 0.0, 'w': 1.0, 'h': 0.88}
    >>> _rect_from_ltrb((0.05, 0.33, 0.96, 0.95))['w']
    0.91
    >>> _rect_from_ltrb(None) is None
    True
    >>> _rect_from_ltrb((0.9, 0.0, 0.1, 1.0))
    Traceback (most recent call last):
        ...
    braidio.importing._writer.ImportError_: crop (0.9, 0.0, 0.1, 1.0) is not...
    """
    if box is None:
        return None
    left, top, right, bottom = (float(v) for v in box)
    if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
        raise ImportError_(
            f"crop {tuple(box)!r} is not a fractional (left, top, right, "
            "bottom) box inside the unit square. The manifest records LTRB; "
            "if this is really (x, y, w, h) the extractor and the importer "
            "have drifted, and a wrong crop is invisible after the render."
        )
    return {
        "x": round(left, 6),
        "y": round(top, 6),
        "w": round(right - left, 6),
        "h": round(bottom - top, 6),
    }


def _beat_id(raw: Optional[str]) -> Optional[str]:
    """``'beat:12'`` -> ``'0012'``; anything else passes through unchanged.

    The extraction recorded the *timeline member index* a panel sat under.
    braidio spells that identity zero-padded (``BEAT_ID_WIDTH``), and
    ``picks_from_panels`` keys a re-plan's pick map on it — so leaving
    ``'beat:12'`` in place would make a later re-plan silently ignore every
    pick and fall back to cycling the pool, i.e. a different set of pictures
    with nothing raised. Converting is the assumption that timeline member
    index == script position, which holds for a script nothing filtered.

    >>> _beat_id("beat:12"), _beat_id("beat:0"), _beat_id(None), _beat_id("0007")
    ('0012', '0000', None, '0007')
    """
    if raw is None:
        return None
    text = str(raw)
    if text.startswith("beat:") and text[5:].isdigit():
        return f"{int(text[5:]):04d}"
    return text


def _beat_role(beat: BeatRecord) -> str:
    """Which kind of node a timeline beat becomes: narration / clip / break.

    The renderer records a narration beat's **delivery style** in the timeline's
    ``kind`` field in place of ``"narration"`` (``_member_role``), so this
    cannot be a lookup against one literal: Two Silences' ``"archive"`` beats
    are narration in an archival register, not clips. Anything that is not a
    clip, a break or a dialogue is therefore narration, and the style is
    carried onto the beat body so a re-weave reproduces it.

    The one contradiction that is refused rather than resolved: a beat that
    reads as narration while carrying a ``source`` span into another recording.
    A clip mis-imported as narration becomes a segment the studio offers to
    re-synthesize and replace — and on these productions that clip is a
    commercial master. Getting this wrong silently is how a rights posture
    becomes a rights incident.

    >>> from braidio.importing._manifest import BeatRecord
    >>> b = lambda **kw: BeatRecord(index=0, start=0.0, end=1.0, **kw)
    >>> _beat_role(b(kind="narration")), _beat_role(b(kind="archive"))
    ('narration', 'narration')
    >>> _beat_role(b(kind="clip", source=(1.0, 2.0))), _beat_role(b(kind="scene-break"))
    ('clip', 'break')
    >>> _beat_role(b(kind="narration", source=(1.0, 2.0)))
    Traceback (most recent call last):
        ...
    braidio.importing._writer.ImportError_: beat 0 reads as narration...
    """
    kind = (beat.kind or "").strip()
    if kind in _CLIP_KINDS:
        return "clip"
    if kind in _BREAK_KINDS:
        return "break"
    if kind in _DIALOGUE_KINDS:
        raise ImportError_(
            f"beat {beat.index} is a dialogue exchange, which this importer "
            "does not carry: a dialogue-render derives from a dialogue-cast "
            "recording which voice speaks each role, and none of the three "
            "finished productions has one to read. Importing it without the "
            "cast would write a render node whose only parent is missing."
        )
    if beat.source is not None:
        raise ImportError_(
            f"beat {beat.index} reads as narration (kind {kind!r}) but carries "
            f"a source span {beat.source!r} into another recording. A clip "
            "imported as narration becomes a segment a surface offers to "
            "re-synthesize — and on these productions that is a commercial "
            "master. Declare the kind, or drop the span."
        )
    return "narration"


def _beat_style(beat: BeatRecord) -> Optional[str]:
    """The delivery style a narration beat's timeline ``kind`` stands in for.

    >>> from braidio.importing._manifest import BeatRecord
    >>> _beat_style(BeatRecord(index=0, kind="archive", start=0.0, end=1.0))
    'archive'
    >>> _beat_style(BeatRecord(index=0, kind="narration", start=0.0, end=1.0)) is None
    True
    """
    kind = (beat.kind or "").strip()
    return None if kind in _NARRATION_KINDS or not kind else kind


#: What a manifest's own ``gaps`` prose says about the move mapping, and what
#: superseded it. The extraction concluded ``drift -> auto`` and that reasoning
#: was void before the importer shipped (``burns.choose_move`` became a
#: weighted pool, so ``auto`` is a real drift ~40% of the time). The importer
#: writes ``push_in`` regardless, so this is a READING hazard rather than a
#: behavioural one — but a gap note that contradicts the code is exactly how a
#: later reader re-derives the wrong answer with the manifest in hand.
_SUPERSEDED_GAP_MARKERS = ("drift -> auto", "drift is mapped to auto", "-> auto")

_GAP_CORRECTION = (
    "CORRECTION, applied by the importer: this manifest's own `gaps` text "
    "still describes the superseded move mapping (drift -> auto). It is void. "
    "burns.choose_move is a weighted pool, so 'auto' resolves to a real "
    "directional drift about 40% of the time against a source in which every "
    "panel pushed in. Every panel was imported as push_in — read the code, not "
    "that note. See thorwhalen/braidio#72."
)


def _stale_gap_corrections(manifest: ProductionManifest) -> list[str]:
    """Correct, in the report, any gap note that still asserts ``drift -> auto``.

    >>> from braidio.importing._manifest import ProductionManifest
    >>> m = ProductionManifest.model_validate(dict(
    ...     production="p", title="P", source_dir="p",
    ...     rights=dict(position="private", why="w"),
    ...     episode_audio=dict(path="e.mp3", duration_s=1.0),
    ...     stills=[], cuts=[], gaps=["MOVE ENCODING. push -> push_in; drift -> auto."]))
    >>> len(_stale_gap_corrections(m))
    1
    >>> m2 = m.model_copy(update={"gaps": ("nothing about moves",)})
    >>> _stale_gap_corrections(m2)
    []
    """
    text = " ".join(manifest.gaps).lower()
    if any(marker.lower() in text for marker in _SUPERSEDED_GAP_MARKERS):
        return [_GAP_CORRECTION]
    return []


def _imported_cache_key(production: str, audio_rel: str, index: int) -> str:
    """A take's cache key — unmistakable for a computed one. See rule 6.

    Keyed on the **mix**, not the cut, for the same reason the node ids are:
    a beat is a member of an episode, and two cuts made against one mix share
    its decomposition rather than each owning a copy of it.

    >>> _imported_cache_key("two-silences", "data/episodes/two-silences.mp3", 3)
    'imported:two-silences:data/episodes/two-silences.mp3:0003'
    """
    return f"{IMPORTED_CACHE_KEY_PREFIX}{production}:{audio_rel}:{index:04d}"


# --- the verb ---------------------------------------------------------------


def import_production(
    manifest: ProductionManifest,
    project_root,
    *,
    source_root=None,
    copy_media: bool = True,
    dry_run: bool = False,
    register_artifacts: bool = True,
    materialize_cuts: str = "delivered",
    allow_cross_device_copy: bool = False,
) -> ImportReport:
    """Write ``manifest`` into a braidio project at ``project_root``.

    **All or nothing — when the path did not already exist.** That qualifier
    is the whole of the guarantee, and it is deliberately not stronger. A
    refusal that fires after the first node is written (an unlinkable blob is
    the live case) would otherwise leave a CLI printing "import refused" over
    a directory holding a ``project.json``, a graph with one annotation and
    some media — the refusal saying nothing happened and the tree saying
    otherwise. So a project **this call created** is removed entirely.

    What is NOT promised, because promising it would mean deleting directories
    we did not create:

    - ``mkdir -p`` the path first — which is how people prepare one — and the
      rollback does not fire, so a refusal can leave exactly that half-written
      state. Measured.
    - A failed **re-import leaves an existing project partially updated**, not
      as it was found: annotations written before the failure keep their new
      values. A reader who retries expecting a clean slate is wrong. The price
      of never deleting somebody's project is that a failed run can leave it
      half-changed; re-running the import is how it converges.
    - Empty parent directories this call created are left behind. Removing
      directories because we also made them is the first step back down the
      road that produced the bug this guard exists for.

    Args:
        manifest: the normalized production (see ``load_manifest``).
        project_root: where the project lives. Created if absent; an existing
            project is updated in place, which is what makes a re-run one
            project rather than two.
        source_root: the folder ``manifest.source_dir`` is relative to. The
            manifest never stores an absolute path (one committed production
            manifest did, in a shared repo — only basenames come forward), so
            the caller supplies the root.
        copy_media: bring the stills, the episode audio and the narration
            takes into the project, so it is self-contained and survives being
            moved to a server. See :func:`_place_into` for why this is a copy
            and the blob store's link is not.
        dry_run: validate everything — files present, licences known, card
            weights sufficient, zoom default unmoved, beat kinds coherent —
            and write nothing.
        register_artifacts: register every artifact in the project's delivery
            catalog, so ``GET /api/artifacts/{id}/bytes`` answers instead of
            404ing. On by default: an unregistered artifact is a file the
            graph names and no surface can hand over.
        materialize_cuts: which rendered cuts come into the project —
            ``"delivered"`` (default; every cut's delivered mp4), ``"all"``
            (also the text-free motion passes), or ``"none"`` (reference them
            where they are, which means no surface can serve them; reported).
        allow_cross_device_copy: permit a real byte copy when the project's
            blob store is not on the media's filesystem. Off by default,
            because the copy is silent, is the whole production again, and is
            rarely what the caller meant.

    Returns:
        an :class:`ImportReport`.

    Raises:
        ImportError_: the manifest cannot be imported faithfully.
        CatalogBackendMismatch: the host reads artifacts from an object store.
        CrossDeviceCatalog: blobs cannot be linked and no copy was authorized.
    """
    root = Path(project_root).expanduser().resolve()
    # The ONLY safe licence to delete recursively is "this directory did not
    # exist when we started", because then everything in it is ours. The
    # tempting test — "there is no project.json here, so it is not a project
    # yet" — reads a caller-supplied path as consent to destroy it, and the
    # refusals that reach this handler are mostly ORDINARY VALIDATION (a
    # missing still, an unknown licence, a light context card, a bad crop, a
    # duplicate beat index), none of which used to write anything. Under that
    # test a manifest with one missing file deletes: a pre-existing directory
    # and its subtree; the directory a **--dry-run** was pointed at, whose
    # whole contract is to write nothing; and — one argument's typo away, since
    # the source root and the project root are separate arguments — the
    # irreplaceable source production itself. No gate here can tell a fresh
    # path from a wrong one, so the wrong one must survive.
    existed = root.exists()
    try:
        return _import_into_project(
            manifest,
            root,
            source_root=source_root,
            copy_media=copy_media,
            dry_run=dry_run,
            register_artifacts=register_artifacts,
            materialize_cuts=materialize_cuts,
            allow_cross_device_copy=allow_cross_device_copy,
        )
    except BaseException:
        # BaseException, not Exception: a Ctrl-C half-way through a 1 GB import
        # leaves exactly the half-written project this exists to prevent, and
        # it is the interruption a person is most likely to perform.
        if not existed and root.exists():
            shutil.rmtree(root, ignore_errors=True)
        raise


def _import_into_project(
    manifest: ProductionManifest,
    project_root,
    *,
    source_root=None,
    copy_media: bool = True,
    dry_run: bool = False,
    register_artifacts: bool = True,
    materialize_cuts: str = "delivered",
    allow_cross_device_copy: bool = False,
) -> ImportReport:
    """Write the manifest. See :func:`import_production` — this is its body."""
    import nw  # noqa: F401  (import registers the tiers/transforms)

    from braidio.bodies._render_nodes import EPISODE_RENDER_V1, RENDER_PROFILE_V1
    from braidio.bodies._video import (
        LABEL_TRACK_V1,
        STILL_V1,
        VIDEO_CUT_V1,
        VIDEO_PANEL_V1,
        LabelTrackBodyV1,
        StillBodyV1,
        VideoCutBodyV1,
        VideoPanelBodyV1,
    )
    from braidio.bodies._render_nodes import (
        NARRATION_SOURCE_TTS,
        EpisodeRenderBodyV1,
        RenderProfileBodyV1,
    )
    from braidio.project import Project
    from braidio.rights import PUBLISHABLE_CLIP_RIGHTS, Profile
    from braidio.transforms._common import (
        TIER_EPISODE_RENDER,
        TIER_LABEL_TRACK,
        TIER_RENDER_PROFILE,
        TIER_STILL,
        TIER_VIDEO_CUT,
        TIER_VIDEO_PANEL,
        file_url,
    )
    from lacing import Annotation

    # --- rule 2: the library default the extraction depended on -------------
    assert_recorded_zoom_default()

    if register_artifacts and not copy_media:
        raise ImportError_(
            "copy_media=False cannot be combined with register_artifacts=True. "
            "With no copy, the path handed to the catalog IS the source file, "
            "so the blob store hardlinks a shared working tree into the "
            "project: source, media and blob become one inode, and an "
            "in-place rewrite upstream changes the blob's bytes under a "
            "digest name that no longer describes them. A catalog whose blobs "
            "live in somebody else's tree is not a deliverable project. Take "
            "the copy, or pass register_artifacts=False and accept that "
            "nothing is retrievable."
        )

    if materialize_cuts not in _MATERIALIZE_CHOICES:
        raise ImportError_(
            f"materialize_cuts={materialize_cuts!r} is not one of "
            f"{sorted(_MATERIALIZE_CHOICES)}."
        )

    # Up front, beside the other refusals and BEFORE the project exists, so a
    # --dry-run reports it and a real run leaves nothing half-written. A
    # catalog the host will not read is the one failure this module cannot
    # survive quietly.
    if register_artifacts:
        assert_local_backend()

    source_root = Path(source_root or ".").expanduser().resolve()
    src = source_root / manifest.source_dir
    if not src.is_dir():
        raise ImportError_(
            f"source folder {src} does not exist. The manifest records "
            f"source_dir={manifest.source_dir!r}; pass source_root= the folder "
            "it is relative to."
        )

    report = ImportReport(
        production=manifest.production,
        project_root=Path(project_root).expanduser().resolve(),
        title=manifest.title,
        rights_position=manifest.rights.position,
        gaps=list(manifest.gaps),
    )
    if manifest.moves == "source":
        report.notes.append(MOVE_IMPORT_NOTE)
    report.notes.append(NARRATION_REPLACEMENT_NOTE)
    report.gaps.extend(_stale_gap_corrections(manifest))

    # --- rule 3: licences, and rule 4: card weights -------------------------
    report.license_codes = normalized_licenses(manifest.stills)
    for cut in manifest.cuts:
        _check_card_weights(cut)

    # Never promote what the source did not.
    if manifest.rights.position in _NON_PUBLIC_POSITIONS:
        public = [
            c.label for c in manifest.cuts if c.profile == Profile.PUBLISHED.value
        ]
        if public:
            raise ImportError_(
                f"{manifest.production}: rights position is "
                f"{manifest.rights.position!r} but cuts {public} declare the "
                "'published' profile. The importer never promotes a cut."
            )

    # --- resolve every file before writing anything -------------------------
    still_paths = {s.key: src / s.path for s in manifest.stills}
    missing = sorted(k for k, p in still_paths.items() if not p.is_file())
    if missing:
        raise ImportError_(
            f"{len(missing)} still files are not on disk: {missing[:8]}"
            + ("" if len(missing) <= 8 else f" (+{len(missing) - 8} more)")
        )
    used_keys = {p.still_key for cut in manifest.cuts for p in cut.panels}
    unknown_keys = sorted(used_keys - manifest.still_keys)
    if unknown_keys:
        raise ImportError_(
            f"panels name stills the manifest does not hold: {unknown_keys}"
        )

    footage_paths = {f.key: src / f.path for f in manifest.footage}
    missing = sorted(k for k, p in footage_paths.items() if not p.is_file())
    if missing:
        raise ImportError_(f"footage files are not on disk: {missing}")
    used_footage = {
        p.footage.key for cut in manifest.cuts for p in cut.panels if p.footage
    }
    unknown_footage = sorted(used_footage - manifest.footage_keys)
    if unknown_footage:
        raise ImportError_(
            f"panels name footage the manifest does not hold: {unknown_footage}"
        )

    audio_paths = {}
    for cut in manifest.cuts:
        audio_paths[cut.audio.path] = src / cut.audio.path
    audio_paths.setdefault(
        manifest.episode_audio.path, src / manifest.episode_audio.path
    )
    missing_audio = sorted(p for p, f in audio_paths.items() if not f.is_file())
    if missing_audio:
        raise ImportError_(f"episode audio not on disk: {missing_audio}")

    # Two mixes at different source paths but the same BASENAME would be
    # placed at one destination, and the second placement would overwrite the
    # first — silently, because each episode node's artifact is hashed from
    # the destination *after* its own placement, so the second node is right
    # and the first ends up pointing at the second's bytes. The productions
    # here all differ, so this refuses rather than renaming: a conditional
    # name is its own trap (adding a third mix would rename the first two).
    by_basename: dict[str, list[str]] = {}
    for rel in audio_paths:
        by_basename.setdefault(Path(rel).name, []).append(rel)
    clashes = {name: rels for name, rels in by_basename.items() if len(rels) > 1}
    if clashes:
        raise ImportError_(
            "two mixes share a file name and would overwrite each other in the "
            f"project: {clashes}. One episode would end up pointing at the "
            "other's audio, with nothing raised. Give them distinct names."
        )

    # Beat kinds are classified BEFORE anything is written, so a clip carrying
    # a narration kind (or a dialogue we cannot carry) refuses the import
    # rather than half-writing a picture track.
    take_paths: dict[tuple[str, int], Path] = {}
    for cut in manifest.cuts:
        seen_index: set[int] = set()
        for beat in cut.beats:
            if beat.index in seen_index:
                raise ImportError_(
                    f"cut {cut.label!r}: two beats share index {beat.index}. "
                    "The index is the member's identity and its position in "
                    "the episode's play order; a duplicate makes "
                    "ordered_member_ids a list that is not the order."
                )
            seen_index.add(beat.index)
            _beat_role(beat)  # raises on a contradiction or a dialogue
            if beat.take is not None:
                f = src / beat.take.path
                if not f.is_file():
                    report.takes_missing.append(f"{cut.label}/{beat.index}")
                else:
                    take_paths[(cut.label, beat.index)] = f
            if beat.take is not None and beat.take.source != NARRATION_SOURCE_TTS:
                raise ImportError_(
                    f"cut {cut.label!r} beat {beat.index}: take source "
                    f"{beat.take.source!r}. An imported take is always 'tts' — "
                    "it is what the production synthesized. 'upload' is what a "
                    "person's own recording becomes when they replace one, and "
                    "claiming it here erases the distinction the field exists "
                    "for."
                )

    # A member's POSITION in ordered_member_ids is matched against a timeline
    # beat's INDEX downstream (braidio.captions.cues_for), so the two must
    # agree — which they only do when the indices are contiguous from zero. A
    # gap shifts every cue after it onto the wrong beat, silently. Real data is
    # contiguous everywhere; nothing checked it.
    for cut in manifest.cuts:
        if not cut.beats:
            continue
        indices = sorted(b.index for b in cut.beats)
        if indices != list(range(len(indices))):
            raise ImportError_(
                f"cut {cut.label!r}: beat indices are {indices[:8]}, not "
                f"0..{len(indices) - 1}. A member's position in "
                "ordered_member_ids is matched against a timeline beat's "
                "index downstream, so a gap silently shifts every cue after "
                "it onto the wrong beat. Renumber, or carry the skipped "
                "members."
            )
        # The verbatim timeline is paired with the same positions (member_spans,
        # cues_for), so its indices must be positions of these beats: a 1-based
        # timeline hands every take its neighbour's interval, silently.
        try:  # read the way TimelineBreakdown.from_dict reads them
            spans = [
                int(b["index"]) for b in (cut.timeline or {}).get("beats") or ()
            ]
        except (KeyError, TypeError, ValueError):
            spans = [None]
        if len(set(spans)) != len(spans) or not all(
            i is not None and 0 <= i < len(indices) for i in spans
        ):
            raise ImportError_(
                f"cut {cut.label!r}: the timeline's beat indices {spans[:8]} are "
                f"not distinct positions in 0..{len(indices) - 1}. They are "
                "paired with the episode's members by position, so a shifted "
                "or duplicated index puts every member at a neighbour's place "
                "on the mix."
            )

    # rule 1's other door, checked HERE so a dry run catches it and a real run
    # refuses before writing anything (the write loop would fail half-way)
    for cut in manifest.cuts:
        for panel in cut.panels:
            _panel_move(manifest, panel.move)

    report.untitled_stills = sorted(s.key for s in manifest.stills if not s.title)
    report.bare_attributions = sorted(
        s.key
        for s in manifest.stills
        if s.attribution
        and s.license
        and s.license.lower().replace(" ", "")
        not in s.attribution.lower().replace(" ", "")
        and "creativecommons" not in s.attribution.lower()
        and "public domain" not in s.attribution.lower()
    )

    if dry_run:
        report.notes.append("dry run: validated, nothing written")
        return report

    # --- the project --------------------------------------------------------
    root = report.project_root
    if (root / "project.json").exists():
        project = Project(root)
    else:
        project = Project.init(root, title=manifest.title, force=True)
    project.update_spec(title=manifest.title, notes=_project_notes(manifest, report))

    index = {a.id: a for a in nw.iter_all_annotations(project.root)}
    profile = _common_profile(manifest)

    # One timestamp for the whole import, so a re-import produces byte-identical
    # rows and the catalog stops churning after the first run.
    stamped_at = _utc_now_iso()

    def _register(
        path: Path,
        artifact_id: str,
        *,
        kind: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        duration_s: Optional[float] = None,
    ) -> None:
        """Make one artifact retrievable, if this import was asked to."""
        if not register_artifacts:
            report.catalog.unregistered.append((str(path), "register_artifacts=False"))
            return
        register_artifact(
            root,
            path,
            artifact_id=artifact_id,
            kind=kind,
            generated_at=stamped_at,
            report=report.catalog,
            width=width,
            height=height,
            duration_s=duration_s,
            # The host's own `upload_artifact` puts its `note` into
            # `prompt`, so a human-readable note is that field's established
            # use for something no model generated — not a claim that one did.
            note=f"imported from {manifest.production}",
            allow_cross_device_copy=allow_cross_device_copy,
        )

    # --- the rights position, as structured graph data ----------------------
    _upsert(
        project,
        Annotation(
            id=_mint(manifest.production, "render-profile"),
            tier=TIER_RENDER_PROFILE,
            reference=_node_ref(
                TIER_RENDER_PROFILE, _mint(manifest.production, "render-profile")
            ),
            body=RenderProfileBodyV1(
                profile=profile,
                publishable_clip_rights=tuple(sorted(PUBLISHABLE_CLIP_RIGHTS)),
            ).model_dump(mode="json"),
            body_schema_uri=RENDER_PROFILE_V1,
            provenance=_provenance(),
        ),
        index,
    )

    # --- the stills ---------------------------------------------------------
    still_ids: dict[str, uuid.UUID] = {}
    for still in manifest.stills:
        dest = still_paths[still.key]
        if copy_media:
            dest = _place_into(
                still_paths[still.key],
                root / "data" / "stills",
                _safe_name(still.key) + still_paths[still.key].suffix,
                report,
            )
        artifact = _artifact_for(dest, kind="image")
        _register(
            dest,
            artifact.asset_id,
            kind="image",
            width=still.width,
            height=still.height,
        )
        ann_id = _mint(manifest.production, "still", still.key)
        still_ids[still.key] = ann_id
        body = StillBodyV1(
            key=still.key,
            artifact_id=artifact.asset_id,
            url=file_url(dest),
            width=still.width,
            height=still.height,
            title=still.title,
            # rule 3: the CODE is what a gate compares; the spelling a credit
            # reads rides beside it. `license_codes` is keyed only on stills
            # that recorded one, so an unlicensed still keeps its None.
            license=report.license_codes.get(still.key, still.license),
            license_label=still.license,
            license_url=still.license_url,
            attribution=still.attribution,
            source_page_url=still.source_page_url,
            author=still.author,
            author_url=still.author_url,
            cacheable=still.cacheable,
            labelled=still.labelled,
            subject=still.subject,
            about=still.about,
            crop=_rect_from_ltrb(still.crop),
            note=still.note,
        )
        written = _upsert(
            project,
            Annotation(
                id=ann_id,
                tier=TIER_STILL,
                reference=_node_ref(TIER_STILL, ann_id),
                body=body.model_dump(mode="json"),
                body_schema_uri=STILL_V1,
                provenance=_provenance(),
            ),
            index,
        )
        if written:
            report.stills_written += 1
        else:
            report.stills_unchanged += 1

    # --- the episode audio: resolve the mixes, WITHOUT writing them yet -----
    # The episode node carries its members and derives from them, so it cannot
    # be written until each cut's beats have been. Resolving the files here
    # keeps the ids deterministic and available to everything below.
    episode_ids: dict[str, uuid.UUID] = {}
    episode_assets: dict[str, str] = {}
    episode_dests: dict[str, Path] = {}
    durations = {manifest.episode_audio.path: manifest.episode_audio.duration_s}
    for cut in manifest.cuts:
        durations[cut.audio.path] = cut.audio.duration_s
    for rel, srcfile in audio_paths.items():
        dest = srcfile
        if copy_media:
            dest = _place_into(
                srcfile, root / "data" / "episodes", Path(rel).name, report
            )
        artifact = _artifact_for(
            dest, kind="audio", mime="audio/mpeg", duration_s=durations.get(rel)
        )
        _register(
            dest,
            artifact.asset_id,
            kind="audio",
            duration_s=durations.get(rel),
        )
        episode_ids[rel] = _mint(manifest.production, "episode", rel)
        episode_assets[rel] = artifact.asset_id
        episode_dests[rel] = dest

    # --- per cut: the script's beats and the takes that played them ---------
    members_by_audio: dict[str, tuple[str, ...]] = {}
    beats_by_audio: dict[str, str] = {}
    for cut in manifest.cuts:
        members = _import_beats(
            project,
            index,
            manifest,
            cut,
            root=root,
            copy_media=copy_media,
            take_paths=take_paths,
            report=report,
            register=_register,
        )
        rel = cut.audio.path
        # Compare the beats THEMSELVES, never the member ids. An id is
        # ``_mint(production, role, mix, index)`` — a pure function of position
        # — so two cuts with the same indices and roles mint the *same* ids
        # however different their words and their takes are. An id comparison
        # therefore passes exactly when it should refuse, and the second cut
        # silently overwrites the first's beat bodies and its take files while
        # the report says both were written.
        fingerprint = _beats_fingerprint(cut.beats)
        if rel in beats_by_audio and beats_by_audio[rel] != fingerprint:
            raise ImportError_(
                f"two cuts render against the same mix ({rel!r}) but describe "
                "different beats. A beat is a member of the EPISODE, so one "
                "mix has one decomposition and one play order; two cuts of it "
                "share that rather than each owning a copy. Reconcile the two "
                "beat lists, or give each cut its own mix."
            )
        beats_by_audio[rel] = fingerprint
        members_by_audio[rel] = members

    # --- the episode nodes, now that they have members ----------------------
    for rel in audio_paths:
        members = members_by_audio.get(rel, ())
        ann_id = episode_ids[rel]
        _upsert(
            project,
            Annotation(
                id=ann_id,
                tier=TIER_EPISODE_RENDER,
                reference=_node_ref(TIER_EPISODE_RENDER, ann_id),
                body=EpisodeRenderBodyV1(
                    profile=profile,
                    ordered_member_ids=members,
                    artifact_id=episode_assets[rel],
                    url=file_url(episode_dests[rel]),
                    duration_s=float(durations.get(rel) or 0.0),
                    timeline=_timeline_for(manifest, rel),
                ).model_dump(mode="json"),
                body_schema_uri=EPISODE_RENDER_V1,
                # The mix derives from its members, which is what makes
                # "replace this take" read as stale through the picture track
                # rather than as a change nothing notices.
                provenance=_provenance([uuid.UUID(m) for m in members]),
            ),
            index,
        )
        report.episodes += 1

    # --- footage: recorded video that panels cut from ------------------------
    footage_refs: dict[str, dict] = {}
    for footage in manifest.footage:
        dest = footage_paths[footage.key]
        if copy_media:
            dest = _place_into(
                dest,
                root / "data" / "footage",
                _safe_name(footage.key) + dest.suffix,
                report,
            )
        artifact = _artifact_for(dest, kind="video", duration_s=footage.duration_s)
        _register(
            dest,
            artifact.asset_id,
            kind="video",
            width=footage.width,
            height=footage.height,
            duration_s=footage.duration_s,
        )
        footage_refs[footage.key] = {
            "artifact_id": artifact.asset_id,
            "url": file_url(dest),
        }
    report.footage = len(footage_refs)

    # --- per cut: panels, cards, the cut records ----------------------------
    for cut in manifest.cuts:
        episode_id = episode_ids[cut.audio.path]
        audio_asset = episode_assets[cut.audio.path]
        track_id = str(_mint(manifest.production, "track", cut.label))

        panel_ids: list[uuid.UUID] = []
        for panel in sorted(cut.panels, key=lambda p: p.order):
            beat = _beat_id(panel.beat_id)
            if beat != panel.beat_id:
                report.beat_ids_renumbered += 1
            pid = _mint(manifest.production, "panel", cut.label, panel.order)
            panel_ids.append(pid)
            _upsert(
                project,
                Annotation(
                    id=pid,
                    tier=TIER_VIDEO_PANEL,
                    reference=_media_ref(audio_asset, panel.start, panel.end),
                    body=VideoPanelBodyV1(
                        still_id=str(still_ids[panel.still_key]),
                        # rule 1 — reality, not the never-realised intent
                        move=_panel_move(manifest, panel.move),
                        zoom=panel.zoom,
                        focus=panel.focus,
                        path=None,
                        # kept as provenance of the source ordinal; nothing
                        # reads it, because nothing is 'auto'
                        seed=panel.seed,
                        beat_id=beat,
                        order=panel.order,
                        track_id=track_id,
                        footage=(
                            {
                                **footage_refs[panel.footage.key],
                                "in_s": panel.footage.in_s,
                            }
                            if panel.footage
                            else None
                        ),
                    ).model_dump(mode="json"),
                    body_schema_uri=VIDEO_PANEL_V1,
                    provenance=_provenance([episode_id]),
                ),
                index,
            )
        report.panels_by_cut[cut.label] = len(panel_ids)

        label_ids: list[uuid.UUID] = []
        for i, label in enumerate(sorted(cut.labels, key=lambda x: x.start)):
            lid = _mint(manifest.production, "label", cut.label, i)
            label_ids.append(lid)
            _upsert(
                project,
                Annotation(
                    id=lid,
                    tier=TIER_LABEL_TRACK,
                    reference=_media_ref(audio_asset, label.start, label.end),
                    body=LabelTrackBodyV1(
                        kind=label.kind,
                        lines=label.lines,
                        headline=label.headline,
                        weight=label.weight,
                        slot=label.slot,
                    ).model_dump(mode="json"),
                    body_schema_uri=LABEL_TRACK_V1,
                    provenance=_provenance([episode_id]),
                ),
                index,
            )
        report.labels_by_cut[cut.label] = len(label_ids)

        still_parents = sorted({still_ids[p.still_key] for p in cut.panels}, key=str)
        settings = _cut_settings(manifest, cut)

        motion_id = None
        if cut.motion_artifact:
            motion_file = src / cut.motion_artifact
            if motion_file.is_file():
                motion_id = _mint(manifest.production, "cut", cut.label, "motion")
                motion_file = _materialize_cut(
                    motion_file,
                    root,
                    f"{motion_id}_motion.mp4",
                    stage="motion",
                    mode=materialize_cuts,
                    report=report,
                )
                motion_art = _artifact_for(
                    motion_file,
                    kind="video",
                    mime="video/mp4",
                    duration_s=cut.duration_s,
                )
                if _is_inside(motion_file, root):
                    _register(
                        motion_file,
                        motion_art.asset_id,
                        kind="video",
                        width=cut.width,
                        height=cut.height,
                        duration_s=cut.duration_s,
                    )
                _upsert(
                    project,
                    Annotation(
                        id=motion_id,
                        tier=TIER_VIDEO_CUT,
                        reference=_node_ref(TIER_VIDEO_CUT, motion_id),
                        body=VideoCutBodyV1(
                            label=cut.label,
                            stage="motion",
                            profile=cut.profile,
                            audio_artifact_id=audio_asset,
                            panel_ids=tuple(str(p) for p in panel_ids),
                            # None: braidio did not render this, so there is no
                            # key that describes its pixels, and a cache lookup
                            # must never answer with it.
                            cache_key=None,
                            artifact_id=motion_art.asset_id,
                            url=file_url(motion_file),
                            duration_s=cut.duration_s,
                            width=cut.width,
                            height=cut.height,
                            fps=cut.fps,
                            settings=settings,
                        ).model_dump(mode="json"),
                        body_schema_uri=VIDEO_CUT_V1,
                        provenance=_provenance(
                            [*panel_ids, *still_parents, episode_id]
                        ),
                    ),
                    index,
                )
                report.cuts_written.append(f"{cut.label} (motion)")

        delivered_id = _mint(manifest.production, "cut", cut.label, "delivered")
        delivered_file = src / cut.artifact
        captions_id = None
        if cut.captions and (src / cut.captions).is_file():
            captions_file = src / cut.captions
            # The sidecar rides with its film. Bringing the .srt in while
            # leaving the mp4 outside the project is the one combination that
            # helps nobody — captions for a video no surface can serve.
            if copy_media and materialize_cuts != "none":
                captions_file = _place_into(
                    captions_file,
                    root / "data" / "cuts",
                    f"{delivered_id}.srt",
                    report,
                )
            captions_art = _artifact_for(
                captions_file, kind="text", mime="application/x-subrip"
            )
            captions_id = captions_art.asset_id
            # Reported unregistered rather than coerced: the catalog's kinds
            # are image/video/audio/json, and an .srt is none of them.
            _register(captions_file, captions_id, kind="text")
        delivered_art = None
        if delivered_file.is_file():
            delivered_file = _materialize_cut(
                delivered_file,
                root,
                f"{delivered_id}.mp4",
                stage="delivered",
                mode=materialize_cuts,
                report=report,
            )
            delivered_art = _artifact_for(
                delivered_file,
                kind="video",
                mime="video/mp4",
                duration_s=cut.duration_s,
            )
            if _is_inside(delivered_file, root):
                _register(
                    delivered_file,
                    delivered_art.asset_id,
                    kind="video",
                    width=cut.width,
                    height=cut.height,
                    duration_s=cut.duration_s,
                )
        parents = [
            *([motion_id] if motion_id else []),
            *label_ids,
            *panel_ids,
            *still_parents,
            episode_id,
        ]
        _upsert(
            project,
            Annotation(
                id=delivered_id,
                tier=TIER_VIDEO_CUT,
                reference=_node_ref(TIER_VIDEO_CUT, delivered_id),
                body=VideoCutBodyV1(
                    label=cut.label,
                    stage="delivered",
                    profile=cut.profile,
                    audio_artifact_id=audio_asset,
                    panel_ids=tuple(str(p) for p in panel_ids),
                    cache_key=None,
                    artifact_id=delivered_art.asset_id if delivered_art else None,
                    url=file_url(delivered_file) if delivered_art else None,
                    duration_s=cut.duration_s,
                    width=cut.width,
                    height=cut.height,
                    fps=cut.fps,
                    captions_artifact_id=captions_id,
                    settings=settings,
                    published=cut.published,
                ).model_dump(mode="json"),
                body_schema_uri=VIDEO_CUT_V1,
                provenance=_provenance(parents),
            ),
            index,
        )
        report.cuts_written.append(f"{cut.label} (delivered)")
        if cut.published and cut.published.get("url"):
            report.published_links[cut.label] = str(cut.published["url"])

    return report


def _place_into(src: Path, folder: Path, name: str, report: ImportReport) -> Path:
    """Copy ``src`` to ``folder/name`` — the project takes its own bytes.

    A project has to be **self-contained**: a body pointing at a path outside
    it is a reference the server it gets rsynced to cannot resolve, which is
    how three finished films arrived somewhere they could be read and not
    watched. So the bytes come in.

    They come in by **copy, not by hardlink**, and that is a decision with a
    measured reason. A same-volume link would make the project free, and the
    blob store below does exactly that — but the blob store links *within* the
    project, where the filename IS the digest, so a shared inode can only ever
    be rewritten with identical bytes. That argument does not transfer here.
    These sources live in shared working trees (one is a git checkout, all
    three are inside a syncing folder), and a link makes the source, the
    project's media and the blob **one inode**: an in-place rewrite upstream
    silently rewrites the project's copy *and* the blob, under a digest name
    that no longer describes its bytes, and content-addressing quietly stops
    holding. A rename-based writer (git checkout, an atomic save) breaks the
    link harmlessly; a truncating one does not, and nothing here can tell
    which a given tool is.

    So the copy is the price of the project owning its bytes. The saving that
    matters is kept: the blob store hardlinks from this copy, so registering
    an artifact still costs nothing.
    """
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    if _same_content(src, dest):
        return dest
    # UNLINK FIRST — this line is load-bearing, not tidy. Once an artifact has
    # been registered, ``blobs/<sha256>`` is a hardlink to this very inode, and
    # ``copy2`` opens its destination for writing rather than replacing it. So
    # copying over an existing file writes the new bytes THROUGH the blob: the
    # blob's contents change under a digest name that no longer describes them,
    # content-addressing quietly stops holding, and the host answers 200 with
    # the wrong picture under the old id. Breaking the link first means the
    # copy lands on a fresh inode and the old blob keeps the bytes it is named
    # for. The case is the ordinary one — a better scan of a still arrives and
    # the production is re-imported.
    dest.unlink(missing_ok=True)
    shutil.copy2(src, dest)
    report.media_copied += 1
    report.bytes_copied += dest.stat().st_size
    return dest


def _same_content(src: Path, dest: Path) -> bool:
    """Is ``dest`` already this exact file? — size AND modification time.

    Size alone is not enough, and the difference is not theoretical: a still
    replaced by a genuinely different image of the same byte length would be
    skipped, the project would keep the old bytes, and the report would say
    ``stills_written=0`` — so "a re-import is a no-op" would be unfalsifiable,
    unable to distinguish "nothing changed" from "the change was invisible to
    the check". ``copy2`` preserves mtime, so comparing both is exact for
    anything this function itself wrote and is rsync's own heuristic besides.
    """
    if not dest.exists():
        return False
    a, b = src.stat(), dest.stat()
    return a.st_size == b.st_size and int(a.st_mtime) == int(b.st_mtime)


def _is_inside(path: Path, root: Path) -> bool:
    """Is ``path`` under ``root``? — i.e. did the project actually take it in.

    >>> _is_inside(Path("/a/b/c.mp4"), Path("/a"))
    True
    >>> _is_inside(Path("/x/c.mp4"), Path("/a"))
    False
    """
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return False
    return True


def _materialize_cut(
    src_file: Path,
    root: Path,
    name: str,
    *,
    stage: str,
    mode: str,
    report: ImportReport,
) -> Path:
    """Bring a rendered cut into the project — or, on ``"none"``, say it is not.

    Named ``{annotation id}.mp4`` / ``{annotation id}_motion.mp4``, which is
    exactly what ``video_cut.render`` / ``.finish`` write into ``data/cuts``.
    That is not cosmetic: ``braidio.downloads`` lists a project's deliverables
    by scanning that directory and reads the stage off the file name, so an
    imported cut appears there on the same terms as a rendered one, and the
    fix lands on two doors instead of one.

    The mp4s are the largest thing an import moves (about 1.1 GB across the
    three productions, delivered-only), so a same-volume hardlink is what
    makes this affordable — see :func:`_place_into`.
    """
    wanted = mode == "all" or (mode == "delivered" and stage == "delivered")
    if not wanted:
        report.catalog.unregistered.append(
            (
                str(src_file),
                f"materialize_cuts={mode!r} leaves the {stage} cut where it is, "
                "so no surface can serve it and the path will not exist on "
                "another machine",
            )
        )
        return src_file
    return _place_into(src_file, root / "data" / "cuts", name, report)


def _utc_now_iso() -> str:
    """``'2026-09-20T12:34:56Z'`` — the catalog's ``generated_at`` spelling."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _beats_fingerprint(beats) -> str:
    """Everything about a cut's beats that two cuts sharing a mix must agree on.

    Deliberately the CONTENT, not the ids: an id is a pure function of
    (production, role, mix, index), so two cuts with the same indices mint the
    same ids no matter how different their words are. Comparing ids answers a
    question nobody asked.
    """
    return json.dumps(
        [b.model_dump(mode="json") for b in sorted(beats, key=lambda x: x.index)],
        sort_keys=True,
    )


def _breakdown_from_beats(beats, *, title: str) -> dict:
    """A ``TimelineBreakdown.to_dict()`` built from a cut's normalized beats.

    ``settings`` is **null** rather than invented: an imported production's
    render settings are usually not on disk, and a fabricated settings block
    is exactly the kind of plausible record that makes a later reader trust a
    number nobody measured. ``from_dict`` recomputes ``totals`` and
    ``duration`` from the beats, so only the beats have to be right.
    """
    rows = [
        {
            "index": b.index,
            "kind": b.kind or "narration",
            "label": b.label or "",
            "source": list(b.source) if b.source else None,
            "duration": round(float(b.end) - float(b.start), 3),
            "start": round(float(b.start), 3),
            "end": round(float(b.end), 3),
        }
        for b in sorted(beats, key=lambda x: x.index)
    ]
    totals: dict[str, float] = {}
    for r in rows:
        totals[r["kind"]] = round(totals.get(r["kind"], 0.0) + r["duration"], 3)
    return {
        "title": title,
        "duration": round(max((r["end"] for r in rows), default=0.0), 3),
        "totals": totals,
        "settings": None,
        "beats": rows,
    }


def _timeline_for(manifest: ProductionManifest, audio_rel: str):
    """The ``TimelineBreakdown`` for the cut rendered against ``audio_rel``.

    **An episode node with members must always carry one.** This is not a
    preference: ``braidio.transforms.episode_timeline`` reads a persisted
    breakdown when there is one and otherwise *reconstructs*, and the
    reconstruction needs a ``weave-config`` node that an imported project does
    not have — so it does not degrade, it **raises**, taking panel planning
    (``video_panels.plan``) and cut rendering (``video_cut.render``) with it
    for that whole production. A manifest that recorded no timeline of its own
    therefore gets one built from its beats, which are the same facts in the
    same shape.
    """
    for cut in manifest.cuts:
        if cut.audio.path != audio_rel:
            continue
        if cut.timeline:
            return dict(cut.timeline)
        if cut.beats:
            return _breakdown_from_beats(cut.beats, title=manifest.title)
    return None


def _import_beats(
    project,
    index: dict,
    manifest: ProductionManifest,
    cut: CutRecord,
    *,
    root: Path,
    copy_media: bool,
    take_paths: dict[tuple[str, int], Path],
    report: ImportReport,
    register,
) -> tuple[str, ...]:
    """Write one cut's beats and takes; return its episode's member ids.

    This is what makes a narration segment a thing at all. Before it, the only
    audio-side node an imported production had was the finished mix, so "which
    part of this is the narration, and can I swap it" had no referent — the
    studio was right to decline to draw a button for it.

    One node per timeline member, because ``ordered_member_ids`` is the
    episode's **play order** and a list missing a member is not one:

    - **narration** (including a beat whose timeline ``kind`` is really a
      delivery style) → a ``narrative-beat/v1`` carrying the authored text,
      plus a ``narration-render/v1`` carrying the take. The render derives from
      the beat, so editing the words stales the recording — which is the
      relationship the whole feature rests on.
    - **clip / archive-with-a-source** → a ``segment-extraction/v1``. No
      ``audio-clip/v1`` parent is invented: that would need a source-media node
      naming a recording this import cannot witness, and a fabricated parent is
      worse than an honest root.
    - **scene break** → the ``scene-break/v1`` authoring node, which *is* the
      member (a break has no render node — the boundary is the decision).

    Ids are minted from ``(production, cut label, index)``, so a re-import
    addresses the same nodes and a no-op re-import writes nothing.
    """
    from braidio.bodies._domain import (
        NARRATIVE_BEAT_V1,
        SCENE_BREAK_V1,
        NarrativeBeatBodyV1,
        SceneBreakBodyV1,
    )
    from braidio.bodies._render_nodes import (
        NARRATION_RENDER_V1,
        NARRATION_SOURCE_TTS,
        SEGMENT_EXTRACTION_V1,
        NarrationRenderBodyV1,
        SegmentExtractionBodyV1,
    )
    from braidio.transforms._common import (
        TIER_NARRATION_RENDER,
        TIER_NARRATIVE_BEAT,
        TIER_SCENE_BREAK,
        TIER_SEGMENT_EXTRACTION,
        beat_id as beat_identity,
        file_url,
    )
    from lacing import Annotation

    members: list[str] = []
    counts = {"narration": 0, "clip": 0, "break": 0}
    takes = 0

    for beat in sorted(cut.beats, key=lambda b: b.index):
        role = _beat_role(beat)
        counts[role] += 1
        ident = beat_identity(beat.index)

        if role == "break":
            bid = _mint(manifest.production, "break", cut.audio.path, beat.index)
            _upsert(
                project,
                Annotation(
                    id=bid,
                    tier=TIER_SCENE_BREAK,
                    reference=_node_ref(TIER_SCENE_BREAK, bid),
                    body=SceneBreakBodyV1(
                        beat_id=ident, label=beat.label or "", marker=beat.marker
                    ).model_dump(mode="json"),
                    body_schema_uri=SCENE_BREAK_V1,
                    provenance=_provenance(),
                ),
                index,
            )
            members.append(str(bid))
            continue

        take_path = take_paths.get((cut.label, beat.index))
        dest = take_path
        artifact = None
        if take_path is not None:
            if copy_media:
                dest = _place_into(
                    take_path,
                    root / "data" / "tts",
                    _safe_name(f"{Path(cut.audio.path).stem}-beat{beat.index:03d}")
                    + take_path.suffix,
                    report,
                )
            duration = (
                beat.take.duration_s
                if beat.take is not None and beat.take.duration_s is not None
                else round(max(beat.end - beat.start, 0.0), 3)
            )
            artifact = _artifact_for(
                dest, kind="audio", mime="audio/mpeg", duration_s=duration
            )
            register(dest, artifact.asset_id, kind="audio", duration_s=duration)
        else:
            duration = round(max(beat.end - beat.start, 0.0), 3)

        if role == "clip":
            sid = _mint(manifest.production, "segment", cut.audio.path, beat.index)
            # No invented span. One production's driver never persisted where
            # in the master each clip was cut from, and (0.0, duration) reads
            # exactly like a real cut from the head of somebody else's
            # recording — a false claim about third-party material, which is
            # the worst place to make one.
            recorded = beat.source is not None
            start_s, end_s = beat.source if recorded else (0.0, 0.0)
            if not recorded:
                report.segments_without_source.append(f"{cut.label}/{beat.index}")
            _upsert(
                project,
                Annotation(
                    id=sid,
                    tier=TIER_SEGMENT_EXTRACTION,
                    reference=_node_ref(TIER_SEGMENT_EXTRACTION, sid),
                    body=SegmentExtractionBodyV1(
                        cache_key=_imported_cache_key(
                            manifest.production, cut.audio.path, beat.index
                        ),
                        start_s=float(start_s),
                        end_s=float(end_s),
                        source_span_recorded=recorded,
                        artifact_id=artifact.asset_id if artifact else None,
                        url=file_url(dest) if dest is not None else None,
                    ).model_dump(mode="json"),
                    body_schema_uri=SEGMENT_EXTRACTION_V1,
                    provenance=_provenance(),
                ),
                index,
            )
            members.append(str(sid))
            continue

        # --- narration: the beat, then the take that spoke it ---------------
        text = beat.text
        if not (text or "").strip():
            # NEVER the timeline's snippet — see the module docstring.
            text = ""
            report.beats_without_text.append(f"{cut.label}/{beat.index}")
        bid = _mint(manifest.production, "beat", cut.audio.path, beat.index)
        _upsert(
            project,
            Annotation(
                id=bid,
                tier=TIER_NARRATIVE_BEAT,
                reference=_node_ref(TIER_NARRATIVE_BEAT, bid),
                body=NarrativeBeatBodyV1(
                    beat_id=ident, text=text, style=_beat_style(beat)
                ).model_dump(mode="json"),
                body_schema_uri=NARRATIVE_BEAT_V1,
                provenance=_provenance(),
            ),
            index,
        )
        tid = _mint(manifest.production, "take", cut.audio.path, beat.index)
        _upsert(
            project,
            Annotation(
                id=tid,
                tier=TIER_NARRATION_RENDER,
                reference=_node_ref(TIER_NARRATION_RENDER, tid),
                body=NarrationRenderBodyV1(
                    cache_key=_imported_cache_key(
                        manifest.production, cut.audio.path, beat.index
                    ),
                    artifact_id=artifact.asset_id if artifact else None,
                    url=file_url(dest) if dest is not None else None,
                    duration_s=float(duration),
                    # Imported takes are synthesized. A person's own recording
                    # becomes 'upload' when they replace one; claiming that
                    # here would erase the only distinction between them.
                    source=NARRATION_SOURCE_TTS,
                ).model_dump(mode="json"),
                body_schema_uri=NARRATION_RENDER_V1,
                # The take derives from the beat: edit the words, and the
                # recording reads stale.
                provenance=_provenance([bid]),
            ),
            index,
        )
        members.append(str(tid))
        if artifact is not None:
            takes += 1

    if cut.beats:
        report.beats_by_cut[cut.label] = len(members)
        report.takes_by_cut[cut.label] = takes
    return tuple(members)


def _common_profile(manifest: ProductionManifest) -> str:
    """The profile every cut agrees on (the episode's rights projection)."""
    profiles = {c.profile for c in manifest.cuts} or {"personal"}
    if len(profiles) > 1:
        raise ImportError_(
            f"{manifest.production}: cuts declare more than one profile "
            f"({sorted(profiles)}); an episode has one rights projection."
        )
    return next(iter(profiles))


def _cut_settings(manifest: ProductionManifest, cut: CutRecord) -> dict[str, Any]:
    """The cut's recorded settings, plus what a surface needs beside it.

    ``size`` and ``fps`` are what ``video_cut.render`` reads, so they are
    normalized here rather than left to whatever the source happened to spell.
    ``rights`` rides along because plan §10 asks for the position to be shown
    *next to the cut*, and ``import`` records that these panels' motion is a
    recovered fact rather than an authored choice.
    """
    settings = dict(cut.settings)
    if cut.width and cut.height:
        settings["size"] = [int(cut.width), int(cut.height)]
    settings.setdefault("size", list(settings.get("size") or ()))
    if cut.fps:
        settings["fps"] = int(cut.fps)
    settings["rights"] = {
        "position": manifest.rights.position,
        "why": manifest.rights.why,
        "measured": manifest.rights.measured,
    }
    # key order kept as it was, so a re-import of a "source" production writes
    # the same bytes and stales nothing
    settings["import"] = {
        "source": manifest.source_dir,
        **({"move_note": MOVE_IMPORT_NOTE} if manifest.moves == "source" else {}),
        "audio": cut.audio.model_dump(mode="json", exclude_none=True),
    }
    return settings


def _project_notes(manifest: ProductionManifest, report: ImportReport) -> str:
    """The project-level rights position and import caveats, as readable text."""
    lines = [
        f"{manifest.title} — imported from {manifest.source_dir}.",
        "",
        f"RIGHTS: {manifest.rights.position}.",
        manifest.rights.why,
    ]
    if manifest.rights.measured:
        lines += ["", f"Measured: {manifest.rights.measured}"]
    if manifest.moves == "source":
        lines += ["", "IMPORT NOTE:", MOVE_IMPORT_NOTE]
    if manifest.gaps:
        lines += ["", "WHAT THE ARCHAEOLOGY COULD NOT SETTLE:"]
        lines += [f"- {g}" for g in manifest.gaps]
    return "\n".join(lines)
