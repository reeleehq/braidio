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


#: The next step, by how far the project has got: ``(op, why)``, ``op`` in
#: braidio's own vocabulary (a host maps it to its tool name, prefix and all).
NEXT_STEP_SAVE_SCRIPT = ("save_script", "there is no script yet")
NEXT_STEP_WEAVE = ("weave_project", "the script has not been rendered yet")
NEXT_STEP_DONE = (
    "weave_project",
    "an episode is made — download it by its episode_id, or weave again after "
    "changing the script",
)


def _tiers():
    """The tier names, from the one table the transforms write by (lazy: the
    transforms package is heavy and this module is imported with ``braidio``)."""
    from braidio.transforms import _common

    return _common


def _episode_row(annotation) -> dict:
    """One finished episode, path-free: the body's ``file://`` url and its
    (large) timeline are left out."""
    body = annotation.body
    duration = body.get("duration_s")
    return {
        "episode_id": str(annotation.id),
        "profile": body.get("profile"),
        "duration_s": None if duration is None else round(float(duration), 2),
    }


class Project(nw.Project):
    """An nw project for a braidio commentary-weave production."""

    KIND: WeaveKind = WeaveKind.COMMENTARY_WEAVE

    def episodes(self) -> list:
        """The project's finished episodes (``episode-renders`` nodes), oldest first.

        A re-weave adds an episode and keeps the earlier ones, so the last is
        the current one.
        """
        tiers = _tiers()
        return sorted(
            nw.annotations_at_tier(self.root, tiers.TIER_EPISODE_RENDER),
            key=lambda a: a.provenance.generated_at_time.to_seconds(),
        )

    def genre_status(self) -> dict:
        """Where this production stands, for a host's one ``project_status`` door
        (``nw.Project.genre_status``, nw#133; braidio#125).

        Counted, in muvid's shape: the script's beats by kind, how many episodes
        were rendered, the latest one (path-free), and the next step as
        ``{"op", "why"}``. Indexed tier reads only — no freshness walk, no file
        probe — and no per-item list, so it stays small however often the
        project is re-woven, and never names a path.

        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as d:
        ...     status = create_project_at(d, "ep").genre_status()
        >>> status["episode_count"], status["next_step"]["op"]
        (0, 'save_script')
        """
        tiers = _tiers()
        counts = {
            key: len(nw.annotations_at_tier(self.root, tier))
            for key, tier in (
                ("narration_beats", tiers.TIER_NARRATIVE_BEAT),
                ("dialogue_beats", tiers.TIER_DIALOGUE_BEAT),
                ("clips", tiers.TIER_AUDIO_CLIP),
                ("scene_breaks", tiers.TIER_SCENE_BREAK),
            )
        }
        episodes = self.episodes()
        if episodes:
            op, why = NEXT_STEP_DONE
        elif any(counts.values()):
            op, why = NEXT_STEP_WEAVE
        else:
            op, why = NEXT_STEP_SAVE_SCRIPT
        return {
            **counts,
            "episode_count": len(episodes),
            "latest_episode": _episode_row(episodes[-1]) if episodes else None,
            "next_step": {"op": op, "why": why},
        }


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
