"""``episode_to_sound_design.foley`` — sound design on the graph path (braidio#161).

nw owns the work: its ``narration_to_sound_design.foley`` Transform
(:mod:`nw.transforms.sound_design`) plans one falaw call that runs
``foley.score`` over a narration track, caches it, stores the mix in falaw's
content store and records ``sound-design/v1``. It is built to be subclassed
by a host with its own narration schema, which is all this module does: the
"narration track" is braidio's ``episode-render/v1``, and the three host
seams read it.

- the audio is the episode's mix, found by content first
  (:func:`~braidio.transforms._common.media_path`), named relative to the
  project so the plan (and its cache key) is not pinned to this machine. For
  a production with a music bed it is the episode's **voice stem** (the mix
  without the bed, braidio#227): a bed in the "voice" reads as speech to
  foley's aligner and ducking. The bed stem is laid back under the design's
  mix when the design completes, so the mix a host serves still has its bed;
- the segments are the episode's spoken sentences, with their starts
  estimated inside each beat's place on the persisted timeline: the same
  :func:`braidio.sound_design.talk_segments` the fast path uses, so both paths
  hand foley the same thing for the same production.

The switch and knobs are the singleton ``sound-design-config/v1`` node that
ingest writes only when ``weave_project(sound_design=...)`` asks for it, so a
production that does not ask keeps exactly the graph and episode it had. A
design derives from ``[episode, config]``: a re-weave that changes the
episode, or a changed knob, re-stales it through ordinary freshness.
"""

from __future__ import annotations

import json
import os
import tempfile
import warnings
from contextvars import ContextVar
from pathlib import Path, PurePath

from lacing import Annotation
from nw import register_transform
from nw.bodies.sound_design import SOUND_DESIGN_CONFIG_BODY_SCHEMA_URI
from nw.transforms.sound_design import (
    SOUND_DESIGN_TAGS,
    NarrationToSoundDesign,
    NarrationTrack,
)

from braidio.bodies._render_nodes import EPISODE_RENDER_V1
from braidio.sound_design import spoken_of, talk_segments, talk_spans
from braidio.transforms._common import (
    TIER_EPISODE_RENDER,
    TIER_SOUND_DESIGN,
    TIER_WEAVE_CONFIG,
    annotation_parents,
    graph_index,
    media_path,
    require_tier,
    resolve_parents,
)

NAME = "episode_to_sound_design.foley"

#: Where a design's mix is kept inside the project, which owns its bytes: a
#: server the project is moved to cannot read falaw's store on this machine.
SOUND_DESIGN_DIR = ("data", "sound-design")

#: Catalog kind of a design's mix (a mastered FLAC).
SOUND_DESIGN_CATALOG_KIND = "audio"

#: The project an :class:`EpisodeToSoundDesign` is executing in: completing a
#: design reads the episode's bed stem from it (nw's completion hook gets no
#: project).
_EXECUTING_PROJECT: ContextVar = ContextVar("braidio_sound_design_project", default=None)


def episode_talk(project, episode: Annotation) -> tuple[list[str], list[float]]:
    """foley's ``(segments, segment_starts)`` for ``episode``: its spoken
    sentences at estimated starts on its own timeline. Refuses an episode with
    nothing spoken (only clips and stings), rather than design nothing."""
    from braidio.transforms._episode import episode_script, episode_timeline

    script = episode_script(project, episode)
    timeline = episode_timeline(project, episode)
    spans = talk_spans(timeline.beats, (spoken_of(b) for b in script.beats))
    segments, starts = talk_segments(spans)
    if not segments:
        raise ValueError(
            f"sound design: episode {episode.id} has no spoken text to place "
            "sounds on (only clips or stings); weave it without sound_design."
        )
    return segments, starts


class EpisodeToSoundDesign(NarrationToSoundDesign):
    """``episode-render/v1`` + ``sound-design-config/v1`` → ``sound-design/v1``."""

    name = NAME
    input_kinds = (EPISODE_RENDER_V1, SOUND_DESIGN_CONFIG_BODY_SCHEMA_URI)
    attributed_to = f"agent:{NAME}"

    def narration_track(self, project, narration: Annotation) -> NarrationTrack:
        # nw calls this only once a design is enabled (nw#221), so the episode's
        # text is read exactly when it will be designed
        segments, starts = episode_talk(project, narration)
        return NarrationTrack(
            segments,
            self.narration_audio_path(project, narration),
            starts,
            # the voice stem is not the episode's own audio: name its bytes, so
            # nw checks the file against them and not the bedded mix's id
            artifact_id=narration.body.get("voice_artifact_id"),
        )

    def narration_audio_path(self, project, narration: Annotation) -> PurePath:
        """The episode's voice stem when it has one (a production with a bed,
        braidio#227), else its mix; relative to the project."""
        body = narration.body
        if body.get("voice_artifact_id") or body.get("voice_url"):
            ref, what = _stem_ref(body, "voice"), f"episode {narration.id}'s voice stem"
        else:
            ref, what = body, f"episode {narration.id}"
        audio = media_path(project.root, ref, what=what).resolve()
        root = Path(project.root).resolve()
        if not audio.is_relative_to(root):
            raise ValueError(
                f"{self.name}: {what} ({audio}) is outside the project ({root}); "
                "re-weave it here before designing."
            )
        return PurePath(audio.relative_to(root))

    def execute(self, project, plan, skeleton, **kwargs):
        token = _EXECUTING_PROJECT.set(project)
        try:
            return super().execute(project, plan, skeleton, **kwargs)
        finally:
            _EXECUTING_PROJECT.reset(token)

    def _complete_annotation(self, skeleton: Annotation, artifact) -> Annotation:
        """nw's completed design, with the episode's bed laid back under its mix
        when the design listened to the voice stem (braidio#227)."""
        completed = super()._complete_annotation(skeleton, artifact)
        project = _EXECUTING_PROJECT.get()
        if project is None:
            return completed
        index = graph_index(project)
        episode = next(
            (a for a in resolve_parents(skeleton, index) if a.tier == TIER_EPISODE_RENDER),
            None,
        )
        if episode is None or not (
            episode.body.get("bed_artifact_id") or episode.body.get("bed_url")
        ):
            return completed
        return completed.model_copy(
            update={"body": _with_bed(project, episode, completed.body, index=index)}
        )


def _stem_ref(body: dict, stem: str) -> dict:
    """``{"artifact_id", "url"}`` of an episode body's ``stem`` (``"voice"`` /
    ``"bed"``), the shape :func:`media_path` reads."""
    return {"artifact_id": body.get(f"{stem}_artifact_id"), "url": body.get(f"{stem}_url")}


def _with_bed(project, episode: Annotation, body: dict, *, index: dict) -> dict:
    """``body`` (a completed ``sound-design/v1``) naming its mix with the
    episode's bed stem laid back under it, mastered as the episode was.

    The design's own mix (without the bed) stays in falaw's content store and is
    named in ``notes``; ``master_report`` is still foley's, of that mix.
    """
    from falaw.content import default_content_store
    from nw.bodies.sound_design import SoundDesignBodyV1

    from braidio.weave import lay_bed
    from braidio.weave_config import WeaveConfig

    store = default_content_store()
    designed_id = body["artifact_id"]
    bed = media_path(
        project.root, _stem_ref(episode.body, "bed"), what=f"episode {episode.id}'s bed stem"
    )
    cfg = require_tier(resolve_parents(episode, index), TIER_WEAVE_CONFIG)
    weave = WeaveConfig.from_snapshot(cfg.body.get("config", {}))
    with tempfile.TemporaryDirectory(prefix="braidio-bed-") as work:
        designed = store.blob_path(designed_id)
        if designed is None:  # an opaque blob backend: spool the bytes
            designed = Path(work) / "design.flac"
            designed.write_bytes(store.get_blob(designed_id))
        out = lay_bed(
            designed,
            bed,
            Path(work) / "design-with-bed.flac",
            target_lufs=weave.target_lufs,
            true_peak=weave.true_peak_dbtp,
            sample_rate=weave.sample_rate,
        )
        import braidio

        duration = float(braidio.duration_s(out))
        bedded_id = store.put_blob(out.read_bytes())
    note = (
        f"the episode's music bed was laid back under the design and the sum "
        f"mastered to {weave.target_lufs:g} LUFS; the design without the bed is "
        f"{designed_id}, which master_report measures (braidio#227)"
    )
    return SoundDesignBodyV1.model_validate(
        {
            **body,
            "artifact_id": bedded_id,
            "duration_s": duration,
            "notes": [*body.get("notes", []), note],
        }
    ).model_dump(mode="json")


def sound_design_of(project, episode: Annotation) -> Annotation | None:
    """The current sound design of ``episode``: the most recent **fresh** one
    derived from it, or ``None``. A design whose config was dropped (a weave
    without ``sound_design``) or changed reads stale, and is not current: a
    host must not ship credits for a design the production no longer asks
    for."""
    import nw

    designs = sorted(
        (
            a
            for a in nw.annotations_at_tier(project.root, TIER_SOUND_DESIGN)
            if str(episode.id) in {str(i) for i in annotation_parents(a)}
        ),
        key=lambda a: a.provenance.generated_at_time.to_seconds(),
    )
    if not designs:
        return None
    import nw

    fresh = nw.fresh_ids(project)
    return next((a for a in reversed(designs) if a.id in fresh), None)


def register_sound_design(project, design: Annotation) -> bool:
    """Make ``design``'s mix retrievable by id from the project's delivery
    catalog (braidio#193).

    The mix is a FLAC in falaw's content store, named by the body's
    ``artifact_id`` (its sha256): nothing a host can fetch. This copies the bytes
    into the project (**copy, not link**: the store is shared, see
    ``braidio.importing._writer._place_into``) and registers them under that same
    id, never a ``file://`` url, so ``HostArtifactCatalog`` serves them wherever
    the project lives. (A graph-path *episode* is not catalog-registered: a host
    reaches it by ``episode_id``; this design's mix is reached by ``artifact_id``.)
    Idempotent: an id the catalog already answers for (row and blob) is left alone.

    Returns whether the mix is retrievable, and never raises for a reason the
    design does not control: ``False`` with a ``RuntimeWarning`` when the host's
    catalog backend is not the local one (refused before any copy), or when the
    blob is gone from falaw's store (pruned; change a knob to re-design).
    """
    from falaw.content import default_content_store
    from nw.atomic_io import copy_file_atomic
    from nw.media_catalog import (
        CatalogBackendMismatch,
        HostArtifactCatalog,
        assert_local_backend,
    )

    artifact_id = design.body.get("artifact_id")
    if not artifact_id:
        return False
    catalog = HostArtifactCatalog(project.root)
    try:
        assert_local_backend()
    except CatalogBackendMismatch as exc:
        warnings.warn(
            f"the sound design's mix was not registered for retrieval: {exc}",
            RuntimeWarning,
            stacklevel=3,
        )
        return False
    if catalog.has(artifact_id):
        return True
    store = default_content_store()
    if not store.has_blob(artifact_id):
        warnings.warn(
            f"sound design {design.id}: its mix {artifact_id[:12]}... is gone from "
            "falaw's content store (pruned), so it cannot be served; change a "
            "sound_design knob to re-design it",
            RuntimeWarning,
            stacklevel=3,
        )
        return False
    dest = Path(project.root).joinpath(*SOUND_DESIGN_DIR, f"{artifact_id}.flac")
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = store.blob_path(artifact_id)
    if source is not None:
        copy_file_atomic(source, dest)
    else:  # an opaque blob backend: no path, so spool the bytes through a temp file
        fd, name = tempfile.mkstemp(dir=dest.parent, prefix=".part-", suffix=".flac")
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(store.get_blob(artifact_id))
            os.replace(name, dest)
        finally:
            Path(name).unlink(missing_ok=True)
    catalog.register(
        dest,
        kind=SOUND_DESIGN_CATALOG_KIND,
        artifact_id=artifact_id,
        duration_s=design.body.get("duration_s"),
        note="sound design mix",
    )
    return catalog.has(artifact_id)


#: A status door is polled and capped (nw drops a whole status over its limit),
#: so credits ride in it only up to this many bytes of JSON; past it the row says
#: how many there are and a host reads them from the design node.
STATUS_CREDITS_MAX_BYTES = 8_192


def sound_design_row(design: Annotation, *, retrievable: bool | None = None) -> dict:
    """What a host needs to hand out a design (path-free): the mix's id, length,
    whether it is ``retrievable`` from the catalog, and the credits that must be
    published with it (or, if they are large, how many there are)."""
    body = design.body
    duration = body.get("duration_s")
    credits = body.get("credits", {})
    row = {
        "design_id": str(design.id),
        "artifact_id": body.get("artifact_id"),
        "duration_s": None if duration is None else round(float(duration), 2),
        "retrievable": retrievable,
    }
    if len(json.dumps(credits, default=str)) <= STATUS_CREDITS_MAX_BYTES:
        row["credits"] = credits
    else:
        row["credits"] = None
        row["credits_omitted"] = True  # read them from the design node
    return row


register_transform(NAME, EpisodeToSoundDesign(), tags=SOUND_DESIGN_TAGS)
