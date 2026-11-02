"""Speak one narration beat with a person's recording instead of TTS (braidio#86, item 1).

A recording does not edit a render. It is an **authoring** decision, recorded
as one ``take-override/v1`` node per beat (identity: its ``beat_id``), and the
ordinary narration transform honours it: when a beat has an override made
against its current text, ``narration_render.tts`` plans a render keyed
``upload:<artifact_id>`` that derives from the override, and completes it from
the recording with no synthesis and no spend. So a re-weave with the same
script is how the recording reaches the mix, and every later member shifts by
the difference in length (``braidio.importing.NARRATION_REPLACEMENT_NOTE``
says why that is a re-weave and never an in-place edit).

Why a separate node rather than rewriting the beat's TTS render in place: a
render's ``cache_key`` names the audio it holds. An uploaded recording kept
under the beat's TTS key would be served by the render cache to *another*
beat with the same words, and an older TTS node with the same key could
displace it on a config round-trip. ``upload:<artifact_id>`` cannot equal any
computed key, the same bargain as the importer's
:data:`~braidio.importing.IMPORTED_CACHE_KEY_PREFIX`.

What keeps, and what drops, an override:

- **editing the beat's text drops it, by design.** The override records a
  digest of the words the person said; once the beat says something else the
  recording no longer speaks it, so the beat is synthesized again. Restoring
  the original text brings the recording back.
- **changing the voice or the weave-config keeps it.** The upload render's key
  depends on the recording alone, not on any synthesis setting.
- **moving the line to another script position drops it.** A beat's identity
  is its script position (``beat_id``), so the override stays with the
  position it was recorded for; the moved words are synthesized there, and
  the recording speaks again only if those words come back to that position.
- **whitespace does not matter.** The digest is of the normalized words, so
  re-flowing a line keeps its recording.
- :func:`restore_take` removes the override; the next re-weave synthesizes
  the beat again (a cache hit when its TTS render is still fresh).

The verbs:

- :func:`preview_take` — free, writes nothing (no node, no file): how long the
  recording is against the take the current mix plays, and which members of
  that mix it moves;
- :func:`replace_take` — validates the recording, copies it into the project
  by content (``data/takes/<artifact_id><suffix>``) and writes the beat's
  override;
- :func:`restore_take` — removes it.

Scope: narration beats of a project braidio wove. A dialogue exchange is not
a narration beat, and an imported production's mix is not exactly the sum of
its takes (braidio#86, "two traps"), so a beat whose take was imported is
refused rather than silently re-woven.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from lacing import Annotation, Artifact

from braidio.bodies import TAKE_OVERRIDE_V1, TakeOverrideBodyV1
from braidio.transforms._common import (
    TIER_NARRATION_RENDER,
    TIER_NARRATIVE_BEAT,
    TIER_TAKE_OVERRIDE,
    annotation_parents,
    beat_id as _beat_id,
    file_url,
    node_ref,
)

#: Where an uploaded recording is kept, under the project root (beside
#: ``data/tts``). Files are named by content: ``<artifact_id><suffix>``.
TAKES_SUBDIR = ("data", "takes")
#: The ``was_generated_by`` of a take-override node and of its recording.
REPLACE_TAKE = "braidio:replace_take"
#: Who a take-override is attributed to (what ingest's authored nodes say).
_ATTRIBUTED_TO = "agent:braidio"
#: Prefix of a narration render's ``cache_key`` when it is spoken by a
#: take-override. A computed key is a 64-hex digest, so this can never equal
#: one: no render cache can serve a person's recording to another beat, and
#: no synthesized take can stand in for the recording.
UPLOAD_CACHE_KEY_PREFIX = "upload:"


def text_digest(text: str) -> str:
    """The SHA-256 of a beat's words: what a take-override was recorded against.

    Whitespace is normalized first, so re-flowing a line (a trailing space, a
    line break) keeps its recording; changing a word drops it.

    >>> text_digest("hello  world ") == text_digest("hello world") != text_digest("hello.")
    True
    """
    words = " ".join(text.split())
    return hashlib.sha256(words.encode("utf-8")).hexdigest()


def upload_cache_key(artifact_id: str) -> str:
    """The ``cache_key`` of a narration render spoken by recording ``artifact_id``.

    >>> upload_cache_key("ab" * 32)[:12]
    'upload:ababa'
    """
    return f"{UPLOAD_CACHE_KEY_PREFIX}{artifact_id}"


def take_override_for(project, beat: Annotation) -> Optional[Annotation]:
    """The override that speaks narration ``beat`` now, or ``None``.

    The beat's override, if its ``text_digest`` matches the beat's **current**
    text; one recorded against other words is ignored (it no longer says what
    the beat says). Read by ``narration_render.tts`` at plan time.
    """
    overrides = _overrides_of(project, str(beat.body.get("beat_id")))
    if len(overrides) > 1:
        raise ValueError(
            f"expected at most one {TIER_TAKE_OVERRIDE!r} node for beat "
            f"{beat.body.get('beat_id')!r}, found {len(overrides)}: an override "
            "is identified by its beat — run restore_take on it, then "
            "replace_take again"
        )
    if not overrides:
        return None
    current = text_digest(beat.body.get("text", "") or "")
    return overrides[0] if overrides[0].body.get("text_digest") == current else None


@dataclass(frozen=True)
class TakePreview:
    """What replacing one take would do, before anything is written.

    ``render_id`` is the take ``episode_id`` (the newest mix that plays this
    beat) plays now; ``shifted_member_ids`` are the members heard after it,
    each of which moves by ``delta_s``.
    """

    beat_id: str
    render_id: str
    old_duration_s: float
    new_duration_s: float
    episode_id: Optional[str] = None
    shifted_member_ids: tuple[str, ...] = ()

    @property
    def delta_s(self) -> float:
        """How much later everything after the take is heard (negative: earlier)."""
        return self.new_duration_s - self.old_duration_s


def preview_take(project, beat, recording) -> TakePreview:
    """What :func:`replace_take` would change for ``beat``. Free; writes nothing.

    ``beat`` is the narration beat's script position (``int``) or its
    ``beat_id`` string; ``recording`` is a local audio file. Refuses exactly
    what :func:`replace_take` refuses, and a beat no mix plays yet.
    """
    node = _narration_beat(project, beat)
    _refuse_imported(project, node)
    new_duration = _recording_duration(_existing(recording))
    episode, render = _heard_take(project, node)
    members = [str(m) for m in episode.body.get("ordered_member_ids") or ()]
    return TakePreview(
        beat_id=str(node.body["beat_id"]),
        render_id=str(render.id),
        old_duration_s=float(render.body.get("duration_s") or 0.0),
        new_duration_s=new_duration,
        episode_id=str(episode.id),
        shifted_member_ids=tuple(members[members.index(str(render.id)) + 1 :]),
    )


def replace_take(project, beat, recording) -> Annotation:
    """Have ``recording`` speak narration ``beat``; return its take-override node.

    The recording must be playable audio with a positive duration. It is
    copied into the project by content (``data/takes/<artifact_id><suffix>``),
    so the take does not depend on where it was uploaded from, and recorded
    against the beat's current text. A beat that already has an override has
    it rewritten under the same id (one override per beat). Nothing is heard
    until the project is re-woven: run ``weave_project`` again with the same
    script (:func:`preview_take` says what will move).
    """
    node = _narration_beat(project, beat)
    _refuse_imported(project, node)
    src = _existing(recording)
    duration = _recording_duration(src)
    artifact_id = Artifact.from_path(
        src,
        kind="audio",
        was_generated_by=REPLACE_TAKE,
        was_attributed_to=_ATTRIBUTED_TO,
    ).asset_id
    dst = _store_recording(project, src, artifact_id=artifact_id)
    body = TakeOverrideBodyV1(
        beat_id=str(node.body["beat_id"]),
        artifact_id=artifact_id,
        url=file_url(dst),
        duration_s=duration,
        text_digest=text_digest(node.body.get("text", "") or ""),
    ).model_dump(mode="json")

    current = _overrides_of(project, body["beat_id"])
    if len(current) == 1 and current[0].body == body:
        return current[0]  # the same recording again: not rewritten, not re-dated
    ann = Annotation(
        id=current[0].id if current else uuid.uuid4(),
        tier=TIER_TAKE_OVERRIDE,
        reference=current[0].reference if current else node_ref(TIER_TAKE_OVERRIDE),
        body=body,
        body_schema_uri=TAKE_OVERRIDE_V1,
        provenance=_upload_provenance(artifact_id),
    )
    for old in current:
        project.graph.remove_annotation(old.id)
    project.graph.add_annotation(ann)
    return ann


def restore_take(project, beat) -> Optional[Annotation]:
    """Remove ``beat``'s take-override; return it (``None``: it had none).

    The next re-weave synthesizes the beat again — a cache hit, at no cost,
    while its earlier TTS render is still fresh. The recording's file stays
    in ``data/takes``: other overrides may name the same bytes.
    """
    removed = _overrides_of(project, _as_beat_id(beat))
    for ann in removed:
        project.graph.remove_annotation(ann.id)
    return removed[-1] if removed else None


def _as_beat_id(beat) -> str:
    return _beat_id(beat) if isinstance(beat, int) else str(beat)


def _overrides_of(project, beat_id: str) -> list[Annotation]:
    return [a for a in _all_overrides(project) if str(a.body.get("beat_id")) == beat_id]


def _narration_beat(project, beat) -> Annotation:
    """The ``narrative-beat`` node of ``beat``; ``ValueError`` names the ones there are."""
    import nw

    wanted = _as_beat_id(beat)
    beats = nw.annotations_at_tier(project.root, TIER_NARRATIVE_BEAT)
    hits = [a for a in beats if a.body.get("beat_id") == wanted]
    if not hits:
        known = sorted(a.body.get("beat_id", "?") for a in beats)
        raise ValueError(
            f"no narration beat {wanted!r} in this project "
            f"(narration beats: {', '.join(known) or 'none'})"
        )
    return hits[-1]


def _takes_of(project, beat: Annotation) -> list[Annotation]:
    """Every narration render derived from ``beat``, current or historical."""
    import nw

    return [
        a
        for a in nw.annotations_at_tier(project.root, TIER_NARRATION_RENDER)
        if beat.id in annotation_parents(a)
    ]


def _refuse_imported(project, beat: Annotation) -> None:
    """Refuse a beat whose take an imported production shipped (braidio#86, trap 1)."""
    from braidio.importing import IMPORTED_CACHE_KEY_PREFIX

    if any(
        str(t.body.get("cache_key", "")).startswith(IMPORTED_CACHE_KEY_PREFIX)
        for t in _takes_of(project, beat)
    ):
        raise ValueError(
            f"narration beat {beat.body.get('beat_id')!r} is a take of an imported "
            "production: its mix is not exactly the sum of its takes, so re-weaving "
            "it would change more than this line (braidio#86). Replacing an "
            "imported take is not supported yet."
        )


def _heard_take(project, beat: Annotation) -> tuple[Annotation, Annotation]:
    """``(episode, render)``: the current mix, and ``beat``'s take in it.

    The current mix is :meth:`braidio.Project.current_episode` (the one rule
    every "current" reader shares, braidio#169); the take is read from its
    ``ordered_member_ids``, never "the last render derived from the beat": a
    beat's history holds takes no current mix plays.
    """
    episode = project.current_episode()
    takes = {str(t.id): t for t in _takes_of(project, beat)}
    for member in (episode.body.get("ordered_member_ids") or ()) if episode else ():
        if str(member) in takes:
            return episode, takes[str(member)]
    raise ValueError(
        f"narration beat {beat.body.get('beat_id')!r} is in no woven mix yet: "
        "weave the project first"
    )


def pending_takes(project) -> tuple[str, ...]:
    """The ``beat_id``s whose recording the current mix does not play yet.

    :func:`replace_take` writes an authoring node, so nothing reads stale until
    the next re-weave plans it: this is how a host knows a re-weave is due. A
    beat counts when its override applies (made against the beat's current
    words) and the current mix has no take keyed by that recording.
    """
    import nw

    episode = project.current_episode()
    heard = set()
    if episode is not None:
        members = {str(m) for m in episode.body.get("ordered_member_ids") or ()}
        heard = {
            a.body.get("cache_key")
            for a in nw.annotations_at_tier(project.root, TIER_NARRATION_RENDER)
            if str(a.id) in members
        }
    beats = {
        str(b.body.get("beat_id")): b
        for b in nw.annotations_at_tier(project.root, TIER_NARRATIVE_BEAT)
    }
    pending = []
    for beat_id in sorted(
        {str(o.body.get("beat_id")) for o in _all_overrides(project)}
    ):
        beat = beats.get(beat_id)
        override = take_override_for(project, beat) if beat is not None else None
        if override is not None and (
            upload_cache_key(override.body["artifact_id"]) not in heard
        ):
            pending.append(beat_id)
    return tuple(pending)


def _all_overrides(project) -> list[Annotation]:
    import nw

    return nw.annotations_at_tier(project.root, TIER_TAKE_OVERRIDE)


def _existing(recording) -> Path:
    path = Path(recording)
    if not path.is_file():
        raise FileNotFoundError(f"no recording at {path}")
    return path


def _recording_duration(path: Path) -> float:
    """The recording's length in seconds; ``ValueError`` unless it is playable audio.

    Never a best-effort ``0.0``: a take of no length would weave as silence
    where the line should be, and say nothing about why.
    """
    import braidio  # runtime attr access so tests can monkeypatch duration_s

    try:
        duration = float(braidio.duration_s(path))
    except (subprocess.CalledProcessError, KeyError, ValueError) as exc:
        raise ValueError(
            f"{path.name} is not a playable audio recording "
            "(ffprobe could not read its duration)"
        ) from exc
    if not duration > 0:  # also refuses NaN
        raise ValueError(f"{path.name} holds no audio (duration {duration} s)")
    return duration


def _store_recording(project, src: Path, *, artifact_id: str) -> Path:
    """Copy ``src`` into the project, named by its content; return the copy."""
    takes = Path(project.root).joinpath(*TAKES_SUBDIR)
    takes.mkdir(parents=True, exist_ok=True)
    dst = takes / f"{artifact_id}{src.suffix.lower()}"
    if not dst.exists():  # the name is the content: an existing copy is these bytes
        shutil.copyfile(src, dst)
    return dst


def _upload_provenance(artifact_id: str):
    """An authored node's provenance, derived from the recording's bytes alone."""
    from lacing import Provenance, RationalTime

    return Provenance(
        was_generated_by=REPLACE_TAKE,
        was_attributed_to=_ATTRIBUTED_TO,
        was_derived_from=[artifact_id],
        generated_at_time=RationalTime.now(),
        activity="import",
    )
