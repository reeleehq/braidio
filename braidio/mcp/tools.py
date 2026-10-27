"""The braidio MCP tool surface — flat, JSON-in/JSON-out functions.

Each function here becomes one MCP tool (referenced by the connector as
``braidio.mcp.tools:<name>``); its signature + docstring *is* the tool schema, so
keep both clean. Three groups:

- **catalog / planning / read** — free, stateless (no ElevenLabs, no spend);
- **project workspace** — per-user projects (create/list/status), free;
- **[COSTED]** renders — the only tools that spend ElevenLabs money. Each writes
  its output into the caller's :class:`~braidio.mcp.workspace.Workspace` and returns
  ``cost_usd`` (an estimate; see :mod:`braidio.cost` + braidio#8) so the metering
  middleware records real dollars.

Values are coerced to JSON via :func:`~braidio.mcp._helpers.to_json`. Costed tools'
intermediate/work files stay inside the workspace (never the server CWD).
"""

from __future__ import annotations

import os

import braidio  # attr access (braidio.narrate, ...) so tests can monkeypatch
from fastmcp.exceptions import ToolError

from braidio.mcp._helpers import (
    script_from_json,
    script_to_json,
    source_from_json,
    to_json,
)
from braidio.rights import DEFAULT_PROFILE
from braidio.mcp.credentials import caller_elevenlabs_key
from braidio.mcp.metering import current_email
from braidio.mcp.workspace import Workspace
from braidio.tts import DEFAULT_MODEL_ID


def _workspace() -> Workspace:
    """The calling user's workspace, keyed by the identity the middleware resolved."""
    return Workspace.for_email(current_email())


def _require_nw(tool: str) -> None:
    if not braidio.HAS_NW:
        raise ToolError(f"{tool} needs braidio's nw layer, which is not installed here")


def _graph_structure(
    ws, *, format_id: str | None, bed_asset_id, sting_asset_id, script, tool: str
):
    """``(format, MusicStructure | None, MusicBed | None, sting_applied, sting_ignored_reason)``
    for a graph ingest.

    The format's declared structure is the base; the uploaded assets are what
    make it audible. Asset ids are resolved through the caller's workspace —
    a tool never takes a server path. Shares
    :func:`braidio.describe_asset_application` with the fast path
    (:func:`braidio.render_format`, braidio#43): under a format whose
    ``music_bed`` is ``"none"``, a supplied ``bed_asset_id`` is refused here —
    before ``open_project``/ingest, so before any spend — rather than silently
    dropped (braidio#53). A supplied ``sting_asset_id`` is never refused (a
    per-beat ``SceneBreak`` marker can still play it even under a ``"none"``
    default), only reported via the returned ``sting_applied`` /
    ``sting_ignored_reason`` — evaluated against the default
    :class:`~braidio.structure.MusicStructure` when no format is declared,
    since that default (``scene_marker="sting"``) is still what a scene break
    resolves against.
    """
    from dataclasses import replace

    from braidio.formats import sting_would_play
    from braidio.music import MusicBed, bed_for_intensity
    from braidio.structure import Sting

    fmt = _format(format_id)
    bed_path = _resolve_asset(ws, bed_asset_id)
    sting_path = _resolve_asset(ws, sting_asset_id)
    sting_applied = None
    sting_ignored_reason = None
    if fmt is not None:
        application = braidio.describe_asset_application(
            fmt, script, bed_asset=bed_path, sting_asset=sting_path
        )
        if application["bed_applied"] is False:
            raise ToolError(
                f"{tool}: format {format_id!r} declares music_bed="
                f"{fmt.music_bed!r} — bed_asset_id {bed_asset_id!r} would be "
                "paid for and dropped; omit it, or use a format whose "
                "music_bed isn't 'none'"
            )
        sting_applied = application["sting_applied"]
        sting_ignored_reason = application["sting_ignored_reason"]
    elif sting_path is not None:
        default_structure = braidio.MusicStructure()
        if sting_would_play(default_structure, script, sting_path):
            sting_applied = True
        else:
            sting_applied = False
            sting_ignored_reason = (
                "no scene break in this script resolves to marker='sting' "
                f"(default scene_marker={default_structure.scene_marker!r}, "
                "no format declared)"
            )
    structure = fmt.structure if fmt is not None else None
    if sting_path is not None:
        base = structure if structure is not None else braidio.MusicStructure()
        structure = replace(base, sting=Sting(sting_path))
    bed = None
    if bed_path is not None:
        bed = (
            bed_for_intensity(bed_path, fmt.music_bed)
            if fmt is not None
            else MusicBed(asset_path=bed_path)
        )
    return fmt, structure, bed, sting_applied, sting_ignored_reason


def _format(format_id: str | None):
    """The :class:`~braidio.formats.Format` for ``format_id`` (``None`` = none)."""
    if format_id is None:
        return None
    if format_id not in braidio.FORMATS:
        raise ToolError(
            f"unknown format {format_id!r}; use one of {sorted(braidio.FORMATS)}"
        )
    return braidio.FORMATS[format_id]


def _profile(profile: str):
    """The :class:`~braidio.rights.Profile` for ``profile``.

    Every tool that takes a rights profile resolves it here, so an unknown value
    is one ToolError naming the choices rather than whichever raw ``ValueError``
    ``Profile()`` happens to produce — the same courtesy :func:`_format` does
    for ``format_id``.
    """
    from braidio import Profile

    try:
        return Profile(profile)
    except ValueError:
        raise ToolError(
            f"unknown profile {profile!r}; use one of "
            f"{sorted(p.value for p in Profile)}"
        ) from None


# --- assistance -------------------------------------------------------------


def help() -> dict:
    """What braidio can do and how to use it.

    Call this when a user asks what this connector/assistant can do, or to recall the
    document -> script -> audio workflow and the free-vs-costed tool split.
    """
    from braidio.mcp._guide import capabilities

    return capabilities()


# --- catalog / registries (free) --------------------------------------------


def list_formats() -> dict:
    """List braidio's ready-made production formats, keyed by format id."""
    return {"formats": {k: to_json(v) for k, v in braidio.FORMATS.items()}}


def list_presets() -> dict:
    """List WeaveConfig presets (bundles of editing knobs), keyed by name."""
    return {"presets": {k: v.to_dict() for k, v in braidio.PRESETS.items()}}


def list_deliveries() -> dict:
    """List delivery presets (TTS model + voice-settings tunings), keyed by name."""
    return {"deliveries": {k: to_json(v) for k, v in braidio.DELIVERIES.items()}}


def list_voice_pools() -> dict:
    """List named voice pools for multi-voice casting, keyed by pool label."""
    return {
        "pools": {
            k: [to_json(voice) for voice in pool] for k, pool in braidio.POOLS.items()
        }
    }


def describe_genre() -> dict:
    """Describe the ``commentary_weave`` genre (its transforms, schemas, status)."""
    if not braidio.HAS_NW:
        return {"available": False, "reason": "nw layer not installed"}
    return {"available": True, "genre": to_json(braidio.COMMENTARY_WEAVE)}


# --- planning / read / safety (free) ----------------------------------------


def estimate_cost(script: dict) -> dict:
    """Estimate the ElevenLabs cost of a production BEFORE paying for synthesis.

    ``script`` is a JSON script envelope (see :func:`weave_project` for the shape).
    Segment extraction / weaving is free; only narration + dialogue text is billed.
    Returns exact character counts and an honest dollar estimate.
    """
    roll = braidio.estimate_cost(script_from_json(script))
    out = to_json(roll)
    out["summary"] = roll.summary
    return out


def plan_production(script: dict, profile: str = DEFAULT_PROFILE.value) -> dict:
    """Filter a script into the beats renderable under a rights ``profile``.

    ``profile`` is ``"personal"`` (play everything) or ``"published"`` (drop /
    substitute non-publishable clips). Returns the planned beats + what was
    dropped/substituted — a dry run, nothing is rendered.
    """
    plan = braidio.plan_production(script_from_json(script), _profile(profile))
    return to_json(plan)


def find_forbidden_quotes(text: str, forbidden: list[str], min_words: int = 5) -> dict:
    """Find forbidden verbatim quotes (>= ``min_words`` words) in ``text``."""
    return {
        "violations": braidio.find_verbatim_text(text, forbidden, min_words=min_words)
    }


def content_violations(
    script: dict, forbidden: list[str], profile: str = "published", min_words: int = 5
) -> dict:
    """Scan a production's render plan for non-publishable clips + forbidden quotes."""
    plan = braidio.plan_production(script_from_json(script), _profile(profile))
    return {
        "violations": braidio.content_violations(plan, forbidden, min_words=min_words)
    }


def audit_platitudes(text: str) -> dict:
    """Flag recycled rhetorical tics in commentary + a platitudes-per-1000-words rate."""
    return {
        "findings": [to_json(f) for f in braidio.audit_platitudes(text)],
        "rate_per_1000_words": braidio.platitude_rate(text),
    }


def clean_text(text: str, collapse_whitespace: bool = True) -> dict:
    """Prep text for clean TTS: expand ligatures/soft-hyphens + drop leaked speaker labels."""
    cleaned = braidio.strip_speaker_labels(
        braidio.clean_ocr(text, collapse_whitespace=collapse_whitespace)
    )
    return {"text": cleaned}


def build_timeline(
    kinds: list[str],
    durations: list[float],
    placements: list[str] | None = None,
    labels: list[str] | None = None,
    title: str = "",
) -> dict:
    """Assemble a queryable timeline breakdown of a planned production (no rendering)."""
    tb = braidio.build_timeline(
        kinds=kinds,
        durations=durations,
        placements=placements,
        labels=labels,
        title=title,
    )
    out = tb.to_dict()
    out["totals"] = tb.totals()
    out["shares"] = tb.shares()
    return out


def narration_segments(script: dict) -> dict:
    """Split a script's narration into sentence-level segments."""
    return {"segments": braidio.narration_segments(script_from_json(script))}


def assign_voices(n: int, pool: str = "four", seed: int = 0) -> dict:
    """Deterministically assign ``n`` voices from a named pool (``"four"`` | ``"many"``)."""
    if pool not in braidio.POOLS:
        raise ToolError(f"unknown pool {pool!r}; use one of {sorted(braidio.POOLS)}")
    voices = braidio.assign_voices(n, braidio.POOLS[pool], seed=seed)
    return {"voices": [to_json(v) for v in voices]}


def find_segment(
    lines: list[dict], quote: str, max_span: int = 12, min_score: float = 0.5
) -> dict:
    """Resolve a quote to a ``[start,end]`` window over time-aligned lines (token-F1)."""
    from braidio import TimedLine

    seg = braidio.find_segment(
        [TimedLine(**ln) for ln in lines], quote, max_span=max_span, min_score=min_score
    )
    if seg is None:
        return {"segment": None}
    out = to_json(seg)
    out["duration_s"] = seg.duration_s
    return {"segment": out}


# --- project workspace (free) -----------------------------------------------


def create_project(project_id: str, title: str = "") -> dict:
    """Create a new braidio project in your workspace (holds the graph + renders)."""
    _require_nw("create_project")
    proj = _workspace().create_project(project_id, title=title)
    return {
        "project_id": project_id,
        "title": title or project_id,
        "root": str(proj.root),
    }


def list_projects() -> dict:
    """List your braidio projects (newest first)."""
    return {"projects": _workspace().list_projects()}


def project_status(project_id: str) -> dict:
    """A project's status: beat/render counts, the next step, and its finished
    episodes (url + duration). Each episode's ``episode_id`` is the id ``resolve``
    accepts; the body's ``artifact_id`` is a content hash, not a download key.
    """
    _require_nw("project_status")
    import nw

    from braidio.project import EPISODE_TIER

    proj = _workspace().open_project(project_id)
    # The genre's status (what a host's one project_status door serves) plus the
    # full episode bodies, url included: this caller owns the workspace.
    episodes = [
        {"episode_id": str(a.id), **to_json(a.body)}
        for a in nw.annotations_at_tier(proj.root, EPISODE_TIER)
    ]
    return {"project_id": project_id, **proj.genre_status(), "episodes": episodes}


#: Content types whose extracted text has lost its markdown structure, so
#: ``as_beats`` would read flattened prose as one paragraph per line.
_NOT_MARKDOWN_TYPES = ("pdf", "html", "xml")


def _markdown_beats(full: str, ctype: str, *, max_chars: int) -> tuple[dict, bool]:
    """``full`` as a JSON script, cut to whole beats within ``max_chars``."""
    if any(kind in (ctype or "").lower() for kind in _NOT_MARKDOWN_TYPES):
        raise ToolError(
            f"as_beats reads markdown or plain text, not {ctype!r}; "
            "ingest without as_beats and author the beats from the text"
        )
    from dataclasses import replace

    from braidio import script_from_markdown

    script = script_from_markdown(full)
    kept, total = [], 0
    for beat in script.beats:
        total += len(beat.text)
        if total > max_chars:
            break
        kept.append(beat)
    truncated = len(kept) < len(script.beats)
    return script_to_json(replace(script, beats=kept)), truncated


def ingest_document(
    project_id: str,
    uri: str | None = None,
    text: str | None = None,
    name: str | None = None,
    max_chars: int = 40000,
    as_beats: bool = False,
) -> dict:
    """Ingest source material into a project so you can analyze it into a Script.

    Give ``uri`` (public http/https: PDF, HTML, text) OR ``text``. The plaintext is
    stored and returned (up to ``max_chars``) to turn into beats. For a markdown
    or text document to read aloud, ``as_beats=true`` returns a ready ``script`` of
    narration beats instead (headings, footnotes) to tag. Free.
    """
    from braidio.mcp import _docs

    ws = _workspace()
    root = ws.project_root(project_id)
    if not (root / "project.json").exists():
        raise ToolError(f"no project {project_id!r} — call create_project first")
    try:
        if uri:
            data, ctype = _docs.fetch_uri(uri)
            src = uri
        elif text is not None:
            data, ctype, src = text.encode("utf-8"), "text/plain", "inline"
        else:
            raise ToolError("provide either `uri` or `text`")
        full = _docs.extract_text(data, ctype)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    script, truncated = None, len(full) > max_chars
    if as_beats:
        script, truncated = _markdown_beats(full, ctype, max_chars=max_chars)

    from hashlib import sha256

    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    doc_id = sha256(data).hexdigest()[:16]
    (docs_dir / f"{doc_id}.txt").write_text(full, encoding="utf-8")
    return {
        "doc_id": doc_id,
        "name": name or src,
        "source": src,
        "content_type": ctype,
        "characters": len(full),
        "truncated": truncated,
        **({"script": script} if script else {"text": full[:max_chars]}),
    }


def save_script(
    project_id: str,
    script: dict,
    source: dict | None = None,
    format_id: str | None = None,
    bed_asset_id: str | None = None,
    sting_asset_id: str | None = None,
    profile: str = DEFAULT_PROFILE.value,
) -> dict:
    """Link a Script's beats into a project's graph (free authoring; render later).

    Writes every beat — narration, dialogue, segment, scene_break — into the
    project graph, so you can review (project_status) and render with
    weave_project when ready. Saving again replaces the previous script beat
    by beat (a changed ``profile`` or format included). ``format_id`` sets the
    dialogue cast; ``bed_asset_id`` / ``sting_asset_id`` record the music
    (see ``help``). Free — no synthesis.
    """
    _require_nw("save_script")
    scr = script_from_json(script)
    src = _resolve_source(source)
    _check_source(scr, src)
    ws = _workspace()
    fmt, structure, bed, sting_applied, sting_ignored_reason = _graph_structure(
        ws,
        format_id=format_id,
        bed_asset_id=bed_asset_id,
        sting_asset_id=sting_asset_id,
        script=scr,
        tool="save_script",
    )
    proj = ws.open_project(project_id)
    ingested = braidio.transforms.ingest_script(
        proj,
        scr,
        config=braidio.transforms.format_config(fmt),
        source=src,
        structure=structure,
        bed=bed,
        profile=_profile(profile),
        cast=fmt.cast if fmt is not None else None,
    )
    return {
        "project_id": project_id,
        "profile": profile,
        "beats": [{"kind": k, "id": str(a.id)} for k, a in ingested.ordered],
        "dropped": list(ingested.plan.dropped),
        "substituted": list(ingested.plan.substituted),
        "sting_applied": sting_applied,
        "sting_ignored_reason": sting_ignored_reason,
    }


# --- assets (braidio#10, free) ----------------------------------------------


def upload_asset(
    uri: str | None = None,
    data_b64: str | None = None,
    name: str | None = None,
) -> dict:
    """Upload media/source into your asset library; returns a ContentRef.

    Provide EITHER ``uri`` (a public http/https URL — audio/video/etc., fetched
    server-side, SSRF-guarded and size-bounded) OR ``data_b64`` (base64 bytes, for
    small inline uploads). Stored content-addressed per user; the returned
    ``item_id`` is usable as ``source.asset_id`` in any render that takes a
    source. Use ``download_audio`` instead for a song/video *page* link
    (YouTube etc.). Free — no synthesis.
    """
    import base64

    from braidio.mcp import _docs

    if uri:
        try:
            data, ctype = _docs.fetch_uri(uri)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        src = uri
    elif data_b64:
        try:
            data = base64.b64decode(data_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise ToolError(f"invalid data_b64: {exc}") from exc
        ctype, src = None, "inline"
    else:
        raise ToolError("provide either `uri` or `data_b64`")

    ws = _workspace()
    ref = ws.content_store().add(data, mime_type=ctype, name=name or (uri or None))
    meta = {**ref.to_json(), "name": name or src, "source": src}
    ws.asset_meta()[ref.item_id] = meta
    return meta


def list_assets() -> dict:
    """List the media/source assets in your library (ContentRefs + names). Free."""
    meta = _workspace().asset_meta()
    return {"assets": [meta[k] for k in meta]}


def get_asset(asset_id: str) -> dict:
    """Details for one uploaded asset (its ContentRef + name, and a URL if the store
    can mint one — e.g. an S3 backend; a local store returns none). Free."""
    from dol import content_url

    ws = _workspace()
    meta = ws.asset_meta()
    if asset_id not in meta:
        raise ToolError(f"no asset {asset_id!r} in your library")
    out = dict(meta[asset_id])
    url = content_url(ws.content_store(), asset_id)
    if url:
        out["url"] = url
    return out


# Bounds for server-side audio fetches (shared prod box; mirrors the intent of
# _docs.MAX_BYTES for document fetches). Audio mp3 is legitimately tens of MB for a
# long track, so the byte cap is larger than the 10MB document cap; both env-tunable.
_AUDIO_MAX_BYTES = int(os.environ.get("BRAIDIO_AUDIO_MAX_BYTES", str(60 * 1024 * 1024)))
_AUDIO_MAX_DURATION_S = int(
    os.environ.get("BRAIDIO_AUDIO_MAX_DURATION_S", str(45 * 60))
)


def download_audio(url: str, name: str | None = None) -> dict:
    """Put the audio of a song or video into your asset library from a link
    (YouTube, SoundCloud, Bandcamp, …): paste a public URL, get back an
    asset_id any render/weave can use. Use ``upload_asset`` for a direct file
    URL or bytes. Free — no synthesis spend. ⚠️ Only fetch audio you have the
    right to use — this grants no rights. Public hosts only,
    size/duration-bounded; best audio stream → mp3 via yt-dlp (no login/API
    key). Needs ``braidio[mcp]`` + ffmpeg.
    """
    from pathlib import Path

    from braidio.mcp import _docs, _media

    # SSRF pre-check (matches upload_asset/ingest_document): refuse non-http(s) and
    # loopback/private/link-local/cloud-metadata hosts. NOTE: yt-dlp resolves the page +
    # follows redirects itself, so this closes the direct-target vector but is not a full
    # egress sandbox — the driving agent's `url` can be steered by prompt injection.
    try:
        _docs._validate_target(url)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    try:
        from yb.download import download_youtube_video, youtube_video_info
    except ImportError as exc:
        raise ToolError(
            "audio download needs yt-dlp — install braidio[mcp] (which pulls yb[download])"
        ) from exc

    # Whatever credentials this deployment was given (often none). Checked BEFORE the
    # network: yt_dlp.cookies.load_cookies SILENTLY ignores an unreadable jar, so an
    # unchecked mis-provisioned server fails identically to a bot-gated one.
    creds = _media.SourceCredentials.from_env()
    creds.check()

    # Reject over-long sources (livestreams / multi-hour) BEFORE fetching any bytes.
    # Credentials go on this call too — the bot gate fires during metadata extraction.
    try:
        preview = youtube_video_info(url, extra_opts=dict(creds.opts))
    except Exception as exc:  # noqa: BLE001 — extractor errors
        raise _media.as_tool_error(
            exc, fallback="could not read the source", credentials=creds
        ) from exc
    duration = preview.get("duration")
    if isinstance(duration, (int, float)) and duration > _AUDIO_MAX_DURATION_S:
        raise ToolError(
            f"source is {int(duration)}s long; exceeds the "
            f"{_AUDIO_MAX_DURATION_S}s audio-download limit"
        )

    ws = _workspace()
    work = ws.renders_dir / "_downloads"
    work.mkdir(parents=True, exist_ok=True)
    try:
        result = download_youtube_video(
            url,
            download_dir=str(work),
            fmt="bestaudio/best",
            merge_to=None,  # audio-only; no video+audio merge
            extra_opts={
                **creds.opts,
                # braidio's own bounds are written AFTER the deployment's options, so a
                # configured option can never widen a safety bound.
                "max_filesize": _AUDIO_MAX_BYTES,  # yt-dlp skips/aborts oversized formats
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            },
        )
    except Exception as exc:  # noqa: BLE001 — yt-dlp raises many extractor error types
        raise _media.as_tool_error(
            exc, fallback="audio download failed", credentials=creds
        ) from exc

    audio_path = Path(result.path)
    if audio_path.suffix.lower() != ".mp3":  # the postprocessor rewrites to .mp3
        mp3 = audio_path.with_suffix(".mp3")
        if mp3.exists():
            audio_path = mp3
    # Belt-and-braces size gate before loading into RAM (post-processing can grow a file,
    # and max_filesize bounds the download, not the extracted output).
    if audio_path.stat().st_size > _AUDIO_MAX_BYTES:
        try:
            audio_path.unlink()
        except OSError:
            pass
        raise ToolError(f"downloaded audio exceeds the {_AUDIO_MAX_BYTES}-byte limit")
    data = audio_path.read_bytes()
    info = result.info or {}
    title = name or info.get("title") or audio_path.stem
    ref = ws.content_store().add(data, mime_type="audio/mpeg", name=title)
    meta = {
        **ref.to_json(),
        "name": title,
        "source": url,
        "kind": "audio",
        "duration": info.get("duration"),
        "copyright_notice": (
            "Downloaded from a user-supplied link — respect the source's copyright and "
            "terms of service; you are responsible for having the right to use it."
        ),
    }
    ws.asset_meta()[ref.item_id] = meta
    try:
        audio_path.unlink()  # the content-addressed store owns the bytes now
    except OSError:
        pass
    return meta


def _resolve_source(source: dict | None):
    """Build a segment source, resolving a ``source.asset_id`` (an uploaded
    :class:`dol.ContentRef`) to its server-local ``asset_path`` for weave/render."""
    if source and source.get("asset_id") and not source.get("asset_path"):
        source = {**source, "asset_path": _workspace().asset_path(source["asset_id"])}
    return source_from_json(source)


def _resolve_asset(ws: Workspace, asset_id: str | None) -> str | None:
    """Resolve an uploaded asset-library id to its server-local path.

    ``None`` (the field omitted) means "no asset" and resolves to ``None``; an
    empty string is a caller mistake, not "no asset", and raises like any other
    unknown id would. Same resolver as ``source.asset_id`` — never a raw server
    path from the caller.
    """
    if asset_id is None:
        return None
    if asset_id == "":
        raise ToolError("asset id must not be empty")
    return ws.asset_path(asset_id)


# --- [COSTED] renders (spend ElevenLabs money) ------------------------------


def _work_dir(ws: Workspace, name: str) -> str:
    """A per-render scratch dir inside the workspace (keeps intermediates off CWD)."""
    return str(ws.renders_dir / "_work" / name)


def _check_source(scr, source) -> None:
    """Fail early if a script needs segment media but no ``source`` was given.

    A segment source references a **server-accessible** ``asset_path``; a remote
    caller can only weave clips whose media already lives on the server (retrieval /
    upload of media is a connector concern — thorwhalen/braidio#10).
    """
    from braidio import SegmentBeat

    if source is None and any(isinstance(b, SegmentBeat) for b in scr.beats):
        raise ToolError(
            "this script has segment beats but no `source`; provide a source (timed "
            "lines + a server-accessible asset_path) or use a narration-only script"
        )


def _render_cost(scr, profile: str, *, cast=None) -> dict:
    """Estimate the spend of the beats that WILL render under ``profile``.

    Costs :func:`braidio.plan_production`'s output (the SSOT for the rights
    projection), so under ``"published"`` dropped clips cost nothing and synthesized
    substitutes are billed — unlike costing the raw script. The figure is a rate
    estimate (``cost_basis="estimate"``; see :mod:`braidio.cost` + braidio#8).

    ``cast`` is the :class:`~braidio.conversation.ConversationCast` the dialogue
    beats render under (``None`` = the default cast, as on every render entry
    point): a dialogue beat is priced at *its* model, the same one the render —
    fast path and ``dialogue_render.tts`` alike — actually submits, so the
    estimate cannot drift from the spend when a format casts another model.
    """
    from braidio.conversation import DEFAULT_CAST

    dialogue_model = (cast if cast is not None else DEFAULT_CAST).model_id
    plan = braidio.plan_production(scr, _profile(profile))
    chars = 0
    priced: list[float] = []
    unpriced = False
    for b in plan.beats:
        if b.kind == "narration":
            text, model = b.content, DEFAULT_MODEL_ID
        elif b.kind == "dialogue":
            text, model = "".join(t for _r, t in (b.turns or ())), dialogue_model
        else:
            continue  # clip = free (local ffmpeg)
        chars += braidio.billable_chars(text)
        cost = braidio.tts_cost_usd(text, model_id=model)
        if cost is None and text:
            unpriced = True
        elif cost is not None:
            priced.append(cost)
    usd = round(sum(priced), 6) if priced else (None if unpriced else 0.0)
    return {
        "cost_usd": usd,
        "characters": chars,
        "unpriced": unpriced,
        "cost_basis": "estimate",
    }


def _delivery(name: str):
    """Resolve a delivery/register preset name to its :class:`braidio.Delivery`."""
    if name not in braidio.DELIVERIES:
        raise ToolError(
            f"unknown delivery {name!r}; use one of {sorted(braidio.DELIVERIES)} "
            "(list_deliveries describes them)"
        )
    return braidio.DELIVERIES[name]


def _episode_retrieval(project_id: str, episode_id: str) -> dict:
    """The claim a caller hands to ``reelee_get_download_url`` for a woven episode.

    The episode body's ``artifact_id`` is the lacing CONTENT hash — an identity
    for provenance, not a retrieval key; ``resolve()`` accepts the episode
    annotation id (the file's stem). Returning the resolvable id is what makes
    "weave, then download what you just made" one step instead of a
    ``my_renders`` detour (braidio#32).
    """
    from braidio.downloads import claim

    return {
        "retrieval": {
            "download": claim(project_id, episode_id),
            "note": (
                f"Call `reelee_get_download_url(genre='braidio', "
                f"project_id='{project_id}', artifact_id='{episode_id}')` "
                "for a link to play and download this episode."
            ),
        }
    }


def _retrieval(out) -> dict:
    """What a REMOTE caller needs to hold this file, plus how to ask for it.

    `out.as_uri()` is a path on the connector's own disk — meaningless at the
    other end of an MCP connection, and for months the only thing these tools
    returned. `path` is kept because it is genuinely useful to an operator
    reading logs server-side; it is no longer the ONLY thing offered.
    """
    from braidio.downloads import claim

    return {
        "path": str(out),
        "download": claim("", out.stem),
        "note": (
            f"Call `reelee_get_download_url(genre='braidio', "
            f"artifact_id='{out.stem}')` for a link to play and download this. "
            f"You can refer to it as \u201c{out.stem}\u201d."
        ),
    }


def narrate(
    text: str,
    voice_id: str | None = None,
    delivery: str = "narration",
    name: str = "narration",
) -> dict:
    """[COSTED] Synthesize one text to speech (single voice) → an mp3 in your workspace.

    ``delivery`` picks the speaking register / voice preset: ``"narration"`` (default —
    reading a script) or ``"conversational"`` (sounds like talking, not reading), among
    others from ``list_deliveries``. It sets the model + voice settings. Uses your own ElevenLabs key when the request sends `X-Elevenlabs-Key`.
    """
    d = _delivery(delivery)
    ws = _workspace()
    out = ws.render_path(name)
    braidio.narrate(
        text,
        out,
        api_key=caller_elevenlabs_key(),
        voice_id=voice_id,
        model_id=d.model_id,
        voice_settings=d.voice_settings,
    )
    return {
        **_retrieval(out),
        "cost_usd": braidio.tts_cost_usd(text, model_id=d.model_id),
        "characters": braidio.billable_chars(text),
        "cost_basis": "estimate",
    }


def render_dialogue(turns: list[list[str]], name: str = "dialogue") -> dict:
    """[COSTED] Render a multi-speaker exchange (``[[role, text], ...]``) → one mp3 (eleven_v3). Uses your own ElevenLabs key when the request sends `X-Elevenlabs-Key`."""
    pairs = [(r, t) for r, t in turns]
    ws = _workspace()
    out = ws.render_path(name)
    braidio.render_dialogue(pairs, api_key=caller_elevenlabs_key(), out_path=out)
    text = "".join(t for _r, t in pairs)
    return {
        **_retrieval(out),
        "cost_usd": braidio.tts_cost_usd(text, model_id="eleven_v3"),
        "characters": braidio.billable_chars(text),
        "cost_basis": "estimate",
    }


def render_multivoice(
    segments: list[str], pool: str = "four", name: str = "multivoice"
) -> dict:
    """[COSTED] Render text segments cycling a pool of voices → one mp3. Uses your own ElevenLabs key when the request sends `X-Elevenlabs-Key`."""
    if pool not in braidio.POOLS:
        raise ToolError(f"unknown pool {pool!r}; use one of {sorted(braidio.POOLS)}")
    ws = _workspace()
    out = ws.render_path(name)
    braidio.render_multivoice(
        segments,
        braidio.POOLS[pool],
        api_key=caller_elevenlabs_key(),
        out_path=out,
        work_dir=_work_dir(ws, name),
    )
    text = "".join(segments)
    return {
        **_retrieval(out),
        "cost_usd": braidio.tts_cost_usd(text),
        "characters": braidio.billable_chars(text),
        "cost_basis": "estimate",
    }


def compose_narration(
    segments: list[str], preset: str = "single_narrator", name: str = "narration"
) -> dict:
    """[COSTED] Config-driven narration render (a WeaveConfig preset) → one mp3."""
    if preset not in braidio.PRESETS:
        raise ToolError(
            f"unknown preset {preset!r}; use one of {sorted(braidio.PRESETS)}"
        )
    ws = _workspace()
    out = ws.render_path(name)
    braidio.compose_narration(
        segments,
        braidio.PRESETS[preset],
        api_key=caller_elevenlabs_key(),
        out_path=out,
        work_dir=_work_dir(ws, name),
    )
    text = "".join(segments)
    return {
        **_retrieval(out),
        "cost_usd": braidio.tts_cost_usd(text),
        "characters": braidio.billable_chars(text),
        "cost_basis": "estimate",
    }


def render_production(
    script: dict,
    source: dict | None = None,
    profile: str = DEFAULT_PROFILE.value,
    delivery: str = "narration",
    name: str | None = None,
    bed_asset_id: str | None = None,
    sting_asset_id: str | None = None,
) -> dict:
    """[COSTED] Render a whole script → one mixed episode mp3 in your workspace.

    ``script`` is a JSON script envelope; ``source`` is required only for segment
    beats. ``profile`` = ``"personal"`` or ``"published"``. ``delivery`` =
    narration register (``"narration"`` or ``"conversational"``; see
    ``list_deliveries``). ``bed_asset_id`` / ``sting_asset_id`` (from
    ``upload_asset``) add a music bed and a scene-break sting; without them a
    scene_break is a pause and spotlight is inert.
    """
    from braidio.music import MusicBed
    from braidio.structure import MusicStructure, Sting

    scr = script_from_json(script)
    src = _resolve_source(source)
    _check_source(scr, src)
    ws = _workspace()
    stem = name or scr.id_slug
    out = ws.render_path(stem)
    bed_path = _resolve_asset(ws, bed_asset_id)
    sting_path = _resolve_asset(ws, sting_asset_id)
    braidio.render_production(
        scr,
        source=src,
        profile=_profile(profile),
        delivery=_delivery(delivery),
        api_key=caller_elevenlabs_key(),
        out_path=out,
        tts_dir=_work_dir(ws, stem) + "/tts",
        clips_dir=_work_dir(ws, stem) + "/clips",
        episodes_dir=str(ws.renders_dir),
        music_bed=MusicBed(asset_path=bed_path) if bed_path else None,
        structure=MusicStructure(sting=Sting(sting_path)) if sting_path else None,
    )
    return {**_retrieval(out), **_render_cost(scr, profile)}


def render_format(
    format_id: str,
    script: dict,
    source: dict | None = None,
    profile: str = DEFAULT_PROFILE.value,
    name: str | None = None,
    bed_asset_id: str | None = None,
    sting_asset_id: str | None = None,
) -> dict:
    """[COSTED] Render a script under a ready-made format preset → mp3 in your workspace.

    ``bed_asset_id`` / ``sting_asset_id`` (from ``upload_asset``/``list_assets``) add
    a music bed (at the format's intensity) and a scene-break sting; without them a
    scene_break is a pause and spotlight is inert. A ``music_bed="none"`` format
    refuses ``bed_asset_id`` up front; the result's ``sting_applied`` says whether a
    supplied sting actually played (see ``help`` for why).
    """
    fmt = _format(format_id)
    scr = script_from_json(script)
    src = _resolve_source(source)
    _check_source(scr, src)
    ws = _workspace()
    stem = name or scr.id_slug
    bed_path = _resolve_asset(ws, bed_asset_id)
    sting_path = _resolve_asset(ws, sting_asset_id)
    application = braidio.describe_asset_application(
        fmt, scr, bed_asset=bed_path, sting_asset=sting_path
    )
    if application["bed_applied"] is False:
        raise ToolError(
            f"render_format: format {format_id!r} declares music_bed="
            f"{fmt.music_bed!r} — bed_asset_id would be paid for and dropped; "
            "omit it, or use a format whose music_bed isn't 'none'"
        )
    out = ws.render_path(stem)
    braidio.render_format(
        fmt,
        scr,
        source=src,
        profile=_profile(profile),
        api_key=caller_elevenlabs_key(),
        out_path=out,
        tts_dir=_work_dir(ws, stem) + "/tts",
        clips_dir=_work_dir(ws, stem) + "/clips",
        episodes_dir=str(ws.renders_dir),
        bed_asset=bed_path,
        sting_asset=sting_path,
    )
    return {
        **_retrieval(out),
        **_render_cost(scr, profile, cast=fmt.cast),
        "sting_applied": application["sting_applied"],
        "sting_ignored_reason": application["sting_ignored_reason"],
    }


def weave_project(
    project_id: str,
    script: dict,
    source: dict | None = None,
    format_id: str | None = None,
    bed_asset_id: str | None = None,
    sting_asset_id: str | None = None,
    profile: str = DEFAULT_PROFILE.value,
) -> dict:
    """[COSTED] Ingest a script into your project and run the full commentary_weave pipeline.

    Renders every beat type: narration, dialogue (the format's cast), segment,
    scene_break. Re-running re-ingests: unchanged beats reuse their renders;
    only what changed (a beat, the format/cast, the rights ``profile``) is
    re-synthesized. ``format_id`` applies a format; ``bed_asset_id`` /
    ``sting_asset_id`` add music. Bills your own ElevenLabs key if the
    request sends `X-Elevenlabs-Key`.
    """
    _require_nw("weave_project")
    scr = script_from_json(script)
    src = _resolve_source(source)
    _check_source(scr, src)
    ws = _workspace()
    fmt, structure, bed, sting_applied, sting_ignored_reason = _graph_structure(
        ws,
        format_id=format_id,
        bed_asset_id=bed_asset_id,
        sting_asset_id=sting_asset_id,
        script=scr,
        tool="weave_project",
    )
    proj = ws.open_project(project_id)
    episode = braidio.weave_project(
        proj,
        scr,
        source=src,
        fmt=fmt,
        structure=structure,
        bed=bed,
        profile=_profile(profile),
        api_key=caller_elevenlabs_key(),
    )
    body = episode.body
    return {
        "episode": to_json(body),
        "url": body.get("url"),
        **_episode_retrieval(project_id, str(episode.id)),
        **_render_cost(scr, profile, cast=fmt.cast if fmt is not None else None),
        "sting_applied": sting_applied,
        "sting_ignored_reason": sting_ignored_reason,
    }
