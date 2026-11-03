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
NEXT_STEP_HEAR_TAKES = (
    "weave_project",
    "a recorded take is not in the mix yet — weave again to hear it",
)
NEXT_STEP_DONE = (
    "weave_project",
    "an episode is made — download it by its episode_id, or weave again after "
    "changing the script",
)


def _pending_takes(project, *, episode) -> tuple:
    """Beats whose recorded take the current mix does not play (braidio#86)."""
    from braidio.transforms._takes import pending_takes

    return pending_takes(project, episode=episode)


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

        A re-weave adds an episode and keeps the earlier ones. The last is NOT
        necessarily the current one — a revert re-weaves to an older episode;
        see :meth:`current_episode`.
        """
        tiers = _tiers()
        return sorted(
            nw.annotations_at_tier(self.root, tiers.TIER_EPISODE_RENDER),
            # generation time, then id: nw's own order (ties: legacy tick-0 stamps)
            key=lambda a: (a.provenance.generated_at_time.to_seconds(), str(a.id)),
        )

    def current_episode(self, *, episodes=None):
        """The episode the script currently weaves to, or ``None`` if none.

        The most recent **fresh** episode, else the most recent
        (:func:`braidio.transforms._common.freshest`, the rule every "current
        node" reader shares). After an edit and its revert, ``weave_project``
        returns the ORIGINAL episode again, which is older than the edited cut
        but the only fresh one (braidio#169). When several are fresh, the one
        voiced as a weave would voice it now wins (an environment voice is in
        no node, so a flip A→B→A leaves both mixes fresh; braidio#172).
        ``episodes``: the list :meth:`episodes` returned, when the caller
        already has it.
        """
        if episodes is None:
            episodes = self.episodes()
        tiers = _tiers()
        if len(episodes) <= 1:
            return episodes[-1] if episodes else None
        with tiers.fresh_ids_pass():  # writes nothing: one walk for every lookup
            fresh_ids = tiers.fresh_ids(self)
            fresh = [e for e in episodes if e.id in fresh_ids]
            if len(fresh) > 1:  # e.g. an env voice flip A→B→A (braidio#172)
                from braidio.transforms._voice import voiced_as_now

                index = tiers.graph_index(self)
                return next(
                    (e for e in reversed(fresh) if voiced_as_now(self, e, index=index)),
                    fresh[-1],  # voiced in no mix yet (an env voice never woven)
                )
            return tiers.freshest(self, episodes)

    def genre_status(self) -> dict:
        """Where this production stands, for a host's one ``project_status`` door
        (``nw.Project.genre_status``, nw#133; braidio#125).

        Counted, in muvid's shape: the script's beats by kind, how many episodes
        were rendered, the current one as ``latest_episode`` (path-free; the
        wire name predates "current", see :meth:`current_episode`), and the
        next step as ``{"op", "why"}``, plus ``pending_takes``: the beats whose
        recorded take the mix does not play yet (braidio#86; empty unless a
        take was replaced). Indexed tier reads — plus one freshness walk, only
        once there is more than one episode to choose from — no file probe, so
        it stays small however often the project is re-woven, and never names
        a path. Two overrides for one beat are reported as the next step
        (``restore_take``), not raised: a polled status door must answer. The
        current episode depends on this process's environment voice when
        several mixes are fresh (:meth:`current_episode`, braidio#172).

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
        current = self.current_episode(episodes=episodes)
        try:
            pending, broken = _pending_takes(self, episode=current), None
        except ValueError as exc:  # two overrides for one beat: say how out
            pending, broken = (), str(exc)
        if broken:
            op, why = "restore_take", broken
        elif pending and episodes:
            op, why = NEXT_STEP_HEAR_TAKES
        elif episodes:
            op, why = NEXT_STEP_DONE
        elif any(counts.values()):
            op, why = NEXT_STEP_WEAVE
        else:
            op, why = NEXT_STEP_SAVE_SCRIPT
        return {
            **counts,
            "episode_count": len(episodes),
            "latest_episode": _episode_row(current) if current else None,
            "pending_takes": list(pending),
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


def open_or_create_project_at(
    projects_dir: "str | Path", project_id: str, *, title: str = ""
) -> Project:
    """The project at ``projects_dir/<project_id>``: opened when it is one,
    created (:func:`create_project_at`) when the folder is absent or empty.

    What every host keeping one project per production needs (braidio#182),
    without knowing nw's on-disk marker. ``title`` names a *new* project only;
    an existing one keeps its own. A folder that holds something but no
    project is refused rather than overwritten.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as d:
    ...     made = open_or_create_project_at(d, "ep_01", title="Episode 1")
    ...     again = open_or_create_project_at(d, "ep_01", title="ignored")
    ...     again.root == made.root, again.read_spec().title
    (True, 'Episode 1')
    """
    from braidio._paths import safe_component

    root = Path(projects_dir) / safe_component(project_id, label="project_id")
    try:
        return Project(root)
    except FileNotFoundError as missing:
        try:
            return create_project_at(projects_dir, project_id, title=title)
        except FileExistsError:
            raise FileExistsError(
                f"{root} exists but is not a braidio project (and is not empty); "
                "refusing to create one over it."
            ) from missing
