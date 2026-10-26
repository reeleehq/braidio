"""Stability guard for the 21 body schemas braidio registers with lacing.

These URIs are *on the wire*. The graph path (nw pipeline + the two deployed
MCP connectors) reads and writes annotations carrying them, and real projects
hold serialized copies of those bodies. So unlike most of this package — where
clean shape beats backward compatibility — a change here is a federation
event that needs a lacing migration. See the "Body schemas are a federation
contract" section of this repo's CLAUDE.md.

This file pins two things per body:

- the **URI string**, because the URI is the contract; and
- the **serialized shape**: which fields exist, which are required, and each
  field's JSON type / constraints / default.

The shapes are read out of ``model_json_schema()`` (which pydantic emits
by-alias, so the pinned names are the names that land in the annotation body)
and rendered as short, readable expressions — ``"string|null = null"``,
``"array<string>"``, ``"tuple<number,number>|null = null"``. A diff on a
failing pin therefore says *what* changed, which a checksum could not. The
table below was generated once from the current models and committed as
literals — it does not derive from the models it is meant to protect.

Breaking and additive changes fail in different tests, with different advice:

- :func:`test_pinned_fields_are_unchanged` fires on a rename, a removal, a
  type change, a constraint change, or a default change — all breaking.
  Reordering field declarations is not one: key order never reaches the wire.
- :func:`test_new_fields_are_additive_and_pinned` fires on a field that
  exists but isn't pinned, and tells you whether it is additive (optional,
  with a default) or breaking (required).

Two of the 21 bodies (``still/v1``, ``video-panel/v1``) nest a carrier
model, ``RectV1`` (a normalized ``x``/``y``/``w``/``h`` region — the same keys
as ``burns.Rect`` and a ``BurnsPath`` keyframe rect). It is not a registered
body, so it has no URI, but its shape IS on the wire inside those two bodies,
so it is pinned in ``PINNED_NESTED`` the way artful pins ``PanelImage``.
``video-panel/v1`` also nests ``FootageRefV1`` (the recorded video a footage
panel plays), pinned the same way. Every
other field is a scalar, an enum, a homogeneous ``array``/``tuple``, or an
open ``object``.
"""

from __future__ import annotations

import json

import pytest

from lacing.schema import get_body_schema, registered_uris

from braidio.bodies import (
    AUDIO_CLIP_V1,
    COMMENTARY_V1,
    DIALOGUE_BEAT_V1,
    DIALOGUE_CAST_V1,
    DIALOGUE_RENDER_V1,
    EPISODE_RENDER_V1,
    EPISODE_V1,
    LABEL_TRACK_V1,
    NARRATION_RENDER_V1,
    NARRATIVE_BEAT_V1,
    PRODUCTION_STRUCTURE_V1,
    RENDER_PROFILE_V1,
    SCENE_BREAK_V1,
    SEGMENT_EXTRACTION_V1,
    SOURCE_MEDIA_V1,
    SOURCE_V1,
    STILL_V1,
    VIDEO_CUT_V1,
    VIDEO_PANEL_V1,
    VOICE_ASSIGNMENT_V1,
    WEAVE_CONFIG_V1,
    AudioClipBodyV1,
    CommentaryBodyV1,
    DialogueBeatBodyV1,
    DialogueCastBodyV1,
    DialogueRenderBodyV1,
    EpisodeBodyV1,
    EpisodeRenderBodyV1,
    LabelTrackBodyV1,
    NarrationRenderBodyV1,
    NarrativeBeatBodyV1,
    ProductionStructureBodyV1,
    FootageRefV1,
    RectV1,
    RenderProfileBodyV1,
    SceneBreakBodyV1,
    SegmentExtractionBodyV1,
    SourceBodyV1,
    SourceMediaBodyV1,
    StillBodyV1,
    VideoCutBodyV1,
    VideoPanelBodyV1,
    VoiceAssignmentBodyV1,
    WeaveConfigBodyV1,
)


MIGRATION_RULE = (
    "\n"
    "FEDERATION-VISIBLE SCHEMA CHANGE.\n"
    "braidio's body schemas carry real graph data — the nw pipeline and the "
    "two deployed MCP connectors read and write them, and stored projects "
    "already hold them. Renaming a URI, renaming or removing a field, or "
    "changing a field's serialized type, constraint or default BREAKS every "
    "stored annotation and every downstream round-trip. It is not a rename "
    "you can land in one pass — it needs a new schema version plus a "
    "`lacing.register_migration` from the old one, and the downstream "
    "packages (nw, the connectors) updated with it.\n"
    "See the 'Body schemas are a federation contract' section of this repo's "
    "CLAUDE.md.\n"
    "Only after the migration exists should the pin below be updated — in "
    "the same commit as the migration."
)


# --- the URIs ----------------------------------------------------------------


def test_body_schema_uris_are_pinned():
    """The URI *is* the contract — stored annotations name it by string."""
    actual = {
        "COMMENTARY_V1": COMMENTARY_V1,
        "SOURCE_V1": SOURCE_V1,
        "AUDIO_CLIP_V1": AUDIO_CLIP_V1,
        "NARRATIVE_BEAT_V1": NARRATIVE_BEAT_V1,
        "DIALOGUE_BEAT_V1": DIALOGUE_BEAT_V1,
        "SCENE_BREAK_V1": SCENE_BREAK_V1,
        "EPISODE_V1": EPISODE_V1,
        "WEAVE_CONFIG_V1": WEAVE_CONFIG_V1,
        "PRODUCTION_STRUCTURE_V1": PRODUCTION_STRUCTURE_V1,
        "RENDER_PROFILE_V1": RENDER_PROFILE_V1,
        "DIALOGUE_CAST_V1": DIALOGUE_CAST_V1,
        "SOURCE_MEDIA_V1": SOURCE_MEDIA_V1,
        "VOICE_ASSIGNMENT_V1": VOICE_ASSIGNMENT_V1,
        "NARRATION_RENDER_V1": NARRATION_RENDER_V1,
        "DIALOGUE_RENDER_V1": DIALOGUE_RENDER_V1,
        "SEGMENT_EXTRACTION_V1": SEGMENT_EXTRACTION_V1,
        "EPISODE_RENDER_V1": EPISODE_RENDER_V1,
        "STILL_V1": STILL_V1,
        "VIDEO_PANEL_V1": VIDEO_PANEL_V1,
        "VIDEO_CUT_V1": VIDEO_CUT_V1,
        "LABEL_TRACK_V1": LABEL_TRACK_V1,
    }
    assert actual == {
        "COMMENTARY_V1": "annot://schema/commentary/v1",
        "SOURCE_V1": "annot://schema/source/v1",
        "AUDIO_CLIP_V1": "annot://schema/audio-clip/v1",
        "NARRATIVE_BEAT_V1": "annot://schema/narrative-beat/v1",
        "DIALOGUE_BEAT_V1": "annot://schema/dialogue-beat/v1",
        "SCENE_BREAK_V1": "annot://schema/scene-break/v1",
        "EPISODE_V1": "annot://schema/episode/v1",
        "WEAVE_CONFIG_V1": "annot://schema/weave-config/v1",
        "PRODUCTION_STRUCTURE_V1": "annot://schema/production-structure/v1",
        "RENDER_PROFILE_V1": "annot://schema/render-profile/v1",
        "DIALOGUE_CAST_V1": "annot://schema/dialogue-cast/v1",
        "SOURCE_MEDIA_V1": "annot://schema/source-media/v1",
        "VOICE_ASSIGNMENT_V1": "annot://schema/voice-assignment/v1",
        "NARRATION_RENDER_V1": "annot://schema/narration-render/v1",
        "DIALOGUE_RENDER_V1": "annot://schema/dialogue-render/v1",
        "SEGMENT_EXTRACTION_V1": "annot://schema/segment-extraction/v1",
        "EPISODE_RENDER_V1": "annot://schema/episode-render/v1",
        "STILL_V1": "annot://schema/still/v1",
        "VIDEO_PANEL_V1": "annot://schema/video-panel/v1",
        "VIDEO_CUT_V1": "annot://schema/video-cut/v1",
        "LABEL_TRACK_V1": "annot://schema/label-track/v1",
    }, MIGRATION_RULE


#: URI → the model importing ``braidio.bodies`` registers against it.
OWNED: dict[str, type] = {
    COMMENTARY_V1: CommentaryBodyV1,
    SOURCE_V1: SourceBodyV1,
    AUDIO_CLIP_V1: AudioClipBodyV1,
    NARRATIVE_BEAT_V1: NarrativeBeatBodyV1,
    DIALOGUE_BEAT_V1: DialogueBeatBodyV1,
    SCENE_BREAK_V1: SceneBreakBodyV1,
    EPISODE_V1: EpisodeBodyV1,
    WEAVE_CONFIG_V1: WeaveConfigBodyV1,
    PRODUCTION_STRUCTURE_V1: ProductionStructureBodyV1,
    RENDER_PROFILE_V1: RenderProfileBodyV1,
    DIALOGUE_CAST_V1: DialogueCastBodyV1,
    SOURCE_MEDIA_V1: SourceMediaBodyV1,
    VOICE_ASSIGNMENT_V1: VoiceAssignmentBodyV1,
    NARRATION_RENDER_V1: NarrationRenderBodyV1,
    DIALOGUE_RENDER_V1: DialogueRenderBodyV1,
    SEGMENT_EXTRACTION_V1: SegmentExtractionBodyV1,
    EPISODE_RENDER_V1: EpisodeRenderBodyV1,
    STILL_V1: StillBodyV1,
    VIDEO_PANEL_V1: VideoPanelBodyV1,
    VIDEO_CUT_V1: VideoCutBodyV1,
    LABEL_TRACK_V1: LabelTrackBodyV1,
}

#: Carrier models nested inside an owned body (no URI of their own, but on
#: the wire all the same). Pinned by name; ``_nested_shapes`` reads them from
#: the owning bodies' ``$defs`` so a carrier that stops being referenced is
#: noticed too.
NESTED: dict[str, type] = {"FootageRefV1": FootageRefV1, "RectV1": RectV1}


@pytest.mark.parametrize("uri", sorted(OWNED))
def test_uri_resolves_to_its_pinned_model(uri):
    assert get_body_schema(uri) is OWNED[uri], MIGRATION_RULE


def test_braidio_owns_exactly_these_twenty_one_body_schemas():
    """A twenty-second braidio-owned schema must be pinned here too, or it ships
    unguarded. (Filtered to braidio's own models: the lacing registry is
    global and other packages register into it as well.)"""
    owned = {
        uri
        for uri in registered_uris()
        if get_body_schema(uri).__module__.split(".")[0] == "braidio"
    }
    assert owned == set(OWNED), (
        "braidio registers a body schema this guard does not pin. Add it to "
        "OWNED and PINNED below." + MIGRATION_RULE
    )


@pytest.mark.parametrize("uri", sorted(OWNED))
def test_bodies_forbid_extra_fields(uri):
    """``extra="forbid"`` is part of the contract: a caller-supplied unknown
    key must raise rather than being silently dropped from a stored body."""
    assert OWNED[uri].model_json_schema()["additionalProperties"] is False


# --- the serialized shapes ---------------------------------------------------

#: Model name → its pinned ``required`` field names and per-field shapes.
#: Generated once from the current models (two of the 21 bodies nest
#: another registered model, ``RectV1`` — see the ``PINNED_NESTED`` note
#: in the module docstring — otherwise there is exactly one entry per
#: body — no full nested-model table like artful's
#: ``PanelImage``/``ShotEntry``).
#: ``required`` is a *set*: JSON object key order is not part of the
#: contract, so moving a field declaration is invisible on the wire and must
#: not fire the federation alarm. Adding, dropping or renaming a required
#: name still does.
PINNED: dict[str, dict] = {
    "CommentaryBodyV1": {
        "required": frozenset({"facet", "generated_by", "text"}),
        "fields": {
            "facet": "enum[biographical|historical|musical|production]",
            "generated_by": "string",
            "source_ids": "array<string>",
            "text": "string",
        },
    },
    "SourceBodyV1": {
        "required": frozenset({"citation", "kind", "public_domain", "title"}),
        "fields": {
            "citation": "string",
            "excerpt": "string|null = null",
            "kind": "string",
            "public_domain": "boolean",
            "title": "string",
            "url": 'string = ""',
        },
    },
    "AudioClipBodyV1": {
        "required": frozenset({"label", "rights", "source_node_id"}),
        "fields": {
            "beat_id": "string|null = null",
            "fade": "tuple<number,number>|null = null",
            "gain_db": "number|null = null",
            "label": "string",
            "rights": "enum[copyrighted|owned-local|public-domain]",
            "source_node_id": "string",
            "spotlight": "boolean|null = null",
        },
    },
    "NarrativeBeatBodyV1": {
        "required": frozenset({"beat_id", "text"}),
        "fields": {
            "beat_id": "string",
            "draws_on": "array<string>",
            "plays_clip": "string|null = null",
            "style": "string|null = null",
            "text": "string",
            "voice": "string|null = null",
            "voice_settings": "object<string,any>|null = null",
        },
    },
    "DialogueBeatBodyV1": {
        "required": frozenset({"beat_id", "turns"}),
        "fields": {
            "beat_id": "string",
            "label": 'string = ""',
            "turns": "array<tuple<string,string>>",
        },
    },
    "SceneBreakBodyV1": {
        "required": frozenset({"beat_id"}),
        "fields": {
            "beat_id": "string",
            "label": 'string = ""',
            "marker": "string|null = null",
        },
    },
    "EpisodeBodyV1": {
        "required": frozenset({"title"}),
        "fields": {
            "ordered_member_ids": "array<string>",
            "title": "string",
        },
    },
    "WeaveConfigBodyV1": {
        "required": frozenset({"config"}),
        "fields": {
            "config": "object<string,any>",
        },
    },
    "ProductionStructureBodyV1": {
        "required": frozenset({"structure"}),
        "fields": {
            "bed": "object<string,any>|null = null",
            "bed_asset_id": "string|null = null",
            "bed_url": "string|null = null",
            "sting": "object<string,any>|null = null",
            "sting_asset_id": "string|null = null",
            "sting_url": "string|null = null",
            "structure": "object<string,any>",
        },
    },
    "RenderProfileBodyV1": {
        "required": frozenset({"profile"}),
        "fields": {
            "dropped": "array<string>",
            "profile": "string",
            "publishable_clip_rights": "array<string>",
            "substituted": "array<string>",
        },
    },
    "DialogueCastBodyV1": {
        "required": frozenset({"model_id", "roles"}),
        "fields": {
            "model_id": "string",
            "roles": "object<string,string>",
            "settings": "object<string,any>|null = null",
        },
    },
    "SourceMediaBodyV1": {
        "required": frozenset({"asset_id", "label"}),
        "fields": {
            "beat_id": "string|null = null",
            "asset_id": "string",
            "label": "string",
            "rights": 'string = "owned-local"',
        },
    },
    "VoiceAssignmentBodyV1": {
        "required": frozenset({"voice_id"}),
        "fields": {
            "pool_label": 'string = "single"',
            "seed": "integer = 0",
            "voice_id": "string",
        },
    },
    "NarrationRenderBodyV1": {
        "required": frozenset({"cache_key"}),
        "fields": {
            "artifact_id": "string|null = null",
            "cache_key": "string",
            "duration_s": "number = 0.0",
            # additive (commentary-studio plan §3): an uploaded take vs TTS
            "source": 'string = "tts"',
            "url": "string|null = null",
        },
    },
    "DialogueRenderBodyV1": {
        "required": frozenset({"cache_key"}),
        "fields": {
            "artifact_id": "string|null = null",
            "cache_key": "string",
            "duration_s": "number = 0.0",
            "url": "string|null = null",
        },
    },
    "SegmentExtractionBodyV1": {
        "required": frozenset({"cache_key", "end_s", "start_s"}),
        "fields": {
            "artifact_id": "string|null = null",
            "cache_key": "string",
            "end_s": "number",
            # additive: start_s/end_s are required floats with no null, so a
            # clip whose source span was never persisted needs a bool to say
            # so rather than an invented (0.0, duration).
            "source_span_recorded": "boolean = true",
            "start_s": "number",
            "url": "string|null = null",
        },
    },
    "EpisodeRenderBodyV1": {
        "required": frozenset({"profile"}),
        "fields": {
            "artifact_id": "string|null = null",
            "duration_s": "number = 0.0",
            "ordered_member_ids": "array<string>",
            "profile": "string",
            # additive (commentary-studio plan §3): the persisted cut points
            "timeline": "object<string,any>|null = null",
            "url": "string|null = null",
        },
    },
    # --- the picture track (commentary-studio plan §3) ---
    "StillBodyV1": {
        "required": frozenset({"artifact_id", "key", "labelled"}),
        "fields": {
            "about": "string|null = null",
            "artifact_id": "string",
            "attribution": "string|null = null",
            "author": "string|null = null",
            "author_url": "string|null = null",
            "cacheable": "boolean|null = null",
            "crop": "RectV1|null = null",
            "height": "integer(minimum=1)|null = null",
            "key": "string",
            "labelled": "boolean",
            "license": "string|null = null",
            "license_label": "string|null = null",
            "license_url": "string|null = null",
            "note": "string|null = null",
            "source_page_url": "string|null = null",
            "subject": "string|null = null",
            "title": "string|null = null",
            "url": "string|null = null",
            "width": "integer(minimum=1)|null = null",
        },
    },
    "VideoPanelBodyV1": {
        "required": frozenset({"order", "seed", "still_id", "track_id"}),
        "fields": {
            "anchor_text": "string|null = null",
            "beat_id": "string|null = null",
            "disclaimed": "boolean = false",
            "focus": "RectV1|null = null",
            "footage": "FootageRefV1|null = null",
            "rationale": "string|null = null",
            "relevance": "number(maximum=1.0, minimum=0.0)|null = null",
            "role": "enum[contextual|decorative|literal]|null = null",
            "scorer": "string|null = null",
            "move": 'string = "auto"',
            "order": "integer(minimum=0)",
            "path": "object<string,any>|null = null",
            "seed": "integer",
            "still_id": "string",
            "track_id": "string",
            "zoom": "number(exclusiveMinimum=0.0) = 1.18",
        },
    },
    "VideoCutBodyV1": {
        "required": frozenset({"audio_artifact_id", "label", "profile", "stage"}),
        "fields": {
            "artifact_id": "string|null = null",
            "audio_artifact_id": "string",
            "cache_key": "string|null = null",
            "captions_artifact_id": "string|null = null",
            "duration_s": "number(minimum=0.0) = 0.0",
            "fps": "integer(minimum=1)|null = null",
            "height": "integer(minimum=1)|null = null",
            "label": "string",
            "panel_ids": "array<string>",
            "profile": "string",
            "published": "object<string,any>|null = null",
            "settings": "object<string,any>",
            "stage": "string",
            "url": "string|null = null",
            "width": "integer(minimum=1)|null = null",
        },
    },
    "LabelTrackBodyV1": {
        "required": frozenset({"kind"}),
        "fields": {
            "headline": "string|null = null",
            "kind": "string",
            "lines": "array<string>",
            "slot": "string|null = null",
            "weight": "integer(minimum=1) = 2",
        },
    },
}

#: The nested carriers' shapes — same rules, same advice.
PINNED_NESTED: dict[str, dict] = {
    "FootageRefV1": {
        "required": frozenset({"artifact_id"}),
        "fields": {
            "artifact_id": "string",
            "in_s": "number(minimum=0.0) = 0.0",
            "url": "string|null = null",
        },
    },
    "RectV1": {
        "required": frozenset({"h", "w", "x", "y"}),
        "fields": {
            "h": "number(exclusiveMinimum=0.0, maximum=1.0)",
            "w": "number(exclusiveMinimum=0.0, maximum=1.0)",
            "x": "number(maximum=1.0, minimum=0.0)",
            "y": "number(maximum=1.0, minimum=0.0)",
        },
    },
}


def test_every_nested_carrier_is_pinned_and_referenced():
    """A nested model on the wire is pinned like a body; one no body references
    any more is a stale pin (and a silent wire change if it was renamed)."""
    assert set(PINNED_NESTED) == set(NESTED) == set(_nested_shapes()), MIGRATION_RULE


@pytest.mark.parametrize("model_name", sorted(PINNED_NESTED))
def test_nested_carrier_shape_is_unchanged(model_name):
    actual = _nested_shapes()[model_name]
    assert actual == PINNED_NESTED[model_name], (
        f"{model_name} (nested in a body): a field was added, renamed, removed, "
        f"retyped or re-constrained." + MIGRATION_RULE
    )


def test_every_owned_body_is_pinned():
    """The completeness guard the two shape tests below need: they parametrise
    over ``PINNED``, so a body that is registered but *not* pinned would never
    be examined and would ship unguarded (#57 review). ``OWNED`` alone does not
    catch it — a URI can be owned and its shape unpinned."""
    assert set(PINNED) == set(_actual_shapes()), (
        "every owned body must have a PINNED entry (and every PINNED entry a "
        "body); add or drop the pin in the same commit as the model." + MIGRATION_RULE
    )


@pytest.mark.parametrize("model_name", sorted(PINNED))
def test_pinned_fields_are_unchanged(model_name):
    """Every pinned field still exists, with the same serialized shape."""
    pinned = PINNED[model_name]
    actual = _actual_shapes()[model_name]
    still_there = {
        name: shape
        for name, shape in actual["fields"].items()
        if name in pinned["fields"]
    }
    assert still_there == pinned["fields"], (
        f"{model_name}: a pinned field was renamed, removed, retyped, "
        f"re-constrained or re-defaulted." + MIGRATION_RULE
    )
    assert actual["required"] == pinned["required"], (
        f"{model_name}: the set of REQUIRED fields changed. Making a field "
        f"required rejects every stored body without it; making one optional "
        f"lets a downstream writer omit it." + MIGRATION_RULE
    )


@pytest.mark.parametrize("model_name", sorted(PINNED))
def test_new_fields_are_additive_and_pinned(model_name):
    """A field that exists but isn't pinned — additive, or breaking?"""
    pinned = PINNED[model_name]
    actual = _actual_shapes()[model_name]
    unpinned = {
        name: shape
        for name, shape in actual["fields"].items()
        if name not in pinned["fields"]
    }
    if not unpinned:
        return
    breaking = sorted(n for n in unpinned if n in actual["required"])
    if breaking:
        pytest.fail(
            f"{model_name}: new REQUIRED field(s) {breaking} — every stored "
            f"body predates them and will now fail validation." + MIGRATION_RULE
        )
    missing = sorted(set(pinned["fields"]) - set(actual["fields"]))
    rename_note = (
        "\nA pinned field also disappeared "
        f"({missing}): if this new field replaces it, that is a RENAME, not "
        "an addition — see test_pinned_fields_are_unchanged's advice above, "
        "not this one."
        if missing
        else ""
    )
    pytest.fail(
        f"{model_name}: new optional field(s) {sorted(unpinned)}. That is an "
        f"ADDITIVE change — old bodies still load, no migration needed — but "
        f"the pin has to record it or this guard silently stops covering the "
        f"body. Add to PINNED[{model_name!r}]['fields'] in this same PR:\n  "
        + "\n  ".join(f"{n!r}: {s!r}," for n, s in sorted(unpinned.items()))
        + rename_note
    )


# --- shape rendering ----------------------------------------------------------
#
# Reduce one JSON-Schema property to a short expression a human can diff.
# Anything pydantic emits that isn't recognised below is appended verbatim as
# ``key=value`` rather than dropped, so an unanticipated keyword still shows
# up in the failure diff instead of slipping through unpinned.

#: Prose, not contract — a reworded docstring must not fail the guard.
PROSE_KEYS = frozenset({"title", "description"})


def _actual_shapes() -> dict[str, dict]:
    """``{model name: {"required": frozenset(...), "fields": {name: shape}}}``
    for the 21 owned bodies (top level only; nested carriers are
    :func:`_nested_shapes`)."""
    return {
        js["title"]: _entry(js)
        for js in (m.model_json_schema() for m in OWNED.values())
    }


def _nested_shapes() -> dict[str, dict]:
    """The carriers the owned bodies reference through ``$defs``, by name."""
    out: dict[str, dict] = {}
    for m in OWNED.values():
        for name, js in m.model_json_schema().get("$defs", {}).items():
            out[name] = _entry(js)
    return out


def _entry(js: dict) -> dict:
    # ``required`` is a set, not the declaration-ordered list pydantic emits:
    # key order does not reach the wire, so reordering fields is not a change.
    return {
        "required": frozenset(js.get("required", ())),
        "fields": {name: _shape(prop) for name, prop in js["properties"].items()},
    }


def _shape(prop: dict) -> str:
    prop = {k: v for k, v in prop.items() if k not in PROSE_KEYS}
    has_default = "default" in prop
    default = prop.pop("default", None)
    expr = _type_expr(prop)
    return f"{expr} = {json.dumps(default, sort_keys=True)}" if has_default else expr


def _type_expr(prop: dict) -> str:
    prop = {k: v for k, v in prop.items() if k not in PROSE_KEYS}
    if "anyOf" in prop:
        rest = {k: v for k, v in prop.items() if k != "anyOf"}
        return _with_extras("|".join(_type_expr(s) for s in prop["anyOf"]), rest)
    if "$ref" in prop:
        return prop["$ref"].rsplit("/", 1)[-1]
    rest = dict(prop)
    if "enum" in rest:
        members = "|".join(sorted(rest.pop("enum")))
        rest.pop("type", None)
        return _with_extras(f"enum[{members}]", rest)
    if "prefixItems" in rest:
        items = rest.pop("prefixItems")
        rest.pop("minItems", None)
        rest.pop("maxItems", None)
        rest.pop("items", None)
        rest.pop("type", None)
        inner = ",".join(_type_expr(item) for item in items)
        return _with_extras(f"tuple<{inner}>", rest)
    kind = rest.pop("type", "any")
    if kind == "array":
        return _with_extras(f"array<{_type_expr(rest.pop('items', {}))}>", rest)
    if kind == "object":
        values = rest.pop("additionalProperties", None)
        keys = rest.pop("propertyNames", None)
        key_expr = _type_expr(keys) if keys else "string"
        value_expr = _type_expr(values) if values not in (None, True) else "any"
        return _with_extras(f"object<{key_expr},{value_expr}>", rest)
    return _with_extras(kind, rest)


def _with_extras(base: str, extras: dict) -> str:
    if not extras:
        return base
    tail = ", ".join(
        f"{k}={json.dumps(v, sort_keys=True)}" for k, v in sorted(extras.items())
    )
    return f"{base}({tail})"


def test_additive_means_forward_compatible_not_backward_compatible():
    """The direction the additive rule does NOT cover, stated executably.

    An old body on a new build loads — that is what a default is for, and
    :func:`test_new_fields_are_additive_and_pinned` is about that direction.
    A **new body on an old build** does not, because every body here is
    ``extra="forbid"``. The read path does not validate, so the field rides
    along invisibly until something constructs the model from the stored dict
    (``credit_line(annotation.body)`` is the live example), and then it raises
    somewhere far from the change.

    The consequence is a deploy ORDER — new build first, then import or write
    with it — and it is the same reasoning as the ``.annot`` migration note.
    This test exists so that property is recorded as a fact rather than
    rediscovered at a credits roll.
    """
    from pydantic import ValidationError

    for model in (StillBodyV1, SegmentExtractionBodyV1, NarrationRenderBodyV1):
        assert model.model_config.get("extra") == "forbid", model.__name__
        required = {name for name, f in model.model_fields.items() if f.is_required()}
        body = {name: _placeholder(model, name) for name in required}
        # a body from a FUTURE build carries a field this one does not know
        with pytest.raises(ValidationError):
            model.model_validate({**body, "a_field_from_a_later_build": 1})
        # and the same body without it is fine, so the refusal is the extra
        # key and not the placeholder values
        model.model_validate(body)


def _placeholder(model, name):
    """A minimal valid value for a required field, by its JSON type."""
    prop = model.model_json_schema()["properties"][name]
    kind = prop.get("type") or next(
        (a.get("type") for a in prop.get("anyOf", []) if a.get("type")), "string"
    )
    return {
        "string": "x",
        "boolean": False,
        "number": 0.0,
        "integer": 0,
        "array": [],
        "object": {},
    }[kind]
