"""``dialogue_render.tts`` — synthesize one dialogue beat to audio, in one pass.

The dialogue counterpart of :mod:`braidio.transforms._narration`: a
**local-render** Transform (no fal call) whose ``plan`` returns a zero-call
``Plan`` + a ``dialogue-render/v1`` skeleton carrying a ``cache_key`` over the
audio-affecting inputs, and whose ``execute`` synthesizes via
``braidio.render_dialogue`` (ElevenLabs Text-to-Dialogue under a
:class:`~braidio.conversation.ConversationCast`) unless an identical
``cache_key`` already has an artifact (compare-and-skip). Never
``render_multivoice``: an exchange is rendered together so prosody is
conditioned across turns — the whole point of the register.

It derives from ``[dialogue-beat, dialogue-cast]`` and nothing else. The
cast is the production's singleton ``dialogue-cast/v1`` node (upserted by
identity at ingest, thorwhalen/braidio#51), so recasting rewrites that node
under its id and every dialogue render reads ``upstream-changed`` — while
the narration renders, extractions and the voice assignments, which do not
derive from it, stay fresh (thorwhalen/braidio#46). The weave-config is
deliberately *not* a parent: nothing in it reaches a dialogue take.

Text-to-Dialogue is billed per character like any other synthesis, so cost
is :func:`braidio.cost.tts_cost_usd` over the joined turn text under the
cast's model: a rate estimate on a live call, ``0.0`` actual on a graph or
disk cache hit (with the estimate reported as the saving), and ``None`` when
the rate is unpriced — never a fake zero.

A caller's ElevenLabs key reaches the synthesis exactly as it does in
:mod:`braidio.transforms._narration`: through ``execute(secrets=)`` as
``secrets["elevenlabs"]``, never through the graph (thorwhalen/braidio#58).
"""

from __future__ import annotations

import json
import shutil
import uuid

from falaw import Plan
from lacing import Annotation
from nw import BaseTransform, TransformInputs, TransformResult, register_transform
from nw.transforms._provenance import derive_provenance

from braidio.bodies._domain import DIALOGUE_BEAT_V1
from braidio.bodies._render_nodes import (
    DIALOGUE_CAST_V1,
    DIALOGUE_RENDER_V1,
    DialogueRenderBodyV1,
)
from braidio.conversation import ConversationCast
from braidio.cost import tts_cost_usd
from braidio.tts import narrator_from_speak, tts_backend
from braidio.transforms._common import (
    TIER_DIALOGUE_BEAT,
    TIER_DIALOGUE_CAST,
    TIER_DIALOGUE_RENDER,
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

NAME = "dialogue_render.tts"


def _turns(beat: Annotation) -> list[tuple[str, str]]:
    """The beat's ``(role, text)`` pairs, as ``render_dialogue`` takes them."""
    return [(str(role), str(text)) for role, text in beat.body.get("turns", ())]


def _cast(node: Annotation) -> ConversationCast:
    """The :class:`ConversationCast` a ``dialogue-cast/v1`` node records."""
    body = node.body
    return ConversationCast(
        roles=dict(body["roles"]),
        model_id=str(body["model_id"]),
        settings=body.get("settings"),
    )


def _billable_text(turns) -> str:
    """The text ElevenLabs bills for — every turn's text, joined (as
    :func:`braidio.cost.estimate_cost` counts a dialogue beat)."""
    return "".join(text for _role, text in turns)


def _dialogue_cache_key(
    transform, turns, cast: ConversationCast, *, tts: str | None = None
) -> str:
    """The output identity for one dialogue take — nw's shared derivation.

    Hashes everything that reaches the audio: the turns (role + text, in
    order), the role → voice map, the model and its settings. Same
    ``nw.transforms.cache_key`` as the narration and extraction transforms,
    with a ``"dialogue"`` tag so the three can never collide (nw#54). A
    registered voice backend ``tts`` (braidio#132) is folded in only when set,
    so an ElevenLabs project keeps its keys.
    """
    from nw.transforms import cache_key as transform_cache_key

    parts = [
        "dialogue",
        json.dumps(list(turns), ensure_ascii=False),
        json.dumps(dict(cast.roles), sort_keys=True),
        cast.model_id,
        json.dumps(cast.settings or {}, sort_keys=True),
    ]
    if tts:
        parts.append(f"tts={tts}")
    return transform_cache_key(transform, *parts)


@register_transform(NAME)
class DialogueRenderTTS(BaseTransform):
    """Dialogue beat (+ dialogue-cast) → ``dialogue-render/v1``."""

    name = NAME
    input_kinds = (DIALOGUE_BEAT_V1, DIALOGUE_CAST_V1)
    output_kind = DIALOGUE_RENDER_V1

    def plan(
        self, project, inputs: TransformInputs, *, params=None
    ) -> tuple[Plan, tuple[Annotation, ...]]:
        beat = inputs.primary[0]
        cast_node = singleton(project, TIER_DIALOGUE_CAST)
        cache_key = _dialogue_cache_key(
            self, _turns(beat), _cast(cast_node), tts=cast_node.body.get("tts")
        )

        full = TransformInputs(
            primary=(beat,), context={DIALOGUE_CAST_V1: (cast_node,)}
        )
        skeleton = Annotation(
            id=uuid.uuid4(),
            tier=TIER_DIALOGUE_RENDER,
            reference=beat.reference,
            body=DialogueRenderBodyV1(cache_key=cache_key).model_dump(),
            body_schema_uri=DIALOGUE_RENDER_V1,
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
        import braidio  # runtime attr access so tests can monkeypatch render_dialogue

        skel = skeleton[0]
        cache_key = skel.body["cache_key"]
        parents = resolve_parents(skel, graph_index(project))
        beat = require_tier(parents, TIER_DIALOGUE_BEAT)
        cast_node = require_tier(parents, TIER_DIALOGUE_CAST)
        cast, tts = _cast(cast_node), cast_node.body.get("tts")
        turns = _turns(beat)
        # ElevenLabs bills per character of the submitted turns — the only real
        # spend here. A rate ESTIMATE (== live-call spend; see braidio#8). A
        # registered backend reports its own rate, or none (unpriced).
        billable = _billable_text(turns)
        cost = (
            tts_backend(tts).cost_usd(billable)
            if tts
            else tts_cost_usd(billable, model_id=cast.model_id)
        )

        if use_cache and not force:
            # Same two-step as narration_render: the fresh equivalent node if
            # there is one, else THIS skeleton completed with the cache-keyed
            # artifact — never a node whose parents a re-ingest replaced or
            # removed (braidio#51).
            existing = fresh_equivalent(project, skel)
            if existing is None:
                hit = cached_output(project, TIER_DIALOGUE_RENDER, cache_key)
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

        out_path = project.root / "data" / "dialogue" / f"{skel.id}.mp3"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # was_cached: the take came from the on-disk dialogue cache (no
        # ElevenLabs call = $0 real spend) even though our graph cache missed.
        if tts:
            # another backend has no one-pass dialogue: turn by turn, never
            # cached (the fast path's fallback, braidio#123)
            braidio.render_turns_sequential(
                turns,
                cast,
                out_path=out_path,
                work_dir=out_path.with_suffix(""),
                narrate_fn=narrator_from_speak(tts_backend(tts).speak),
            )
            shutil.rmtree(out_path.with_suffix(""), ignore_errors=True)
            was_cached = False
        else:
            _, was_cached = braidio.render_dialogue(
                turns,
                cast,
                # The caller's key, if the driver handed one in (nw's `secrets=`
                # seam; thorwhalen/braidio#58) — else None, the process env.
                # Never persisted: not in the node, not in the cache_key.
                api_key=elevenlabs_key(secrets),
                out_path=out_path,
                return_cache_status=True,
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
        # Unpriced is honest, not free: nw's cost fields are plain floats, so the
        # unknown rides on ``has_unknown_costs`` — the flag every cost gate in
        # the federation reads to tell $0 from "we do not know".
        estimate = cost if cost is not None else 0.0
        return TransformResult(
            annotations=(completed,),
            artifacts=(artifact,),
            cost_usd_actual=0.0 if was_cached else estimate,
            cache_hit_savings_usd=estimate if was_cached else 0.0,
            has_unknown_costs=cost is None,
        )
