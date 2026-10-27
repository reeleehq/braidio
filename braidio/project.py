"""braidio as an nw app: a :class:`nw.Project` for commentary-weave productions.

Subclassing ``nw.Project`` inherits the whole folder facade + lacing graph +
freshness (``stale_after``) machinery; braidio adds its production kind and its
domain/render body-schema vocabulary (registered on import of
:mod:`braidio.bodies`). This module requires ``nw``; it is imported lazily by
``braidio/__init__`` only when ``nw`` is available.

The costed ``plan``/``execute`` Transform pipeline (each transform delegating to
braidio's functional core for the actual audio, and recording provenance via
:mod:`braidio.provenance`) is the next increment — see the design doc and
nw#9 (generalizing nw's shot/mp4 render seam).
"""

from __future__ import annotations

from pathlib import Path

import nw

from braidio import bodies as _bodies  # noqa: F401  (import registers schemas)
from braidio.kinds import WeaveKind


#: What :meth:`Project.genre_status` counts: its key -> the graph tier counted.
STATUS_COUNTS = {
    "narration_beats": "narrative-beats",
    "dialogue_beats": "dialogue-beats",
    "clips": "audio-clips",
    "scene_breaks": "scene-breaks",
    "narration_renders": "narration-renders",
    "dialogue_renders": "dialogue-renders",
    "segment_extractions": "segment-extractions",
}

#: The counts that mean "a script has been saved".
_SCRIPT_COUNTS = ("narration_beats", "dialogue_beats", "clips")

#: The tier holding a project's finished episodes.
EPISODE_TIER = "episode-renders"

#: The next step, by how far the project has got (tool names as the MCP surface
#: exposes them).
NEXT_STEP_SAVE_SCRIPT = "save a script (save_script)"
NEXT_STEP_WEAVE = "render the episode (weave_project)"
NEXT_STEP_DONE = "download an episode (resolve its episode_id)"


def episode_rows(root) -> list[dict]:
    """The finished episodes of the project at ``root``, path-free.

    One row per ``episode-renders`` node: ``episode_id`` (the id a download
    resolves), ``profile`` and ``duration_s``. The body's ``url`` (a ``file://``
    path) and ``timeline`` (large) are deliberately left out.
    """
    return [
        {
            "episode_id": str(a.id),
            "profile": a.body.get("profile"),
            "duration_s": round(float(a.body.get("duration_s") or 0.0), 2),
        }
        for a in nw.annotations_at_tier(root, EPISODE_TIER)
    ]


class Project(nw.Project):
    """An nw project for a braidio commentary-weave production."""

    KIND: WeaveKind = WeaveKind.COMMENTARY_WEAVE

    def genre_status(self) -> dict:
        """Where this production stands, for a host's one ``project_status`` door
        (``nw.Project.genre_status``, nw#133; braidio#125).

        Counts of what the graph holds (:data:`STATUS_COUNTS`), the finished
        episodes (:func:`episode_rows`) and the next step. Cheap indexed tier
        reads only — no freshness walk, no file probe — and never a path, so a
        polled endpoint can serve it as is.

        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as d:
        ...     status = create_project_at(d, "ep").genre_status()
        >>> status["episodes"], status["next_step"]
        ([], 'save a script (save_script)')
        """
        counts = {
            key: len(nw.annotations_at_tier(self.root, tier))
            for key, tier in STATUS_COUNTS.items()
        }
        episodes = episode_rows(self.root)
        if episodes:
            next_step = NEXT_STEP_DONE
        elif any(counts[key] for key in _SCRIPT_COUNTS):
            next_step = NEXT_STEP_WEAVE
        else:
            next_step = NEXT_STEP_SAVE_SCRIPT
        return {**counts, "episodes": episodes, "next_step": next_step}


def create_project_at(
    projects_dir: "str | Path", project_id: str, *, title: str = "", force: bool = False
) -> Project:
    """Create a braidio project at ``projects_dir/<project_id>`` — the *host-placed* create.

    The counterpart to :meth:`braidio.mcp.workspace.Workspace.create_project`, which
    places a project in braidio's own per-caller workspace. This one takes the
    directory from its caller, because a host that will **serve** the project
    (reelee, over HTTP) has to put it where its own resolver and lister look — under
    braidio's data home it would be a sibling of nothing the host can address. See
    nw's ``projects_dir`` placement contract (thorwhalen/nw#84).

    Deliberately here and not in ``braidio.mcp.workspace``: importing that module
    runs ``braidio/mcp/__init__.py`` and pulls **fastmcp**, and the host-placed path
    has no reason to require the MCP extra. ``project_id`` is validated as a single
    traversal-safe component, so the result is always a direct child of
    ``projects_dir`` — which is the property the host's lister depends on, and which
    nw verifies after the fact.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as d:
    ...     p = create_project_at(d, "ep_01", title="Episode 1")
    ...     p.root.name
    'ep_01'
    """
    from braidio._paths import safe_component

    root = Path(projects_dir) / safe_component(project_id, label="project_id")
    root.parent.mkdir(parents=True, exist_ok=True)
    return Project.init(root, title=title or project_id, force=force)
