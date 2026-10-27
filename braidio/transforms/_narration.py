"""``narration_render.tts`` — synthesize one narration beat to audio.

A **local-render** Transform (no fal call): ``plan`` returns a zero-call
``Plan`` + a ``narration-render/v1`` skeleton carrying a ``cache_key`` over
the audio-affecting inputs; ``execute`` synthesizes via ``braidio.narrate``
(ElevenLabs), unless an identical ``cache_key`` already has an artifact
(compare-and-skip). It derives from ``[beat, voice-assignment, weave-config]``.

The skeleton's ``provenance.was_derived_from`` records exactly those inputs;
``execute`` re-resolves them from the graph (it receives no ``inputs``) and
writes the completed node through ``project.graph`` so ``nw.stale_after``
sees it.

A caller's ElevenLabs key reaches the synthesis through ``execute(secrets=)``
— nw's per-caller credential seam — as ``secrets["elevenlabs"]``, and nowhere
else: not the node, not provenance, not the ``cache_key`` (a key is not an
audio-affecting input). ``None`` falls back to the process environment
(thorwhalen/braidio#58).
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path

from falaw import Plan
from lacing import Annotation
from nw import BaseTransform, TransformInputs, TransformResult, register_transform
from nw.transforms._provenance import derive_provenance

from braidio.bodies._domain import NARRATIVE_BEAT_V1
from braidio.bodies._render_nodes import (
    WEAVE_CONFIG_V1,
    VOICE_ASSIGNMENT_V1,
    NARRATION_RENDER_V1,
    NarrationRenderBodyV1,
)
from braidio.cost import backend_cost_usd
from braidio.delivery import model_supports_speed
from braidio.render import narrate_turns, plan_beat_turns
from braidio.tts import (
    DEFAULT_MODEL_ID,
    DEFAULT_VOICE_ID,
    narrator_from_speak,
    tts_backend,
)
from braidio.weave_config import WeaveConfig
from braidio.transforms._common import (
    TIER_NARRATIVE_BEAT,
    TIER_VOICE_ASSIGNMENT,
    TIER_WEAVE_CONFIG,
    TIER_NARRATION_RENDER,
    beat_ordinal,
    child_at_tier,
    singleton,
    graph_index,
    resolve_parents,
    require_tier,
    adopt_output,
    cached_output,
    fresh_equivalent,
    audio_artifact,
    safe_duration,
    file_url,
    elevenlabs_key,
)

NAME = "narration_render.tts"


def _narration_cache_key(
    transform, text: str, voice_id: str, model_id: str, settings, turns=(), *, tts=None
) -> str:
    """The output identity for one TTS render — nw's shared derivation.

    ``nw.transforms.cache_key`` folds ``impl_version`` with the same
    omit-if-default rule as the falaw salt, so keys are byte-identical to
    the hand-rolled predecessor until this transform's first real behaviour
    bump — which is then the first salt (nw#54; drift in the previous
    private ``mixing._cache`` import would have silently changed cache keys
    on PAID calls).

    ``turns`` is the paced plan (:func:`_beat_turns`): what is synthesized when
    the config paces narration. Unpaced, it is empty and adds nothing, so an
    unpaced project keeps its keys. ``tts`` (a registered backend name,
    braidio#132) is folded in only when set, by the same rule: an ElevenLabs
    project keeps its keys, and switching backends changes every one.
    """
    from nw.transforms import cache_key as transform_cache_key

    parts = [
        "narration",
        text,
        voice_id,
        model_id,
        json.dumps(settings or {}, sort_keys=True),
    ]
    if turns:
        parts.append(json.dumps([asdict(t) for t in turns], sort_keys=True))
    if tts:
        parts.append(f"tts={tts}")
    return transform_cache_key(transform, *parts)


def _speak_beat(
    text: str,
    out_path: Path,
    *,
    tts: str | None,
    turns,
    api_key,
    voice_id: str,
    model_id: str,
    settings,
) -> tuple[Path, bool]:
    """Synthesize one beat → ``(audio path, was_cached)``.

    ElevenLabs through ``braidio.narrate`` (``was_cached``: mixing served it
    from disk), or the registered backend ``tts`` through
    :func:`braidio.tts.narrator_from_speak` (never cached; its file carries
    the format it reported). Paced ``turns`` are one call each, concatenated
    into ``out_path``.
    """
    import braidio  # runtime attr access so tests can monkeypatch narrate

    cached: list[bool] = []
    if tts:
        synthesize = narrator_from_speak(tts_backend(tts).speak)
    else:

        def synthesize(turn_text, turn_path, **kwargs):
            path, hit = braidio.narrate(
                turn_text, turn_path, return_cache_status=True, **kwargs
            )
            cached.append(hit)
            return path

    kwargs = dict(api_key=api_key, voice_id=voice_id, model_id=model_id)
    if turns:  # paced: one call per turn, real silence between (braidio#118)
        paced = narrate_turns(
            turns,
            stem=out_path.with_suffix(""),
            voice_settings=settings,
            narrate_fn=synthesize,
            **kwargs,
        )
        # one turn and no gap is that turn's own file, in the backend's format
        final = out_path.with_suffix(Path(paced).suffix)
        shutil.move(paced, final)
        for leftover in out_path.parent.glob(f"{out_path.stem}-turn*"):
            leftover.unlink()  # per-turn parts and gaps: only the mix is a node
        return final, bool(cached) and all(cached)
    path = synthesize(text, out_path, voice_settings=settings, **kwargs)
    return Path(path), bool(cached) and all(cached)


def _beat_settings(beat, config: dict) -> dict:
    """The beat's own voice settings, else the config's — the fast path's rule."""
    return beat.body.get("voice_settings") or config.get("voice_settings", {})


def _beat_turns(beat, config: dict, *, model_id: str, settings: dict) -> list:
    """The beat's paced turns — ``[]`` when the config does not pace narration.

    The fast path's planner (:func:`braidio.render.plan_beat_turns`), so a
    ``segmentation_unit`` other than ``"beat"`` cuts a beat the same way on both
    paths. Seeded by the beat's script position (``beat_id`` here,
    ``PlannedBeat.from_index`` on the fast path), which a dropped beat leaves
    unchanged.
    """
    return plan_beat_turns(
        beat.body.get("text", ""),
        config=WeaveConfig.from_snapshot(config),
        supports_speed=model_supports_speed(model_id),
        settings=settings,
        seed_offset=beat_ordinal(beat),
    )


@register_transform(NAME)
class NarrationRenderTTS(BaseTransform):
    """Narrative beat (+ voice-assignment + weave-config) → ``narration-render/v1``."""

    name = NAME
    input_kinds = (NARRATIVE_BEAT_V1, VOICE_ASSIGNMENT_V1, WEAVE_CONFIG_V1)
    output_kind = NARRATION_RENDER_V1

    def plan(
        self, project, inputs: TransformInputs, *, params=None
    ) -> tuple[Plan, tuple[Annotation, ...]]:
        beat = inputs.primary[0]
        cfg = singleton(project, TIER_WEAVE_CONFIG)
        va = child_at_tier(project, TIER_VOICE_ASSIGNMENT, beat.id)

        config = cfg.body.get("config", {})
        voice_id = va.body.get("voice_id") or DEFAULT_VOICE_ID
        model_id = config.get("model_id", DEFAULT_MODEL_ID)
        settings = _beat_settings(beat, config)
        turns = _beat_turns(beat, config, model_id=model_id, settings=settings)
        cache_key = _narration_cache_key(
            self,
            beat.body.get("text", ""),
            voice_id,
            model_id,
            settings,
            turns,
            tts=config.get("tts"),
        )

        full = TransformInputs(
            primary=(beat,),
            context={VOICE_ASSIGNMENT_V1: (va,), WEAVE_CONFIG_V1: (cfg,)},
        )
        skeleton = Annotation(
            id=uuid.uuid4(),
            tier=TIER_NARRATION_RENDER,
            reference=beat.reference,
            body=NarrationRenderBodyV1(cache_key=cache_key).model_dump(),
            body_schema_uri=NARRATION_RENDER_V1,
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
        secrets=None,
    ) -> TransformResult:
        skel = skeleton[0]
        cache_key = skel.body["cache_key"]
        parents = resolve_parents(skel, graph_index(project))
        beat = require_tier(parents, TIER_NARRATIVE_BEAT)
        va = require_tier(parents, TIER_VOICE_ASSIGNMENT)
        cfg = require_tier(parents, TIER_WEAVE_CONFIG)
        config = cfg.body.get("config", {})
        text = beat.body.get("text", "")
        model_id = config.get("model_id", DEFAULT_MODEL_ID)
        tts = config.get("tts")  # a registered backend, else ElevenLabs (#132)
        # ElevenLabs bills per character — the only real spend here. A rate ESTIMATE
        # (== live-call spend; may over-report on a lower-level mixing-cache hit that
        # braidio can't yet detect — thorwhalen/braidio#8).
        cost = backend_cost_usd(text, tts=tts, model_id=model_id)

        if use_cache and not force:
            # Idempotent re-run: the same render over the same, still-fresh
            # inputs is the node already there.
            existing = fresh_equivalent(project, skel)
            if existing is None:
                # The audio is already made under this key, but the node that
                # made it may describe parents since replaced or removed
                # (re-ingest, braidio#51): complete THIS skeleton with its
                # artifact rather than hand the episode a dangling member.
                hit = cached_output(project, TIER_NARRATION_RENDER, cache_key)
                if hit is not None:
                    existing = adopt_output(skel, hit)
                    project.graph.add_annotation(existing)
            if existing is not None:
                # No synthesis: $0 spent, and the estimate is what caching saved
                # — or, unpriced, an unknown amount: say so rather than $0.
                return TransformResult(
                    annotations=(existing,),
                    artifacts=(),
                    cost_usd_actual=0.0,
                    cache_hit_savings_usd=cost if cost is not None else 0.0,
                    has_unknown_costs=cost is None,
                )

        out_path = project.root / "data" / "tts" / f"{skel.id}.mp3"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # was_cached: mixing served this from its on-disk cache (no ElevenLabs call
        # = $0 real spend) even though our graph cache missed — braidio#8.
        voice_id = va.body.get("voice_id") or DEFAULT_VOICE_ID
        settings = _beat_settings(beat, config)
        # The caller's key, if the driver handed one in (nw's `secrets=` seam;
        # thorwhalen/braidio#58) — else None, the process env. Never persisted:
        # not in the node, not in the cache_key.
        api_key = elevenlabs_key(secrets)
        turns = _beat_turns(beat, config, model_id=model_id, settings=settings)
        out_path, was_cached = _speak_beat(
            text,
            out_path,
            tts=tts,
            turns=turns,
            api_key=api_key,
            voice_id=voice_id,
            model_id=model_id,
            settings=settings,
        )
        duration = safe_duration(out_path)
        artifact = audio_artifact(
            out_path,
            transform_name=self.name,
            derived_from=skel.provenance.was_derived_from,
            duration_s=duration,
            cost_usd=cost,
        )
        completed = skel.model_copy(
            update={
                "body": {
                    **skel.body,
                    "artifact_id": artifact.asset_id,
                    "url": file_url(out_path),
                    "duration_s": duration,
                }
            }
        )
        project.graph.add_annotation(completed)
        # A mixing-cache hit cost $0 in reality — report that as actual spend while
        # keeping the estimate on the Artifact (cost_usd) for prediction (braidio#8).
        # Unpriced is honest, not free: nw's cost fields are plain floats, so the
        # unknown rides on ``has_unknown_costs``, the flag every cost gate reads
        # to tell $0 from "we do not know".
        estimate = cost if cost is not None else 0.0
        return TransformResult(
            annotations=(completed,),
            artifacts=(artifact,),
            cost_usd_actual=0.0 if was_cached else estimate,
            cache_hit_savings_usd=estimate if was_cached else 0.0,
            has_unknown_costs=cost is None,
        )
