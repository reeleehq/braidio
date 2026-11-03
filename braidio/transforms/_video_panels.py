"""``video_panels.plan`` — an episode + its stills → the panel track.

A **free, batch** Transform (commentary-studio plan §5): one ``episode-render``
plus the project's ``still`` nodes in, one ``video-panel/v1`` annotation per
span out. Spans come from :func:`braidio.video.plan_spans` over the episode's
**persisted** timeline (reconstructed for rows that predate it, see
:func:`braidio.transforms.episode_timeline`), so the cuts land where the
narration changes subject and survive a reopen. Each panel is pinned to its
span through a :class:`lacing.MediaRef` on the episode's audio, which is what
makes "which panel covers *t*?" an interval query rather than a scan.

Assignment takes an explicit **pick map** — ``params["picks"]``, ``{beat_id:
[still_key, ...]}``, the shape the finished productions already used — and
falls back to cycling the pool without a back-to-back repeat (the
:func:`braidio.video.assign_stills` rule) for any beat it does not name. A key
the pool does not hold raises at plan time: a silent fallback would ship a
picture nobody chose.

**The seed is minted from the beat, not the ordinal.** ``mint_seed(beat_id,
k)`` hashes the beat's id and the panel's index *within that beat*, so a
panel's camera move survives a reorder, a re-weave and a still swap — "keep
this move, change this picture" is the first thing the owner asked for, and
``i % 4`` could not express it.

**A panel derives from the episode, not from its still.** ``still_id`` is an
authored pointer — the same shape as ``audio-clip.source_node_id`` — and the
panel's *value* never reads the still, so a provenance edge to it would make
every label edit on a still stale the panel (and, through it, every cut)
with nothing to re-derive. The **cuts** derive from the stills: a still's
label edit stales the cuts, and the motion pass answers from its cache while
the text pass re-runs — which is the split earning itself. Swapping the
picture is a patch to ``still_id`` (the panel's own digest moves); replacing
the bytes is a new still with a new artifact id, then that patch.

**A track is an identity.** Every panel a plan writes carries the same
``track_id``, minted once per plan run, so two plans over one episode stay
apart: :func:`tracks_for_episode` groups them and :func:`panels_for_episode`
returns the latest. ``execute`` is idempotent by *authored value* — when the
latest track already places the same stills over the same spans, it is
returned and nothing is written, **even if its moves have since been
edited** (the edit is the point of the track); a plan that says something
else (different picks, different bounds) writes a new track beside it,
never over it. ``force=True``
writes a new track even when the value is the same. After a re-weave the new
episode has no track yet, so the planner plans anew; carry choices forward
with :func:`picks_from_panels` over the old track.

**Every placement says why, and how well it fits** (thorwhalen/braidio#85).
Each panel records the words spoken under it (``anchor_text``), the author's
reason (``rationale``, from a ``{still_key, reason}`` pick), a ``relevance``
score from a pluggable scorer (``params["relevance"]``, see
:mod:`braidio.relevance`) and a ``role`` — ``literal`` / ``contextual`` /
``decorative``, or ``None`` when nobody said. The knobs:

- ``min_relevance`` (default :data:`DEFAULT_MIN_RELEVANCE`): a placement
  scoring below it is *decorative*, whatever it claimed;
- ``allow_decorative`` (default ``True``): ``False`` refuses any decorative
  placement, a float caps decorative screen time at that share;
- ``allow_disclaimed`` (default ``False``): whether a pick may be marked
  ``disclaimed`` — the "right-shaped, wrong-specific, rescued by its label"
  placement. Off, such a pick refuses; on, the cut shows its label for the
  whole panel and no card in the label's slot may hide it;
- ``pick_scope`` (default ``"beat"``): ``"span"`` chooses, for each span,
  the most relevant still among the beat's picks (or the pool) for the words
  under *that span*, instead of taking the beat's list positionally.

The defaults keep a re-plan of an existing track placing the same stills
over the same spans: nothing is refused unless a caller asks for it, and the
new fields only describe the placement. The flip side: because the
assessment fields (``anchor_text``, ``relevance``, ``scorer``, ``role``) are
not part of a track's identity, a re-plan whose only change is a pick's
``role`` — or a first scoring of a track planned before these fields
existed — returns the existing track; pass ``force=True`` to write the
assessed track. :func:`placement_report` lists what
a reviewer should look at.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Mapping, Optional

from falaw import Plan
from lacing import Annotation, MediaRef, TimeInterval
from nw import BaseTransform, TransformInputs, TransformResult, register_transform
from nw.transforms._provenance import derive_provenance

from braidio.bodies._render_nodes import EPISODE_RENDER_V1
from braidio.bodies._video import (
    DEFAULT_MOVE,
    DEFAULT_ZOOM,
    PANEL_ROLES,
    STILL_V1,
    VIDEO_PANEL_V1,
    VideoPanelBodyV1,
)
from braidio.transforms._common import (
    TIER_EPISODE_RENDER,
    TIER_STILL,
    TIER_VIDEO_PANEL,
    annotation_parents,
    graph_index,
    resolve_parents,
)
from braidio.transforms._episode import episode_timeline
from braidio.relevance import resolve_scorer, scorer_id
from braidio.video import MAX_PANEL_S, MIN_PANEL_S, plan_spans

NAME = "video_panels.plan"

#: Rate of the panel intervals on the episode audio (milliseconds).
_RATE = 1000
#: Seeds are 31-bit so they survive every JSON reader and burns' int index.
_SEED_BITS = 31

#: Below this relevance a placement is recorded as ``decorative``. Tuned to the
#: default lexical scorer: under it, a still whose naming words the narration
#: does not say at all scores 0.0, and one word of a three-word subject 0.33.
DEFAULT_MIN_RELEVANCE = 0.1
PICK_SCOPES: tuple[str, ...] = ("beat", "span")
DEFAULT_PICK_SCOPE = "beat"
#: A cue counts as spoken under a span when it overlaps it by this much (or by
#: half the cue, if shorter) — the weave's crossfades make edges ragged.
_MIN_CUE_OVERLAP_S = 0.25


@dataclass(frozen=True)
class Pick:
    """One authored choice: a still, and optionally why and what it claims.

    ``params["picks"]`` entries may be a bare still key (the old shape) or a
    mapping with these fields. A bare key is an *unexplained* pick.

    >>> as_pick("dylan-1965")
    Pick(still_key='dylan-1965', reason=None, role=None, disclaimed=False)
    >>> as_pick({"still_key": "k", "reason": "names Dylan", "role": "literal"}).role
    'literal'
    """

    still_key: str
    reason: Optional[str] = None
    role: Optional[str] = None
    disclaimed: bool = False


def as_pick(entry) -> Pick:
    """Normalize one pick-map entry (a key, a mapping, or a :class:`Pick`)."""
    if isinstance(entry, Pick):
        pick = entry
    elif isinstance(entry, str):
        pick = Pick(entry)
    elif isinstance(entry, Mapping):
        unknown = set(entry) - set(Pick.__dataclass_fields__)
        if unknown or "still_key" not in entry:
            raise ValueError(
                f"{NAME}: a pick is a still key or a mapping with 'still_key' "
                f"(and optionally 'reason', 'role', 'disclaimed'); got {dict(entry)!r}"
            )
        pick = Pick(
            still_key=str(entry["still_key"]),
            reason=(None if entry.get("reason") is None else str(entry["reason"])),
            role=entry.get("role"),
            disclaimed=as_flag(entry.get("disclaimed", False), name="disclaimed"),
        )
    else:
        raise ValueError(f"{NAME}: cannot read a pick from {entry!r}")
    if pick.role is not None and pick.role not in PANEL_ROLES:
        raise ValueError(
            f"{NAME}: pick {pick.still_key!r} has role {pick.role!r}, "
            f"not one of {PANEL_ROLES}"
        )
    return pick


def as_flag(value, *, name: str) -> bool:
    """A boolean knob, read strictly: JSON callers send ``"false"`` too.

    >>> as_flag("false", name="x"), as_flag(True, name="x"), as_flag(0, name="x")
    (False, True, False)
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off", "", "none"):
        return False
    raise ValueError(f"{NAME}: {name} must be a boolean, got {value!r}")


def _min_relevance(value) -> float:
    """``min_relevance`` in ``[0, 1]`` (``None`` = the default)."""
    threshold = DEFAULT_MIN_RELEVANCE if value is None else float(value)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"{NAME}: min_relevance must lie in [0, 1], got {value!r}")
    return threshold


def _decorative_share(value) -> float:
    """``allow_decorative`` as the largest admissible share of screen time.

    >>> _decorative_share(True), _decorative_share(False), _decorative_share(0.25)
    (1.0, 0.0, 0.25)
    """
    if isinstance(value, bool) or (
        isinstance(value, str) and not value.strip().replace(".", "", 1).isdigit()
    ):
        return 1.0 if as_flag(value, name="allow_decorative") else 0.0
    share = float(value)
    if not 0.0 <= share <= 1.0:
        raise ValueError(
            f"{NAME}: allow_decorative must be a bool or a share in [0, 1], got {value!r}"
        )
    return share


def span_interval(start_s: float, end_s: float) -> TimeInterval:
    """A span's interval on the episode audio, quantized to whole milliseconds.

    lacing's time is rational and refuses a lossy float; a span edge from
    ``plan_spans`` is a float (a beat divided in three lands on 22.88…), so it
    is rounded to the tick here, once, and consecutive spans stay gapless
    because they round the same shared edge the same way. The rounding is
    lacing's (``TimeInterval.from_seconds_lossy``), the one rule every braidio
    float window uses (braidio#184, #189).

    >>> iv = span_interval(0.0, 22.8866)
    >>> (iv.start.value, iv.end.value, iv.end.rate)
    (0, 22887, 1000)
    """
    return TimeInterval.from_seconds_lossy(start_s, end_s, rate=_RATE)


def mint_seed(beat_id: str | None, k: int) -> int:
    """A panel's motion seed: a stable hash of its beat and its index in it.

    >>> mint_seed("0003", 0) == mint_seed("0003", 0)
    True
    >>> mint_seed("0003", 0) != mint_seed("0003", 1)
    True
    """
    digest = hashlib.sha256(f"{beat_id or ''}/{k}".encode()).digest()
    return int.from_bytes(digest[:4], "big") & ((1 << _SEED_BITS) - 1)


def picks_from_panels(panels, index: dict, *, with_reasons: bool = False) -> dict:
    """``{beat_id: [still_key, ...]}`` recovered from an existing track.

    The pick map to hand the planner after a re-weave, so the new track keeps
    the pictures the old one chose per beat. With ``with_reasons=True`` each
    entry is a pick mapping (``still_key``, ``reason``, ``role``,
    ``disclaimed``) so the reasons travel too. Either way the planner
    **re-scores** every carried pick against the narration it now sits under,
    which is how a pick carried into a shorter cut that dropped its sentence
    shows up as decorative instead of passing silently.
    """
    picks: dict[str, list] = {}
    for panel in sorted(panels, key=lambda p: int(p.body.get("order", 0))):
        beat_id = panel.body.get("beat_id")
        still = index.get(uuid.UUID(str(panel.body["still_id"])))
        if beat_id is None or still is None:
            continue
        key = str(still.body["key"])
        entry = (
            {
                "still_key": key,
                "reason": panel.body.get("rationale"),
                # "decorative" may be the plan's verdict rather than the
                # author's claim, and carried forward it would stick even
                # where the new narration names the still; the planner
                # re-derives it from the score instead.
                "role": (
                    None
                    if panel.body.get("role") == "decorative"
                    else panel.body.get("role")
                ),
                "disclaimed": bool(panel.body.get("disclaimed", False)),
            }
            if with_reasons
            else key
        )
        picks.setdefault(str(beat_id), []).append(entry)
    return picks


#: The findings :func:`placement_report` can raise about a panel.
REPORT_ISSUES: tuple[str, ...] = ("unexplained", "decorative", "disclaimed", "unscored")


def placement_report(panels) -> list[dict]:
    """One row per panel a reviewer should look at, in track order.

    A panel is listed when it is ``unexplained`` (no role — nobody said what
    it claims), ``decorative`` (admitted filler, or scored below threshold),
    ``disclaimed`` (honest only while its label shows) or ``unscored`` (a
    track planned before scoring existed). Accepts annotations or bodies.

    >>> placement_report([{"order": 0, "still_id": "s", "role": "literal",
    ...                    "relevance": 0.8}])
    []
    >>> [r["issues"] for r in placement_report([{"order": 1, "still_id": "s"}])]
    [['unexplained', 'unscored']]
    """
    rows = []
    for p in panels:
        body = getattr(p, "body", p)
        issues = []
        if body.get("role") is None:
            issues.append("unexplained")
        elif body.get("role") == "decorative":
            issues.append("decorative")
        if body.get("disclaimed"):
            issues.append("disclaimed")
        if body.get("relevance") is None:
            issues.append("unscored")
        if issues:
            rows.append(
                {
                    "order": body.get("order"),
                    "beat_id": body.get("beat_id"),
                    "still_id": body.get("still_id"),
                    "role": body.get("role"),
                    "relevance": body.get("relevance"),
                    "rationale": body.get("rationale"),
                    "anchor_text": body.get("anchor_text"),
                    "issues": issues,
                }
            )
    return sorted(rows, key=lambda r: int(r["order"] or 0))


def tracks_for_episode(project, episode_id) -> dict[str, list[Annotation]]:
    """``{track_id: panels in order}`` for every track planned over ``episode_id``,
    oldest track first."""
    import nw

    by_track: dict[str, list[Annotation]] = {}
    for a in nw.annotations_at_tier(project.root, TIER_VIDEO_PANEL):
        if episode_id in a.provenance.was_derived_from:
            by_track.setdefault(str(a.body.get("track_id", "")), []).append(a)
    ordered = sorted(
        by_track.items(),
        key=lambda kv: min(p.provenance.generated_at_time.to_seconds() for p in kv[1]),
    )
    return {
        tid: sorted(panels, key=lambda a: int(a.body.get("order", 0)))
        for tid, panels in ordered
    }


def panels_for_episode(
    project, episode_id, *, track_id: str | None = None
) -> list[Annotation]:
    """One panel track over ``episode_id``, in ``order``: ``track_id``'s, or
    the latest planned when none is named. ``[]`` when there is none."""
    tracks = tracks_for_episode(project, episode_id)
    if track_id is not None:
        return list(tracks.get(track_id, []))
    return list(next(reversed(tracks.values()), []))


#: The fields a plan AUTHORS on a panel. Everything else (move, zoom, focus,
#: path) is what a person edits afterwards, and an edit must not turn the next
#: re-plan into a new track that hides it behind "the latest".
#: ``rationale`` and ``disclaimed`` are authored too (a pick carries them);
#: ``anchor_text`` / ``relevance`` / ``scorer`` / ``role`` are the plan's
#: *assessment* of the placement, so a new scorer does not fork the track.
#: Each maps to the value a panel written before it existed reads as.
_AUTHORED_FIELDS = {
    "still_id": None,
    "beat_id": None,
    "order": None,
    "rationale": None,
    "disclaimed": False,
}


def _track_value(panels) -> list:
    """What a track *says* as planned — per panel its span and its authored
    fields — the value two plans are compared on. A track whose motion has
    since been edited still compares equal to the plan that made it, and is
    returned rather than superseded."""
    out = []
    for p in panels:
        iv = p.reference.interval
        authored = {k: p.body.get(k, d) for k, d in _AUTHORED_FIELDS.items()}
        out.append((iv.start.value, iv.end.value, iv.end.rate, _json_value(authored)))
    return out


def _json_value(body: dict) -> str:
    import json

    return json.dumps(body, sort_keys=True, default=str)


def _beat_ids(episode: Annotation, index: dict) -> list[str | None]:
    """The ``beat_id`` behind each timeline beat, in member order."""
    out: list[str | None] = []
    for sid in episode.body.get("ordered_member_ids", ()):
        member = index.get(uuid.UUID(sid))
        if member is None:
            out.append(None)
            continue
        own = member.body.get("beat_id")
        if own is None:
            own = next(
                (
                    p.body.get("beat_id")
                    for p in resolve_parents(member, index)
                    if p.body.get("beat_id") is not None
                ),
                None,
            )
        out.append(None if own is None else str(own))
    return out


def _anchor_texts(project, episode: Annotation, timeline, spans) -> list[str]:
    """The words spoken under each span: the caption cues it covers.

    A span with no cue (a music clip — the graph does not know its words) falls
    back to the beat's timeline label, which is what a reviewer would read.
    """
    from braidio.captions import cues_for
    from braidio.transforms._episode import episode_script

    cues = cues_for(episode_script(project, episode), timeline)
    out = []
    for span in spans:
        said = [
            c.text
            for c in cues
            if min(c.end_s, span.end) - max(c.start_s, span.start)
            >= min(_MIN_CUE_OVERLAP_S, c.duration_s / 2)
            and c.duration_s > 0
        ]
        out.append(" ".join(said) or str(span.label or ""))
    return out


@dataclass(frozen=True)
class _Placement:
    still: Annotation
    pick: Optional[Pick]
    relevance: float


def _choose_stills(
    spans,
    beat_ids,
    stills,
    picks: dict,
    *,
    anchors: list[str],
    score,
    pick_scope: str = DEFAULT_PICK_SCOPE,
) -> list[_Placement]:
    """One still per span: the pick map where it names the beat, else the pool.

    ``pick_scope="beat"`` takes a beat's picks positionally (the k-th span of
    the beat gets the k-th pick, the last one repeating). ``"span"`` takes the
    most relevant candidate for that span's words, ties broken in rotation so
    an all-zero score still varies the pictures, and never the same still
    twice in a row when another candidate exists.
    """
    by_key = {str(s.body["key"]): s for s in stills}
    unknown = sorted(
        {p.still_key for ps in picks.values() for p in ps if p.still_key not in by_key}
    )
    if unknown:
        raise ValueError(
            f"{NAME}: picks name still keys the project does not hold: "
            f"{unknown}. Known keys: {sorted(by_key)}"
        )
    cache: dict[tuple[int, object], float] = {}

    def relevance(i: int, candidates: list[Annotation]) -> list[float]:
        todo = [c for c in candidates if (i, c.id) not in cache]
        if todo:
            got = list(score(anchors[i], [dict(c.body) for c in todo]))
            if len(got) != len(todo):
                raise ValueError(
                    f"{NAME}: the relevance scorer returned {len(got)} scores "
                    f"for {len(todo)} stills"
                )
            for c, v in zip(todo, got):
                cache[(i, c.id)] = min(1.0, max(0.0, float(v)))
        return [cache[(i, c.id)] for c in candidates]

    def best(i: int, options: list, stills_of, start: int, previous):
        """The most relevant option, ties in rotation from ``start``."""
        n = len(options)
        rotated = [options[(start + j) % n] for j in range(n)]
        if previous is not None and n > 1:
            rest = [o for o in rotated if stills_of(o).id != previous.id]
            rotated = rest or rotated
        scores = relevance(i, [stills_of(o) for o in rotated])
        top = max(range(len(rotated)), key=lambda j: (scores[j], -j))
        return rotated[top], scores[top]

    chosen: list[_Placement] = []
    within_beat: dict[str | None, int] = {}
    pool_cursor = 0
    for i, span in enumerate(spans):
        beat_id = beat_ids[span.beat_index] if span.beat_index < len(beat_ids) else None
        k = within_beat.get(beat_id, 0)
        within_beat[beat_id] = k + 1
        previous = chosen[-1].still if chosen else None
        beat_picks = picks.get(beat_id) if beat_id is not None else None
        if beat_picks:
            if pick_scope == "span":
                pick, rel = best(
                    i, beat_picks, lambda p: by_key[p.still_key], k, previous
                )
            else:
                pick = beat_picks[min(k, len(beat_picks) - 1)]
                rel = relevance(i, [by_key[pick.still_key]])[0]
            chosen.append(_Placement(by_key[pick.still_key], pick, rel))
            continue
        if pick_scope == "span":
            still, rel = best(i, stills, lambda s: s, pool_cursor, previous)
            pool_cursor += 1
            chosen.append(_Placement(still, None, rel))
            continue
        still = stills[pool_cursor % len(stills)]
        # no back-to-back repeat when the pool allows it
        if previous is not None and still.id == previous.id and len(stills) > 1:
            pool_cursor += 1
            still = stills[pool_cursor % len(stills)]
        pool_cursor += 1
        chosen.append(_Placement(still, None, relevance(i, [still])[0]))
    return chosen


def _judge(placements, spans, *, min_relevance, decorative_share, allow_disclaimed):
    """``[(role, disclaimed), ...]`` per placement, or a refusal naming them all.

    A disclaimed pick is admitted only under ``allow_disclaimed`` and only for
    a labelled still (the label is what makes it honest), and is exempt from
    the threshold. Anything else below ``min_relevance`` is decorative; the
    decorative screen time must fit ``decorative_share``.
    """
    verdicts, problems = [], []
    decorative_s = 0.0
    total_s = sum(sp.duration for sp in spans) or 1.0
    for n, (pl, span) in enumerate(zip(placements, spans)):
        pick = pl.pick
        role = pick.role if pick else None
        disclaimed = bool(pick and pick.disclaimed)
        key = pl.still.body.get("key")
        if disclaimed:
            if not allow_disclaimed:
                problems.append(
                    f"panel {n} ({key!r}) is a disclaimed pick; pass "
                    "allow_disclaimed=True to admit a picture that is honest "
                    "only while its label shows"
                )
            elif not pl.still.body.get("labelled"):
                problems.append(
                    f"panel {n} ({key!r}) is disclaimed but its still is "
                    "unlabelled — a disclaimer needs a label to show"
                )
        elif pl.relevance < min_relevance:
            role = "decorative"
        if role == "decorative":
            decorative_s += span.duration
        verdicts.append((role, disclaimed))
    if decorative_s / total_s > decorative_share + 1e-9:
        problems.append(
            f"decorative placements fill {decorative_s / total_s:.0%} of the "
            f"track, over allow_decorative={decorative_share:g} "
            f"(min_relevance={min_relevance:g}); give those spans a relevant "
            "still, a reasoned pick, or raise the allowance"
        )
    if problems:
        raise ValueError(f"{NAME}: " + "; ".join(problems))
    return verdicts


@register_transform(NAME)
class VideoPanelsPlan(BaseTransform):
    """``episode-render`` (+ the project's stills) → one ``video-panel/v1`` per span."""

    name = NAME
    input_kinds = (EPISODE_RENDER_V1, STILL_V1)
    output_kind = VIDEO_PANEL_V1
    is_batch = True

    def plan(
        self, project, inputs: TransformInputs, *, params=None
    ) -> tuple[Plan, tuple[Annotation, ...]]:
        import nw

        params = dict(params or {})
        episode = inputs.primary[0]
        if episode.tier != TIER_EPISODE_RENDER:
            raise ValueError(
                f"{NAME}: the first primary input must be an episode-render"
            )
        audio_id = episode.body.get("artifact_id")
        if not audio_id:
            raise ValueError(
                f"{NAME}: episode {episode.id} has no rendered audio yet — weave first"
            )
        stills = [a for a in inputs.primary[1:] if a.tier == TIER_STILL] or list(
            nw.annotations_at_tier(project.root, TIER_STILL)
        )
        if not stills:
            raise ValueError(f"{NAME}: the project holds no stills to plan panels from")
        stills = sorted(stills, key=lambda s: str(s.body.get("key", "")))

        index = graph_index(project)
        timeline = episode_timeline(project, episode)
        spans = plan_spans(
            timeline,
            min_panel_s=float(params.get("min_panel_s", MIN_PANEL_S)),
            max_panel_s=float(params.get("max_panel_s", MAX_PANEL_S)),
        )
        beat_ids = _beat_ids(episode, index)
        picks = {
            str(beat): [as_pick(e) for e in entries]
            for beat, entries in dict(params.get("picks") or {}).items()
        }
        pick_scope = str(params.get("pick_scope", DEFAULT_PICK_SCOPE))
        if pick_scope not in PICK_SCOPES:
            raise ValueError(
                f"{NAME}: pick_scope {pick_scope!r} is not one of {PICK_SCOPES}"
            )
        scorer_spec = params.get("relevance")
        anchors = _anchor_texts(project, episode, timeline, spans)
        chosen = _choose_stills(
            spans,
            beat_ids,
            stills,
            picks,
            anchors=anchors,
            score=resolve_scorer(scorer_spec),
            pick_scope=pick_scope,
        )
        verdicts = _judge(
            chosen,
            spans,
            min_relevance=_min_relevance(params.get("min_relevance")),
            decorative_share=_decorative_share(params.get("allow_decorative", True)),
            allow_disclaimed=as_flag(
                params.get("allow_disclaimed", False), name="allow_disclaimed"
            ),
        )
        scored_by = scorer_id(scorer_spec)
        move = str(params.get("move", DEFAULT_MOVE))
        zoom = float(params.get("zoom", DEFAULT_ZOOM))
        track_id = str(uuid.uuid4())

        skeletons = []
        within_beat: dict[str | None, int] = {}
        for order, (span, placement, (role, disclaimed), anchor) in enumerate(
            zip(spans, chosen, verdicts, anchors)
        ):
            still = placement.still
            beat_id = (
                beat_ids[span.beat_index] if span.beat_index < len(beat_ids) else None
            )
            k = within_beat.get(beat_id, 0)
            within_beat[beat_id] = k + 1
            full = TransformInputs(primary=(episode,))
            skeletons.append(
                Annotation(
                    id=uuid.uuid4(),
                    tier=TIER_VIDEO_PANEL,
                    reference=MediaRef(
                        asset_id=str(audio_id),
                        interval=span_interval(span.start, span.end),
                    ),
                    body=VideoPanelBodyV1(
                        still_id=str(still.id),
                        move=move,
                        zoom=zoom,
                        seed=mint_seed(beat_id, k),
                        beat_id=beat_id,
                        order=order,
                        track_id=track_id,
                        anchor_text=anchor or None,
                        rationale=placement.pick.reason if placement.pick else None,
                        relevance=placement.relevance,
                        scorer=scored_by,
                        role=role,
                        disclaimed=disclaimed,
                    ).model_dump(mode="json"),
                    body_schema_uri=VIDEO_PANEL_V1,
                    provenance=derive_provenance(
                        self, full, attributed_to="agent:braidio"
                    ),
                )
            )
        return Plan(calls=()), tuple(skeletons)

    def execute(
        self,
        project,
        plan: Plan,
        skeleton: tuple[Annotation, ...],
        *,
        use_cache: bool = True,
        force: bool = False,
    ) -> TransformResult:
        if not skeleton:
            return TransformResult(annotations=(), artifacts=(), cost_usd_actual=0.0)
        # The first ANNOTATION parent: nw orders annotation ids before any
        # declared artifact ids (nw#55), but relying on list position is how
        # this would quietly start reading an asset id as the episode.
        episode_id = annotation_parents(skeleton[0])[0]
        if use_cache and not force:
            existing = panels_for_episode(project, episode_id)
            if existing and _track_value(existing) == _track_value(skeleton):
                return TransformResult(
                    annotations=tuple(existing), artifacts=(), cost_usd_actual=0.0
                )
        for skel in skeleton:
            project.graph.add_annotation(skel)
        return TransformResult(
            annotations=tuple(skeleton), artifacts=(), cost_usd_actual=0.0
        )
