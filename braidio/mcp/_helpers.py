"""JSON (de)serialization helpers for the braidio MCP tool layer.

MCP tools speak plain JSON, but braidio returns rich Python: frozen dataclasses,
``pathlib.Path``, ``str``-enums, pydantic ``lacing.Annotation``\\ s, ``UUID``\\ s.
:func:`to_json` coerces any of those to a JSON-friendly structure (recursing into
dataclasses so nested ``Path``/enum values are coerced too — which
``dataclasses.asdict`` would not do), and :func:`script_from_json` /
:func:`source_from_json` build braidio's frozen input types from a JSON envelope.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from uuid import UUID
from collections.abc import Mapping


def to_json(obj: Any) -> Any:
    """Recursively coerce a braidio return value into JSON-friendly data.

    Handles dataclasses (recursed field-by-field), ``Path``/``UUID`` → ``str``,
    ``Enum`` → ``.value``, pydantic models → ``model_dump(mode="json")``, and
    mappings/sequences. ``bytes`` raise — audio bytes must be written to the
    workspace and referenced by path, never embedded in a JSON tool result.

    Note the single-segment path: a multi-segment one would render with the host's
    separator (``a/b`` on POSIX, ``a\\b`` on Windows) and make this doctest fail on
    one platform or the other. ``PurePosixPath`` would not do either -- it is not a
    ``Path``, so it would fall through to the last-resort branch instead of the
    ``Path`` one this is meant to document.

    >>> from pathlib import Path
    >>> to_json({"p": Path("beats.json"), "xs": (1, 2)})
    {'p': 'beats.json', 'xs': [1, 2]}
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (Path, UUID)):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, bytes):
        raise TypeError(
            "bytes are not JSON-serializable; write audio to the workspace and "
            "return its path/url instead"
        )
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_json(getattr(obj, f.name)) for f in fields(obj)}
    if hasattr(obj, "model_dump"):  # pydantic (e.g. lacing.Annotation)
        return obj.model_dump(mode="json")
    if isinstance(obj, Mapping):
        return {str(k): to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_json(v) for v in obj]
    return str(obj)  # last resort — never fail a tool on serialization


def script_from_json(payload: Mapping[str, Any]):
    """Build a :class:`braidio.Script` from a JSON envelope.

    Each beat dispatches on a ``"type"`` discriminator: ``"narration"`` →
    :class:`Narration`, ``"segment"`` → :class:`SegmentBeat`, ``"dialogue"`` →
    :class:`Dialogue` (its ``turns`` are normalized to a tuple of ``(role, text)``
    tuples), ``"scene_break"`` → :class:`SceneBreak`. Extra keys per beat are
    passed through to the dataclass, so an unknown key raises a clear
    ``TypeError`` rather than being silently dropped.
    """
    from braidio import Script, Narration, SegmentBeat, Dialogue, SceneBreak

    builders = {
        "narration": Narration,
        "segment": SegmentBeat,
        "dialogue": Dialogue,
        "scene_break": SceneBreak,
    }
    beats = []
    for i, raw in enumerate(payload.get("beats", [])):
        spec = dict(raw)
        kind = spec.pop("type", None)
        if kind not in builders:
            raise ValueError(
                f"beat[{i}] needs a 'type' of {sorted(builders)}, got {kind!r}"
            )
        if kind == "dialogue":
            spec["turns"] = tuple((r, t) for r, t in spec.get("turns", ()))
        beats.append(builders[kind](**spec))
    return Script(title=payload["title"], id_slug=payload["id_slug"], beats=beats)


def script_to_json(script) -> dict:
    """The inverse of :func:`script_from_json`: a Script as its JSON envelope."""
    from braidio import Dialogue, Narration, SceneBreak, SegmentBeat

    types = {
        Narration: "narration",
        SegmentBeat: "segment",
        Dialogue: "dialogue",
        SceneBreak: "scene_break",
    }
    beats = [{"type": types[type(b)], **to_json(b)} for b in script.beats]
    return {"title": script.title, "id_slug": script.id_slug, "beats": beats}


def source_from_json(payload: Optional[Mapping[str, Any]]):
    """Build a :class:`braidio.TimedLineSegmentSource` from JSON, or ``None``.

    ``payload`` carries ``lines`` (``[{index,start_s,end_s,text}]``) and an
    ``asset_path`` (a server-local media file), plus optional ``song_end_s`` /
    ``min_score``. ``None``/empty → ``None`` (a script with no segment beats needs
    no source).
    """
    if not payload:
        return None
    from braidio import TimedLine, TimedLineSegmentSource

    lines = [TimedLine(**ln) for ln in payload.get("lines", [])]
    return TimedLineSegmentSource(
        lines=lines,
        asset_path=payload["asset_path"],
        song_end_s=payload.get("song_end_s"),
        min_score=payload.get("min_score", 0.5),
    )
