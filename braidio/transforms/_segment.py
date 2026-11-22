"""``segment_extraction.ffmpeg`` — cut + pad one source segment to audio.

A **local-render** Transform: ``plan`` returns a zero-call ``Plan`` + a
``segment-extraction/v1`` skeleton (the playable window read from the
audio-clip's ``MediaRef`` interval, a ``cache_key`` over the extraction
inputs); ``execute`` cuts via ``braidio.extract_padded`` unless an identical
``cache_key`` already has an artifact. It derives from
``[audio-clip, source-media, weave-config]`` — change the window, the source,
or the pad/fade config and only this extraction (and the episode) re-stale.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from falaw import Plan
from lacing import Annotation
from nw import BaseTransform, TransformInputs, TransformResult, register_transform
from nw.transforms._provenance import derive_provenance

from braidio.bodies._domain import AUDIO_CLIP_V1
from braidio.bodies._render_nodes import (
    WEAVE_CONFIG_V1,
    SOURCE_MEDIA_V1,
    SEGMENT_EXTRACTION_V1,
    SegmentExtractionBodyV1,
)
from braidio.transforms._common import (
    TIER_SOURCE_MEDIA,
    TIER_WEAVE_CONFIG,
    TIER_SEGMENT_EXTRACTION,
    singleton,
    graph_index,
    resolve_parents,
    require_tier,
    adopt_output,
    cached_output,
    fresh_equivalent,
    audio_artifact,
    file_url,
    source_media_identity,
    source_media_path,
)

NAME = "segment_extraction.ffmpeg"


def _pads_fades(config: dict):
    """``((pre, post), (fade_in, fade_out), min_len)`` from a weave-config."""
    pads = (
        float(config.get("clip_pre_roll_s", 0.4)),
        float(config.get("clip_post_roll_s", 0.3)),
    )
    fades = (
        float(config.get("clip_fade_in_s", 0.5)),
        float(config.get("clip_fade_out_s", 0.8)),
    )
    return pads, fades, float(config.get("clip_min_len_s", 2.2))


@register_transform(NAME)
class SegmentExtractionFFmpeg(BaseTransform):
    """Audio clip (+ source-media + weave-config) → ``segment-extraction/v1``."""

    name = NAME
    input_kinds = (AUDIO_CLIP_V1, SOURCE_MEDIA_V1, WEAVE_CONFIG_V1)
    output_kind = SEGMENT_EXTRACTION_V1

    def plan(
        self, project, inputs: TransformInputs, *, params=None
    ) -> tuple[Plan, tuple[Annotation, ...]]:
        clip = inputs.primary[0]
        start_s = clip.reference.interval.start.to_seconds()
        end_s = clip.reference.interval.end.to_seconds()

        index = graph_index(project)
        source_node_id = uuid.UUID(str(clip.body["source_node_id"]))
        sm = index[source_node_id]
        source_identity = source_media_identity(sm.body)

        cfg = singleton(project, TIER_WEAVE_CONFIG)
        pads, fades, min_len = _pads_fades(cfg.body.get("config", {}))
        from nw.transforms import cache_key as transform_cache_key

        # nw's shared derivation (nw#54): byte-identical to the hand-rolled
        # key at the default impl_version; a behaviour bump is the salt.
        cache_key = transform_cache_key(
            self,
            "segment",
            source_identity,
            str(start_s),
            str(end_s),
            str(pads),
            str(fades),
            str(min_len),
        )

        full = TransformInputs(
            primary=(clip,),
            context={SOURCE_MEDIA_V1: (sm,), WEAVE_CONFIG_V1: (cfg,)},
        )
        skeleton = Annotation(
            id=uuid.uuid4(),
            tier=TIER_SEGMENT_EXTRACTION,
            reference=clip.reference,
            body=SegmentExtractionBodyV1(
                cache_key=cache_key, start_s=start_s, end_s=end_s
            ).model_dump(),
            body_schema_uri=SEGMENT_EXTRACTION_V1,
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
        import braidio  # runtime attr access so tests can monkeypatch extract_padded

        skel = skeleton[0]
        cache_key = skel.body["cache_key"]
        if use_cache and not force:
            # Same two-step as narration_render: reuse the fresh equivalent
            # node if there is one, else complete this skeleton with the
            # cache-keyed artifact — never return a node whose parents a
            # re-ingest replaced or removed (braidio#51).
            existing = fresh_equivalent(project, skel)
            reused = existing is not None  # an adopted node is written, not reused
            if existing is None:
                hit = cached_output(project, TIER_SEGMENT_EXTRACTION, cache_key)
                if hit is not None:
                    existing = adopt_output(skel, hit)
                    project.graph.add_annotation(existing)
            if existing is not None:
                return TransformResult(
                    annotations=(existing,),
                    artifacts=(),
                    cost_usd_actual=0.0,
                    reused_ids=frozenset({existing.id}) if reused else frozenset(),
                )

        parents = resolve_parents(skel, graph_index(project))
        sm = require_tier(parents, TIER_SOURCE_MEDIA)
        cfg = require_tier(parents, TIER_WEAVE_CONFIG)
        pads, fades, min_len = _pads_fades(cfg.body.get("config", {}))
        # by content first: a moved project reads its registered source from the
        # catalog, not from the path recorded on another machine (braidio#153)
        asset_path = source_media_path(project.root, sm.body)

        out_path = project.root / "data" / "clips" / f"{skel.id}.mp3"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        braidio.extract_padded(
            asset_path,
            float(skel.body["start_s"]),
            float(skel.body["end_s"]),
            out_path,
            pre_roll_s=pads[0],
            post_roll_s=pads[1],
            fade_in_s=fades[0],
            fade_out_s=fades[1],
            min_len_s=min_len,
        )
        artifact = audio_artifact(
            out_path,
            transform_name=self.name,
            derived_from=skel.provenance.was_derived_from,
        )
        completed = skel.model_copy(
            update={
                "body": {
                    **skel.body,
                    "artifact_id": artifact.asset_id,
                    "url": file_url(out_path),
                }
            }
        )
        project.graph.add_annotation(completed)
        return TransformResult(
            annotations=(completed,),
            artifacts=(artifact,),
            cost_usd_actual=0.0,
            cache_hit_savings_usd=0.0,
        )
