"""Which of braidio's body schemas name artifacts — the declarations nw#55 asks for.

nw writes the artifact tier of provenance by declaration: a body schema's owner
says which body fields hold bare 64-hex lacing asset ids, and
``nw.transforms.derive_provenance`` appends those ids to ``was_derived_from`` for
every input of that schema. A declared field holding anything else makes planning
raise, so a field is declared here only when **every** writer stores a bare asset id
(or ``None``).

Declared:

- ``production-structure/v1`` → ``sting_asset_id``, ``bed_asset_id``. One writer
  (``_ingest._structure_node``), which stores ``Artifact.from_path(path).asset_id``
  — the SHA-256 of the file's bytes, validated by lacing — or ``None``. The client's
  workspace id never reaches the body; the resolved file is re-hashed. Only
  ``weave_to_episode.default`` takes this schema as an input, so only
  ``episode-render/v1`` gains the edge. The sting and bed bytes are hashed but not
  registered in any artifact store: the edge names content, not a stored blob.
- ``take-override/v1`` → ``artifact_id``. One writer
  (``_takes.replace_take``), which stores ``Artifact.from_path(recording).asset_id``,
  and the body schema itself refuses anything but 64 lowercase hex. Only
  ``narration_render.tts`` takes this schema as an input, so a narration render
  spoken by an uploaded recording names the recording's bytes in its provenance
  (braidio#86).

Deliberately NOT declared (each is a decision, not an omission):

- ``source-media/v1.asset_id`` — holds a filesystem path (``_ingest.py`` writes
  ``str(resolved.asset_path)``), not an asset id. Declaring it would make every
  segment extraction raise at plan time.
- ``production-structure/v1.sting_url`` / ``bed_url`` — ``file://`` locators.
- The render bodies' ``artifact_id`` (narration, dialogue, segment-extraction,
  episode-render), ``still/v1.artifact_id`` and ``video-cut/v1``'s
  ``artifact_id`` / ``audio_artifact_id`` / ``captions_artifact_id`` — plausible
  candidates, but their writers (Transforms, cache-hit adoption, the importer)
  were not audited value-by-value for nw#55, including data written by older
  releases. The two mistakes are not symmetric: an undeclared field only means no
  edge is written, which is the behaviour before nw#55; a wrongly declared one
  makes planning raise on existing projects. Each can be declared here once its
  writers are shown to store bare asset ids only.
"""

from __future__ import annotations

from nw.transforms import asset_fields, register_asset_refs

from braidio.bodies import PRODUCTION_STRUCTURE_V1, TAKE_OVERRIDE_V1

#: ``{body_schema_uri: dotted field paths}`` — the SSOT for what braidio declares.
ASSET_REF_FIELDS: dict[str, tuple[str, ...]] = {
    PRODUCTION_STRUCTURE_V1: ("sting_asset_id", "bed_asset_id"),
    TAKE_OVERRIDE_V1: ("artifact_id",),
}


def declare_asset_refs() -> None:
    """Register :data:`ASSET_REF_FIELDS` with nw. Idempotent (nw replaces)."""
    for uri, paths in ASSET_REF_FIELDS.items():
        register_asset_refs(uri, asset_fields(*paths))


declare_asset_refs()
