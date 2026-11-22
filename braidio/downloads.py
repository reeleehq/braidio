"""Resolve a caller's rendered audio to its file — braidio's half of delivery.

Every braidio render tool returns ``out.as_uri()``: a ``file://`` path on the
connector's own filesystem, meaningless to a remote caller. A user paid $0.1233
and $0.0252 for two episodes that rendered correctly, passed every check, and
could not be retrieved by any tool on any surface. The defect hid for months
because on a local stdio server a ``file://`` path IS openable by the human
sitting at that machine — it only becomes visible the moment there is a remote
caller, which is also the moment it costs money.

This is the module braidio#10 called *"the actual deliverable of a costed
render"* and which that issue was closed without. Ownership follows the seam
agreed in reelee#252 §1 and already serving muvid:

- **braidio owns resolution** (this module): claim → file, authorised by the
  same per-email workspace scoping every tool uses. It never touches token,
  URL or page code.
- **The host owns transport**: one connector-owned route turning claims into
  signed, expiring URLs and a watch page.

Why braidio's references are names, not numbers
-----------------------------------------------
muvid names a render ``b02fc05417ea`` — a uuid slice nobody can say — so it
assigns ordinals (``cut 4``) and :mod:`nw.delivery` ships the parser for them.
braidio has the opposite problem and needs the opposite answer: its renders are
already titled by the person who made them (*"Why the Sky Looks Blue"*), and
layering ``episode 3`` on top of a real title would replace a good reference
with a worse one. So braidio's ``ref`` IS the title.

That is not an inconsistency in the seam — it is the seam working. A genre
supplies whatever reference is natural for it; ``Deliverable.ref`` means "the
label a human should say", and only the genre knows what that is.

Two locations, one caller-facing set (braidio#32)
-------------------------------------------------
A caller's audio lives in two places, and resolution spans both:

- **Flat renders** — every standalone render site (``render_production``,
  ``render_format``, ``narrate``, ``render_dialogue``, ``compose_narration``)
  writes through ``Workspace.render_path``: one flat per-caller directory keyed
  by the title the caller gave the render.
- **Project episodes** — the graph pipeline (``weave_project``) writes each
  assembled episode inside its project, at
  ``{project}/data/episodes/{annotation_id}.mp3``. That location is
  load-bearing: the episode annotation's body records that ``url``, and the
  partial-re-render DAG hangs off it. Delivery adapts to the graph's layout,
  never the other way around.

An earlier revision of this module claimed the project weaves wrote into the
flat directory too. They never did, and the one episode a paying caller
produced through the *documented* project-attached path was exactly the one no
tool could return (braidio#32). The lesson is the module's own founding lesson
one directory over: a resolver that covers only the layout its author
remembered is a resolver with an unreachable population.

``project_id``: ignored when resolving, honoured when listing (braidio#37)
-------------------------------------------------------------------------
The same field means opposite things to the module's two halves, and the
asymmetry is not an oversight — it falls out of which direction ignoring it
moves the answer.

- **:func:`resolve` accepts and ignores it.** Callers guess it — the connector
  requires *some* project_id for braidio but never validates which, and
  nothing ties the value in a claim to where the file actually lives — so
  treating it as a filter would refuse resolvable claims over a field that
  carries no information. Ignoring it only WIDENS the set searched, and the
  widening is bounded and email-scoped: episodes are found by scanning the
  caller's own projects.
- **:func:`list_deliverables` honours it, and narrows.** Here the field is not
  a guess but the question — ``nw.delivery.Lister``'s contract is that
  ``project_id=None`` means every project of that caller's in this genre, so a
  value means one. Ignoring it widens the ANSWER instead of the search, which
  is simply a wrong one: a project-scoped listing used to return every other
  project's episodes (each row saying so in its own ``project_id``) plus the
  flat renders, under a filter that claimed to have excluded them.

Cross-tenant containment never depended on this in either direction — it is
``_episode_dirs``'s email scoping and symlink checks, which both halves share.
"""

from __future__ import annotations

from pathlib import Path

from nw.delivery import Deliverable

#: The genre identifier the connector's resolver registry keys on.
GENRE = "braidio"

#: Extensions a render may legitimately carry, with their content types. braidio
#: produces audio; ``.mp4`` is here because an audiovisual format render lands in
#: the same directory and would otherwise be listed but unservable.
_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".mp4": "video/mp4",
}

__all__ = [
    "GENRE",
    "claim",
    "resolve",
    "list_deliverables",
    "list_projects",
    "organise",
]


def claim(project_id: str, artifact_id: str) -> dict:
    """The claim dict a tool returns — what the connector turns into a signed URL."""
    return {"genre": GENRE, "project_id": project_id, "artifact_id": artifact_id}


def _workspace(email: str):
    from braidio.mcp.workspace import Workspace

    return Workspace.for_email(email)


def _sidecar_path(media_path: Path) -> Path:
    """The organise sidecar beside one render/episode file.

    ``<stem>.organise.json`` — invisible to every listing (the ``.json``
    suffix is outside ``_CONTENT_TYPES``) and gone with its render. Naming
    state lives HERE, in the genre's own workspace, never in a host store,
    and never by renaming the file: the stem IS the artifact_id a signed
    token was minted against.
    """
    return media_path.with_name(media_path.stem + ".organise.json")


def _read_sidecar(media_path: Path) -> dict:
    import json

    sc = _sidecar_path(media_path)
    if not sc.exists():
        return {}
    try:
        loaded = json.loads(sc.read_text())
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def _overlay(d: Deliverable) -> Deliverable:
    """Apply the organise sidecar to a built deliverable.

    braidio's ``ref`` IS its title, so an assigned title mirrors into ``ref``
    (the seam's rule for title-reffed genres) — the label follows the rename
    while ``artifact_id`` stays put.
    """
    side = _read_sidecar(d.path)
    if not side:
        return d
    from dataclasses import replace

    changes: dict = {}
    title = side.get("title")
    if isinstance(title, str) and title.strip():
        changes["title"] = title
        changes["ref"] = title
        # The FILE never moves, but the host-facing filename ("what it should
        # be called in someone's Downloads folder") follows the label.
        changes["filename"] = f"{title}{d.path.suffix}"
    meta = dict(d.meta)
    if side.get("tags"):
        meta["tags"] = list(side["tags"])
    if side.get("note"):
        meta["note"] = side["note"]
    if meta != d.meta:
        changes["meta"] = meta
    return replace(d, **changes) if changes else d


def _stem_for_assigned_title(dirpath: Path, want: str) -> "str | None":
    """The stem whose organise sidecar assigns this title (case-folded), or None."""
    import json

    if not dirpath.is_dir():
        return None
    suffix = ".organise.json"
    for sc in dirpath.glob(f"*{suffix}"):
        try:
            loaded = json.loads(sc.read_text())
        except (OSError, ValueError):
            continue
        title = loaded.get("title") if isinstance(loaded, dict) else None
        if isinstance(title, str) and title.strip().casefold() == want:
            return sc.name[: -len(suffix)]
    return None


def _media_exists(dirpath: Path, stem: str) -> bool:
    """Whether a media file for this stem still exists — an ORPHANED organise
    sidecar (media removed out of band) must neither hold a title against
    reuse nor resolve to anything."""
    return any((dirpath / f"{stem}{ext}").is_file() for ext in _CONTENT_TYPES)


def _deliverable(path: Path, email: str, project_id: str = "") -> Deliverable:
    stat = path.stat()
    return _overlay(
        Deliverable(
            path=path,
            content_type=_CONTENT_TYPES[path.suffix.lower()],
            # The title IS the filename here, so it needs no rewriting to be
            # recognisable in a Downloads folder.
            filename=path.name,
            artifact_id=path.stem,
            project_id=project_id,
            genre=GENRE,
            ref=path.stem,
            title=path.stem,
            size_bytes=stat.st_size,
            created_at=stat.st_mtime,
        )
    )


#: The per-project media directories the graph pipeline writes, by kind:
#: ``episodes`` (``weave_to_episode``, audio) and ``cuts`` (``video_cut.render``
#: / ``video_cut.finish``, the commentary video — a ``{id}_motion.mp4`` is the
#: frames alone, a ``{id}.mp4`` the delivered cut). Both are load-bearing
#: locations: the annotation bodies record these ``url``\ s.
_PROJECT_MEDIA_KINDS: dict[str, tuple[str, str]] = {
    "episode": ("data", "episodes"),
    "cut": ("data", "cuts"),
}


def _episode_deliverable(
    path: Path, project_id: str, project_title: str, *, kind: str = "episode"
) -> Deliverable:
    """A project episode (or cut) as a Deliverable — id-keyed, so it reads differently.

    The stem is an annotation uuid nobody can say, so ``ref`` stays ``None``
    (``label`` falls back to the id) rather than pretending the uuid is a
    reference, and the human-facing fields borrow the project's title so a
    listing row and a Downloads-folder filename both say whose episode it is.
    A cut additionally says which stage it is — ``motion`` (frames only) or
    ``delivered`` — read off the file name the render transforms chose.
    """
    stat = path.stat()
    meta = {"kind": kind}
    if kind == "cut":
        meta["stage"] = "motion" if path.stem.endswith("_motion") else "delivered"
    return _overlay(
        Deliverable(
            path=path,
            content_type=_CONTENT_TYPES[path.suffix.lower()],
            filename=f"{project_id}-{kind}-{path.stem[:8]}{path.suffix}",
            artifact_id=path.stem,
            project_id=project_id,
            genre=GENRE,
            ref=None,
            title=f"{project_title} — {kind}",
            size_bytes=stat.st_size,
            created_at=stat.st_mtime,
            meta=meta,
        )
    )


def _episode_dirs(email: str, project_id: "str | None" = None):
    """``(project_id, title, episodes_dir)`` for each of the caller's projects
    — the audio walk; :func:`_media_dirs` is the same walk over every kind.

    ``project_id`` narrows the walk to that one project, by matching the
    ROWS this scan already yields rather than by building a path out of the
    caller's string. That is what makes an unknown, malformed or not-owned
    id a silent skip yielding nothing, never a ``ValueError`` out of
    ``project_root`` — the rule :func:`list_deliverables` needs (a lister
    is handed ids only one genre knows) and the one containment property
    below stays intact under.

    Bounded: ``list_projects`` only yields directories under the caller's own
    email-scoped tree that carry a ``project.json``. Two hardenings on top:

    - A project whose *resolved* episodes dir escapes the caller's projects
      tree (a symlinked project root or a symlinked ``data/episodes``) is
      skipped — the per-file parent check alone follows the symlinked
      component first and so would happily serve another tenant's directory.
      Nothing reachable creates such a symlink today; this is the check that
      keeps that sentence from having to stay true forever.
    - A directory name that fails the component rule is skipped rather than
      raised: it cannot hold an episode our tools wrote (``create_project``
      applies the same rule), and one out-of-band directory must not turn
      every braidio lookup into a 500.

    Note the ``project.json`` filter's flip side: an episode whose project has
    lost its ``project.json`` is invisible here — acceptable, because
    ``weave_project`` cannot have written it without one (``open_project``
    requires it), so the state only arises from out-of-band damage.
    """
    for pid, title, dirpath, _kind in _media_dirs(
        email, project_id, kinds=("episode",)
    ):
        yield pid, title, dirpath


def _media_dirs(
    email: str,
    project_id: "str | None" = None,
    *,
    kinds: "tuple[str, ...]" = tuple(_PROJECT_MEDIA_KINDS),
):
    """``(project_id, title, dir, kind)`` per project per media kind — the walk
    :func:`_episode_dirs` documents, over :data:`_PROJECT_MEDIA_KINDS`. Every
    directory passes the same containment check whatever its kind."""
    ws = _workspace(email)
    projects_root = ws.projects_dir.resolve()
    for row in ws.list_projects():
        pid = row["project_id"]
        if project_id and pid != project_id:
            continue
        for kind in kinds:
            try:
                dirpath = ws.project_root(pid).joinpath(*_PROJECT_MEDIA_KINDS[kind])
                if not dirpath.resolve().is_relative_to(projects_root):
                    continue
            except (ValueError, OSError):
                continue
            yield pid, row.get("title", pid), dirpath, kind


def resolve(email: str, project_id: str, artifact_id: str) -> Deliverable:
    """The caller's rendered audio as a servable file — braidio's download authority.

    ``artifact_id`` is the render's title, with or without its extension.
    ``project_id`` is accepted and ignored (see the module docstring).

    Raises ``KeyError`` for anything that does not resolve to a render of *this*
    caller — including titles shaped like an attack and other users' work. (No
    ``PermissionError`` branch: the renders directory is scoped by email, so
    "someone else's episode" and "no such episode" are indistinguishable here,
    and saying which would leak existence.)
    """
    from nw.paths import check_path_segment

    name = (artifact_id or "").strip()
    try:
        # The same rule the workspace applies when it CREATES a render, so we
        # never refuse something it happily wrote.
        check_path_segment(name.strip(), what="artifact_id")
    except ValueError as e:
        raise KeyError(str(e)) from None

    renders = _workspace(email).renders_dir
    stem = Path(name).stem if Path(name).suffix.lower() in _CONTENT_TYPES else name
    for ext in _CONTENT_TYPES:
        candidate = renders / f"{stem}{ext}"
        # resolve() the parent to defeat a symlink planted inside the dir; the
        # component check above already refuses `..` and separators.
        if candidate.is_file() and candidate.resolve().parent == renders.resolve():
            return _deliverable(candidate, email, project_id)

    # Not a flat render — try it as a project episode id (braidio#32). Every
    # project of the caller's is checked, not just ``project_id``: callers
    # guess that argument (nothing validates it against where the file lives),
    # so trusting it would refuse a claim for an episode that exists.
    for pid, title, dirpath, kind in _media_dirs(email):
        for ext in _CONTENT_TYPES:
            candidate = dirpath / f"{stem}{ext}"
            if candidate.is_file() and candidate.resolve().parent == dirpath.resolve():
                return _episode_deliverable(candidate, pid, title, kind=kind)

    # Not an id — an organise-ASSIGNED title must resolve from the moment it
    # is accepted (the seam's accepted-title-resolves obligation). Matched on
    # the suffix-STRIPPED input, same as the stem probes above — titles never
    # carry a media extension (organise refuses them), so "Sky Final.mp3"
    # unambiguously means the title "Sky Final". Flat renders first, then
    # episodes; every candidate passes the same containment check as the id
    # probes (the fallback must not be a symlink side door).
    want = stem.strip().casefold()
    assigned = _stem_for_assigned_title(renders, want)
    if assigned is not None:
        for ext in _CONTENT_TYPES:
            candidate = renders / f"{assigned}{ext}"
            if candidate.is_file() and candidate.resolve().parent == renders.resolve():
                return _deliverable(candidate, email, project_id)
    for pid, title, dirpath, kind in _media_dirs(email):
        assigned = _stem_for_assigned_title(dirpath, want)
        if assigned is None:
            continue
        for ext in _CONTENT_TYPES:
            candidate = dirpath / f"{assigned}{ext}"
            if candidate.is_file() and candidate.resolve().parent == dirpath.resolve():
                return _episode_deliverable(candidate, pid, title, kind=kind)

    raise KeyError(f"no render named {name!r}")


def list_deliverables(email: str, project_id: str = None) -> "list[Deliverable]":
    """Everything this caller can be handed — flat renders AND project episodes,
    newest first.

    Without this a reference is undiscoverable — a user could only name a render
    they still remembered from an earlier conversation, which is exactly the
    state that left a paid episode unreachable (braidio#32: the episode existed,
    resolved by nothing, and appeared in no listing).

    ``project_id`` NARROWS, per the seam's contract (:data:`nw.delivery.Lister`:
    ``None`` means every project of that caller's in this genre, so a value
    means one). Two consequences, and both are the point:

    - the flat renders dir is skipped entirely — a flat render belongs to no
      project, so it can never be a member of a project-scoped answer; and
    - an unknown, malformed or not-owned id yields ``[]``, never an error. The
      host fans one id out across every genre's lister (``reelee_my_renders``
      → ``lister(caller, project_id or None)``, filtering nothing itself) and
      genre namespaces are disjoint, so "an id this genre has never heard of"
      is the ordinary case, not a failure — raising would fabricate a "could
      not list your braidio work" problems entry about work never in scope.

    This is the opposite of :func:`resolve`, deliberately. There the same
    field is accepted and ignored, because ignoring it only WIDENS what
    resolves; here ignoring it widens the ANSWER, which is a wrong answer to
    the question asked (braidio#37).
    """
    out = []
    # Skipped outright under a project filter: a flat render's project_id is
    # "" and no filter value can equal it.
    renders = _workspace(email).renders_dir if not project_id else None
    if renders is not None and renders.is_dir():
        for child in sorted(renders.iterdir()):
            if child.is_file() and child.suffix.lower() in _CONTENT_TYPES:
                try:
                    out.append(_deliverable(child, email))
                except OSError:
                    continue
    for pid, title, dirpath, kind in _media_dirs(email, project_id):
        if not dirpath.is_dir():
            continue
        for child in sorted(dirpath.iterdir()):
            if child.is_file() and child.suffix.lower() in _CONTENT_TYPES:
                try:
                    out.append(_episode_deliverable(child, pid, title, kind=kind))
                except OSError:
                    continue
    out.sort(key=lambda d: d.created_at or 0, reverse=True)
    return out


def _is_braidios(root) -> bool:
    """Whether the project at ``root`` is braidio's to list (braidio#214).

    Where a host shares braidio's data home (reelee's studio wiring), the caller's
    bucket also holds the host's own projects; a project is braidio's when its
    recorded genre envelope names a genre braidio registered (the genre's project
    factory lives in this package) or records none (a project made before genres
    were recorded). A genre registered by anyone else, or by no one in this
    process, is not braidio's.
    """
    import nw

    envelope = nw.project_genre(root, fallback="graph")
    slug = (envelope or {}).get("genre")
    if not slug:
        return True
    if slug not in nw.genre_project_factories:
        return False
    module = getattr(nw.genre_project_factories[slug], "__module__", "") or ""
    return module.split(".", 1)[0] == __package__.split(".", 1)[0]


def list_projects(email: str) -> list:
    """Every project of this caller's — rendered or not (reelee#333).

    braidio's half of ``nw.delivery.ProjectLister``. Rows are
    ``nw.delivery.ProjectSummary``, newest-modified first (the workspace
    already orders them). ``deliverable_count`` counts the project's
    EPISODES only, by design — the audio is the production, a cut is a
    delivery made from it (the studio counts cuts through the graph); the flat
    per-caller renders belong to no project and are the ``Lister``'s to
    show. ``0`` is the load-bearing answer: a production with a script and
    no weave yet must list as "no episode yet" rather than vanish.

    The seam's error contract applies: ``[]`` is a positive claim, an
    infrastructure failure raises, and nothing here creates a workspace
    directory just to list it. The nw import is lazy so an environment
    carrying an older ``nw`` loses exactly this capability, never the
    module (and with it ``resolve``).
    """
    from nw.delivery import ProjectSummary

    ws = _workspace(email)
    rows = []
    for r in ws.list_projects():
        pid = r["project_id"]
        if not _is_braidios(ws.project_root(pid)):
            continue
        episodes = ws.project_root(pid) / "data" / "episodes"
        count = 0
        if episodes.is_dir():
            try:
                count = sum(
                    1
                    for c in episodes.iterdir()
                    if c.is_file() and c.suffix.lower() in _CONTENT_TYPES
                )
            except OSError:
                count = None  # unreadable is not zero — None means "not counted"
        rows.append(
            ProjectSummary(
                project_id=pid,
                title=r.get("title") or pid,
                genre=GENRE,
                created_at=r.get("created"),
                modified_at=r.get("modified"),
                deliverable_count=count,
            )
        )
    return rows


def organise(
    email: str,
    project_id: str,
    artifact_id: str,
    *,
    title: "str | None" = None,
    tags: "list | None" = None,
    note: "str | None" = None,
) -> Deliverable:
    """Rename, tag or annotate one render or episode — braidio's Organiser half.

    Persistence is a genre-owned sidecar beside the file (see
    :func:`_sidecar_path`) — the file itself is NEVER renamed: braidio's
    ``artifact_id`` IS the stem, a signed token is minted against it, and an
    episode's location is load-bearing for the graph. braidio's ``ref`` IS
    its title, so an accepted title mirrors into ``ref`` — the label follows
    the rename, the identity does not.

    Collisions are refused naming the holder, over the whole set this genre
    resolves names against: flat render stems, episode ids, and every
    already-assigned title (flat and episode, case-folded). ``None`` leaves a
    field unchanged; ``""``/``[]`` clears. All-or-nothing; the return is the
    deliverable AS RE-READ through ``resolve`` (the receipt rule).

    Auth is ``resolve``'s exactly — ``KeyError`` for anything that is not
    this caller's, ``ValueError`` for a refused request.
    """
    import json

    from nw.delivery import check_title

    if title is None and tags is None and note is None:
        raise ValueError("nothing to change: pass title=, tags= and/or note=")

    target = resolve(email, project_id, artifact_id)

    new_title: "str | None" = None
    if title is not None and title != "":
        new_title = check_title(title)
        from pathlib import Path as _P

        if _P(new_title).suffix.lower() in _CONTENT_TYPES:
            raise ValueError(
                f"{new_title!r} ends in a media extension — resolve strips "
                "those to find files, so the name would collide with the "
                "stem namespace; pick a name without the extension"
            )
        want = new_title.casefold()
        ws = _workspace(email)
        renders = ws.renders_dir

        def _holder() -> "str | None":
            # Flat stems + their assigned titles.
            if renders.is_dir():
                for child in renders.iterdir():
                    if child.suffix.lower() in _CONTENT_TYPES and child.is_file():
                        if (
                            child.stem.casefold() == want
                            and child.stem != target.artifact_id
                        ):
                            return child.stem
                own = _stem_for_assigned_title(renders, want)
                if (
                    own is not None
                    and own != target.artifact_id
                    and _media_exists(renders, own)
                ):
                    return own
            # Episode + cut ids and their assigned titles, across every
            # project — the same population `resolve` answers from, or a
            # title accepted here could resolve to two files (braidio#73).
            for pid, _t, episodes, _kind in _media_dirs(email):
                if not episodes.is_dir():
                    continue
                for child in episodes.iterdir():
                    if child.suffix.lower() in _CONTENT_TYPES and child.is_file():
                        if (
                            child.stem.casefold() == want
                            and child.stem != target.artifact_id
                        ):
                            return child.stem
                own = _stem_for_assigned_title(episodes, want)
                if (
                    own is not None
                    and own != target.artifact_id
                    and _media_exists(episodes, own)
                ):
                    return own
            return None

        holder = _holder()
        if holder is not None:
            raise ValueError(f"{new_title!r} is already held by {holder!r}")
    if tags is not None and not isinstance(tags, (list, tuple)):
        raise ValueError("tags must be a list of strings (or [] to clear)")

    side = _read_sidecar(target.path)
    if title is not None:
        if title == "":
            side.pop("title", None)
        else:
            side["title"] = new_title
    if tags is not None:
        if len(tags) == 0:
            side.pop("tags", None)
        else:
            side["tags"] = [str(t) for t in tags]
    if note is not None:
        if note == "":
            side.pop("note", None)
        else:
            side["note"] = str(note)

    sc = _sidecar_path(target.path)
    if side:
        sc.write_text(json.dumps(side, indent=2))
    else:
        sc.unlink(missing_ok=True)  # everything cleared — leave no empty sidecar

    # The receipt: re-resolve from storage, never echo the request — with an
    # EMPTY project_id, so the receipt's project_id matches what every later
    # listing shows (a flat render's is "", an episode's is discovered).
    return resolve(email, "", target.artifact_id)
