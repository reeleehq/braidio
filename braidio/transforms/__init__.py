"""braidio's concrete ``nw.Transform`` pipeline — registered on import.

The ``Transform`` *abstraction* lives in ``nw`` (per the federation prime
directive); this package holds braidio's concrete audio-render instances and
registers them with ``nw.transforms`` as a side effect of import. It turns the
authoring graph (narrative beats, dialogue beats, audio clips, a weave-config)
into render-provenance nodes, all written **through** ``project.graph`` so
``nw.stale_after`` drives partial re-render — one freshness engine, not
braidio's parallel standalone ``record_render`` store.

The chain (``sources → segments → weave``):

- ``beat_to_voice_assignment.default`` — narrative beat (+ weave-config) →
  ``voice-assignment/v1`` (deterministic; no synthesis).
- ``narration_render.tts`` — narrative beat (+ voice-assignment + config) →
  ``narration-render/v1`` (ElevenLabs TTS; cached by an explicit ``cache_key``).
- ``dialogue_render.tts`` — dialogue beat (+ the singleton dialogue-cast) →
  ``dialogue-render/v1`` (ElevenLabs Text-to-Dialogue, one pass per exchange;
  cached by an explicit ``cache_key``; thorwhalen/braidio#46).
- ``segment_extraction.ffmpeg`` — audio clip (+ source-media + config) →
  ``segment-extraction/v1`` (ffmpeg cut+pad; cached).
- ``weave_to_episode.default`` — all member renders, plus any scene-break
  nodes (+ config, + the production-structure and render-profile nodes when
  there are any) → one ``episode-render/v1`` (the ``projection_entrypoint`` —
  the delivered mix).

The rights :class:`~braidio.rights.Profile` is applied once, at ingest, by the
same :func:`braidio.rights.plan_production` the no-graph path runs — so a beat
the profile refuses never becomes a node, and no Transform re-decides rights.

:func:`weave_project` is the synchronous convenience driver; the Transforms
are independently registered so nw / reelee can drive them via the
``commentary_weave`` genre (see :mod:`braidio.genre`).
"""

from __future__ import annotations

from nw import TransformInputs

from braidio.transforms._common import ELEVENLABS_SECRET
from braidio.weave_config import WeaveConfig
from braidio.transforms._ingest import ingest_script, IngestedScript
from braidio.transforms import (  # noqa: F401
    _asset_refs,
    _voice,
    _narration,
    _dialogue,
    _segment,
    _episode,
    _video_panels,
    _video_cut,
)
from braidio.transforms._episode import (
    MemberSpan,
    episode_script,
    episode_timeline,
    member_spans,
)
from braidio.transforms._video_panels import (
    Pick,
    as_pick,
    mint_seed,
    panels_for_episode,
    picks_from_panels,
    placement_report,
    tracks_for_episode,
)
from braidio.transforms._video_cut import (
    panel_footage,
    panel_path,
    path_for_panel,
    path_on_canvas,
    resolve_move,
)

# Registered transform names (the genre references these).
VOICE_ASSIGNMENT_TRANSFORM = _voice.NAME
NARRATION_RENDER_TRANSFORM = _narration.NAME
DIALOGUE_RENDER_TRANSFORM = _dialogue.NAME
SEGMENT_EXTRACTION_TRANSFORM = _segment.NAME
EPISODE_TRANSFORM = _episode.NAME
# The picture track (commentary-studio plan §5): free planning, then two
# local-CPU render passes — motion (minutes), then text onto the finished
# motion (seconds) — so a label edit never re-renders a frame.
VIDEO_PANELS_TRANSFORM = _video_panels.NAME
VIDEO_CUT_RENDER_TRANSFORM = _video_cut.RENDER_NAME
VIDEO_CUT_FINISH_TRANSFORM = _video_cut.FINISH_NAME
#: Free, side-effect-free: would a planned video_cut skeleton reuse a cut on
#: disk rather than draw one? (quote a re-make before running it, reelee#480)
video_cut_reuses_existing = _video_cut.reuses_existing_cut

__all__ = [
    "format_config",
    "ingest_script",
    "IngestedScript",
    "weave_project",
    "episode_timeline",
    "member_spans",
    "MemberSpan",
    "episode_script",
    "mint_seed",
    "panels_for_episode",
    "picks_from_panels",
    "placement_report",
    "Pick",
    "as_pick",
    "tracks_for_episode",
    "panel_footage",
    "panel_path",
    "path_for_panel",
    "path_on_canvas",
    "resolve_move",
    "VOICE_ASSIGNMENT_TRANSFORM",
    "NARRATION_RENDER_TRANSFORM",
    "DIALOGUE_RENDER_TRANSFORM",
    "SEGMENT_EXTRACTION_TRANSFORM",
    "EPISODE_TRANSFORM",
    "VIDEO_PANELS_TRANSFORM",
    "VIDEO_CUT_RENDER_TRANSFORM",
    "video_cut_reuses_existing",
    "VIDEO_CUT_FINISH_TRANSFORM",
]


def recorded_config(project) -> dict | None:
    """The weave-config snapshot ``project`` already records, or ``None``."""
    import nw

    nodes = nw.annotations_at_tier(project.root, "weave-configs")
    return dict(nodes[0].body.get("config", {})) if nodes else None


def format_config(fmt=None, *, config=None, voice_id=None, recorded=None):
    """The weave-config a production records: one rule for every writer.

    ``config`` else the format's ``weave``, carrying the narration voice —
    ``voice_id`` else ``fmt.narration_voice`` — when there is one. ``weave_project``
    and ``save_script`` both resolve through here, so saving and then weaving a
    format never rewrites the config node (and so never re-stales its renders).

    The narration model and settings, from the format (braidio#120, #135): a
    format's ``weave`` holds the editing knobs and its ``narration_delivery``
    holds what the narrator speaks with, which is what the fast path renders.
    A project's **first** weave-config takes them from the delivery, so a
    format sounds the same on both paths. A project that already records a
    weave-config (``recorded``, :func:`recorded_config`) keeps the model and
    settings it recorded: switching them would re-synthesize, and re-bill,
    every narration beat on the next weave. An explicit ``config`` is used as
    given.
    """
    if config is None and fmt is not None:
        delivery = fmt.narration_delivery
        source = (
            recorded
            if recorded is not None
            else {
                "model_id": delivery.model_id,
                "voice_settings": dict(delivery.voice_settings),
            }
        )
        config = fmt.weave.with_(
            model_id=source.get("model_id", fmt.weave.model_id),
            voice_settings=source.get("voice_settings", fmt.weave.voice_settings),
        )
    if voice_id is None and fmt is not None:
        voice_id = fmt.narration_voice
    if voice_id is not None:
        config = (config or WeaveConfig()).with_(narration_voice=voice_id)
    return config


def weave_project(
    project,
    script,
    *,
    config=None,
    source=None,
    fmt=None,
    structure=None,
    bed=None,
    profile=None,
    rights=None,
    cast=None,
    voice_id=None,
    api_key=None,
):
    """Ingest ``script`` and run the whole commentary-weave chain, in order.

    Returns the completed ``episode-render/v1`` annotation (its body carries
    the assembled audio's ``url`` + ``artifact_id``). A thin synchronous
    projection over the registered Transforms' ``plan``/``execute`` contract —
    each ``plan`` resolves its own context (config / voice-assignment /
    source-media / production-structure / dialogue-cast) from the graph, so
    the driver only supplies the primary input. A cost-gated / async runner
    (``nw.jobs``, reelee's planner) can drive the same Transforms via the
    genre.

    ``fmt`` (a :class:`~braidio.formats.Format`) supplies the format's declared
    defaults — its ``weave`` config, its ``structure`` and its dialogue
    ``cast`` — for whichever of ``config`` / ``structure`` / ``cast`` the
    caller left out; an explicit argument always wins. ``bed`` is the
    app-supplied music bed (:class:`~braidio.music.MusicBed`). With no format,
    no structure and no bed, this is the plain weave it always was
    (thorwhalen/braidio#39).

    ``cast`` (a :class:`~braidio.conversation.ConversationCast`) is what the
    script's :class:`~braidio.script.Dialogue` beats are voiced with, resolved
    exactly as :func:`braidio.render.render_production` / ``render_format``
    resolve it: explicit, else the format's, else
    :data:`~braidio.conversation.DEFAULT_CAST`. It is recorded as the singleton
    ``dialogue-cast/v1`` node only when the script has a dialogue beat, and
    each ``dialogue-render/v1`` derives from it (thorwhalen/braidio#46).

    ``voice_id`` is the narration voice, resolved as on the fast path: the
    beat's own voice, else ``voice_id``, else the format's ``narration_voice``,
    else the config's pool. It is recorded in the weave-config, so changing it
    re-stales (and re-bills) every narration render that did not name its own
    voice. Left unset it writes nothing (thorwhalen/braidio#115).

    ``profile`` (a :class:`~braidio.rights.Profile`) and ``rights`` (a
    :class:`~braidio.rights.RightsPolicy`) are the rights seam, and they mean
    exactly what they mean on :func:`braidio.render.render_production`: the
    script is filtered through :func:`~braidio.rights.plan_production` at
    ingest, so a segment the profile refuses is never extracted and never
    reaches the mix. ``None`` leaves the profile undeclared, which resolves to
    :data:`~braidio.rights.DEFAULT_PROFILE` — the fast path's default, and the
    behaviour every project had before (thorwhalen/braidio#47).

    **Re-running on the same project is the way to change any of this.**
    :func:`ingest_script` reconciles the graph by identity (the singleton
    tiers by tier, beats by script position): an unchanged beat keeps its node
    and its render is a cache hit, a changed config / profile / structure is
    rewritten under its existing id so every render that derived from it reads
    stale through provenance, and a beat the new profile refuses is removed.
    The weave is idempotent too: a render whose inputs are unchanged and still
    fresh is reused rather than duplicated, and a re-run with nothing changed
    returns the episode already there and writes nothing. After a real change
    the previous episode stays in the graph as history, stale; the returned
    one is current (thorwhalen/braidio#51).

    ``api_key`` is the caller's ElevenLabs key, and means exactly what it
    means on :func:`braidio.render.render_production`: ``None`` (default)
    resolves from the process environment. It reaches the two synthesis
    Transforms through nw's ``execute(secrets=)`` seam — as
    ``{"elevenlabs": api_key}``, to those two and no other — and nothing in
    the graph: not a node, not provenance, not a ``cache_key``
    (thorwhalen/braidio#58).
    """
    import nw

    config = format_config(
        fmt, config=config, voice_id=voice_id, recorded=recorded_config(project)
    )
    if fmt is not None:
        structure = structure if structure is not None else fmt.structure
        cast = cast if cast is not None else fmt.cast

    ing = ingest_script(
        project,
        script,
        config=config,
        source=source,
        structure=structure,
        bed=bed,
        profile=profile,
        rights=rights,
        cast=cast,
    )
    voice = nw.get_transform(VOICE_ASSIGNMENT_TRANSFORM)
    narration = nw.get_transform(NARRATION_RENDER_TRANSFORM)
    dialogue = nw.get_transform(DIALOGUE_RENDER_TRANSFORM)
    segment = nw.get_transform(SEGMENT_EXTRACTION_TRANSFORM)
    episode = nw.get_transform(EPISODE_TRANSFORM)

    # The one place the key becomes a Secrets, handed only to the Transforms
    # that spend it; the free ones (voice, segment, episode) never see it.
    secrets = nw.as_secrets({ELEVENLABS_SECRET: api_key})

    render_by_authoring_id = {}
    for beat in ing.narration_beats:
        _run(voice, project, beat)
        render_by_authoring_id[beat.id] = _run(
            narration, project, beat, secrets=secrets
        )
    for beat in ing.dialogue_beats:
        render_by_authoring_id[beat.id] = _run(dialogue, project, beat, secrets=secrets)
    for clip in ing.audio_clips:
        render_by_authoring_id[clip.id] = _run(segment, project, clip)

    # A scene break renders no node of its own — the authoring annotation IS
    # the member, and the episode transform turns it into a sting or a pause.
    members = tuple(
        auth if kind == "scene_break" else render_by_authoring_id[auth.id]
        for kind, auth in ing.ordered
    )
    return _run(episode, project, *members)


def _run(transform, project, *primary, secrets=None):
    """``plan`` then ``execute`` one transform over ``primary``; return its output.

    ``secrets`` is offered only when there is one — the Transform's ``execute``
    then declares the keyword or it is a caller bug worth a ``TypeError``.
    """
    inputs = TransformInputs(primary=tuple(primary))
    plan, skeleton = transform.plan(project, inputs)
    kwargs = {} if secrets is None else {"secrets": secrets}
    result = transform.execute(project, plan, skeleton, **kwargs)
    return result.annotations[0]
