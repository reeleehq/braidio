"""Tests for the braidio MCP server (``braidio.mcp``).

Skipped entirely if the ``mcp`` extra (fastmcp + py2mcp) isn't installed. Exercises
the JSON helpers, the per-user workspace (isolation + traversal safety), the
fail-closed identity/allowlist model, and the served tools end-to-end via an
in-memory FastMCP client — including that costed tools record real cost in the
usage ledger and the error path is recorded. ElevenLabs/ffmpeg work is stubbed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import braidio

pytest.importorskip("fastmcp")
pytest.importorskip("py2mcp")

import braidio.mcp as bmcp  # noqa: E402
from braidio.mcp import metering  # noqa: E402
from braidio.mcp._helpers import (
    script_from_json,
    source_from_json,
    to_json,
)  # noqa: E402
from braidio.mcp.workspace import Workspace  # noqa: E402
from fastmcp import Client  # noqa: E402

_NW = pytest.mark.skipif(not braidio.HAS_NW, reason="nw layer not installed")

#: Identity of the local-dev (no-OAuth) server the served-tool tests run against.
OWNER = "owner@example.com"

#: Opaque bearer value carried by the stand-in OAuth token. Never verified — the
#: tests monkeypatch the dependency that would do the verifying.
MOCK_TOKEN_VALUE = "test-access-token"

#: OAuth client the stand-in token is issued to. fastmcp's telemetry hook reads it
#: off every verified token, so it has to be present and non-empty.
MOCK_CLIENT_ID = "test-client"

#: Scopes granted to the stand-in token. braidio authorizes on the ``email``/``sub``
#: claim, not on scopes, so an empty grant is the honest default.
MOCK_SCOPES: list[str] = []


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIDIO_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("BRAIDIO_TTS_USD_PER_1K_CHARS", raising=False)


def _local_server(**kw):
    """A server in explicit local-dev mode (identity = OWNER, no OAuth)."""
    kw.setdefault("local_user", OWNER)
    return bmcp.build_server(**kw)


def _call(server, tool, args):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args)

    return asyncio.run(go())


def _mock_token(monkeypatch, email, *, client_id=MOCK_CLIENT_ID, scopes=None):
    """Install a **real** ``AccessToken`` for ``email`` as the verified caller.

    It must be the genuine type, not a duck-typed stand-in carrying only ``claims``:
    fastmcp's server plumbing reads the *whole* token off every request — its
    telemetry hook touches ``token.client_id``/``token.scopes`` (catching only
    ``RuntimeError``), and ``get_access_token`` coerces foreign objects through
    ``model_dump()``, requiring ``token``/``client_id``/``scopes``. A partial double
    therefore fails every *served* tool call with an opaque ``AttributeError`` that
    has nothing to do with the behaviour under test.
    """
    import fastmcp.server.dependencies as deps
    from fastmcp.server.auth.auth import AccessToken

    token = AccessToken(
        token=MOCK_TOKEN_VALUE,
        client_id=client_id,
        scopes=list(MOCK_SCOPES if scopes is None else scopes),
        claims={"email": email},
    )
    monkeypatch.setattr(deps, "get_access_token", lambda: token)
    return token


# --- serialization ----------------------------------------------------------


def test_to_json_coerces_path_enum_dataclass():
    from braidio import Profile, Voice

    assert to_json(Path("/a/b")) == str(Path("/a/b"))  # Path -> str (platform-agnostic)
    assert to_json(Profile.PUBLISHED) == "published"
    assert to_json(Voice(id="v", name="V", gender="f")) == {
        "id": "v",
        "name": "V",
        "gender": "f",
        "accent": "",
        "note": "",
    }


def test_to_json_pydantic_and_str_fallback():
    from pydantic import BaseModel

    class M(BaseModel):
        a: int
        b: str

    assert to_json(M(a=1, b="x")) == {"a": 1, "b": "x"}

    class Weird:
        def __repr__(self):
            return "WEIRD"

    assert to_json(Weird()) == "WEIRD"  # last-resort str()


def test_to_json_rejects_bytes():
    with pytest.raises(TypeError):
        to_json(b"audio")


def test_script_from_json_dispatches_beat_types():
    script = script_from_json(
        {
            "title": "t",
            "id_slug": "01",
            "beats": [
                {"type": "narration", "text": "hi"},
                {"type": "segment", "reference": "clip"},
                {"type": "dialogue", "turns": [["A", "x"], ["B", "y"]]},
                {"type": "scene_break", "label": "act 2"},
                {"type": "segment", "reference": "key exhibit", "spotlight": True},
            ],
        }
    )
    assert [type(b).__name__ for b in script.beats] == [
        "Narration",
        "SegmentBeat",
        "Dialogue",
        "SceneBreak",
        "SegmentBeat",
    ]
    assert script.beats[2].turns == (("A", "x"), ("B", "y"))
    assert script.beats[3].label == "act 2" and script.beats[3].marker is None
    assert script.beats[4].spotlight is True


def test_script_from_json_rejects_unknown_beat_type():
    with pytest.raises(ValueError):
        script_from_json({"title": "t", "id_slug": "01", "beats": [{"type": "song"}]})


def test_source_from_json_none_and_built():
    assert source_from_json(None) is None
    src = source_from_json(
        {
            "lines": [{"index": 0, "start_s": 0.0, "end_s": 1.0, "text": "la"}],
            "asset_path": "/tmp/song.mp3",
            "song_end_s": 12.0,
        }
    )
    assert type(src).__name__ == "TimedLineSegmentSource"
    assert src.resolve("la") is not None  # a working source over the timed line


# --- workspace --------------------------------------------------------------


def test_workspace_isolates_users(tmp_path):
    a = Workspace.for_email("a@x.com", root=tmp_path)
    b = Workspace.for_email("b@x.com", root=tmp_path)
    assert a.projects_dir != b.projects_dir
    assert a.projects_dir.parent == b.projects_dir.parent


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", "", "x\x00y"])
def test_workspace_rejects_path_traversal(tmp_path, bad):
    ws = Workspace.for_email("u@x.com", root=tmp_path)
    with pytest.raises(ValueError):
        ws.project_root(bad)
    with pytest.raises(ValueError):
        ws.render_path(bad)


@_NW
def test_workspace_open_missing_and_list(tmp_path):
    ws = Workspace.for_email("u@x.com", root=tmp_path)
    assert ws.list_projects() == []
    with pytest.raises(FileNotFoundError):
        ws.open_project("nope")
    ws.create_project("p1", title="One")
    (row,) = ws.list_projects()
    assert row["project_id"] == "p1" and row["title"] == "One"
    assert row["modified"] is not None and row["created"] is None
    assert ws.open_project("p1").root == ws.project_root("p1")


# --- identity / access (fail-closed) ----------------------------------------


def test_mock_token_is_a_real_access_token(monkeypatch):
    """The test double must satisfy fastmcp's *whole* token contract, not just ``claims``.

    Guards against re-introducing a duck-typed stand-in: this asserts the real type
    and then runs the very fastmcp hook (``get_auth_span_attributes``) that a partial
    double breaks — which is what turned every served-tool test red under fastmcp 3.1.
    """
    from fastmcp.server.auth.auth import AccessToken
    from fastmcp.server.telemetry import get_auth_span_attributes

    _mock_token(monkeypatch, "user@example.com")
    # Resolve the dependency the way fastmcp itself does — a late module-attribute
    # lookup, so the patched provider (not the import-time original) is what runs.
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    assert isinstance(token, AccessToken)
    assert get_auth_span_attributes()["enduser.id"] == MOCK_CLIENT_ID


def test_token_email_prefers_email_claim_lowercased(monkeypatch):
    _mock_token(monkeypatch, "USER@Example.COM")
    assert metering.token_email() == "user@example.com"


def test_current_email_raises_outside_call():
    with pytest.raises(Exception):
        metering.current_email()


def test_served_uses_token_identity(monkeypatch):
    _mock_token(monkeypatch, "user@example.com")
    ledger = {}
    server = bmcp.build_server(
        ledger=ledger, allowed={"user@example.com"}
    )  # no local_user
    _call(server, "list_formats", {})
    assert next(iter(ledger.values()))["email"] == "user@example.com"


def test_fail_closed_without_token_or_local_user():
    server = bmcp.build_server(
        ledger={}, allowed={"a@b.com"}
    )  # no local_user, no token
    with pytest.raises(Exception) as ei:
        _call(server, "list_formats", {})
    assert "authentication required" in str(ei.value)


def test_deny_by_default_when_no_allowlist(monkeypatch):
    _mock_token(monkeypatch, "user@example.com")
    server = bmcp.build_server(ledger={})  # served, allowed=None, allow_any off
    with pytest.raises(Exception) as ei:
        _call(server, "list_formats", {})
    assert "allowlist" in str(ei.value)


def test_allowlist_rejects_unauthorized_caller():
    server = _local_server(ledger={}, allowed={"someone@else.com"})  # OWNER not in set
    with pytest.raises(Exception) as ei:
        _call(server, "list_formats", {})
    assert "not authorized" in str(ei.value)


# --- served tools -----------------------------------------------------------


def test_all_tools_registered():
    server = _local_server(ledger={})

    async def names():
        async with Client(server) as c:
            return {t.name for t in await c.list_tools()}

    assert asyncio.run(names()) == set(bmcp.TOOL_NAMES)
    assert len(bmcp.COSTED_TOOLS) == 7


def test_estimate_cost_tool():
    server = _local_server(ledger={})
    r = _call(
        server,
        "estimate_cost",
        {
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [{"type": "narration", "text": "a" * 1000}],
            }
        },
    )
    assert r.structured_content["characters"] == 1000
    assert r.structured_content["usd"] == pytest.approx(0.30)


def test_free_tool_records_entry_without_cost():
    ledger = {}
    server = _local_server(ledger=ledger)
    _call(server, "list_formats", {})
    entry = next(iter(ledger.values()))
    assert entry["tool"] == "list_formats"
    assert entry["status"] == "done"
    assert entry["email"] == OWNER
    assert entry.get("cost_usd") is None  # free op


def _stub_writes(monkeypatch, fn_name):
    def _stub(*a, **kw):
        out = kw.get("out_path") or (a[1] if len(a) > 1 else None)
        if out:
            p = Path(out)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"AUDIO")
            return p
        return None

    monkeypatch.setattr(braidio, fn_name, _stub)


def test_narrate_records_real_cost(monkeypatch):
    _stub_writes(monkeypatch, "narrate")
    ledger = {}
    server = _local_server(ledger=ledger)
    r = _call(server, "narrate", {"text": "x" * 2000})
    assert r.structured_content["cost_usd"] == pytest.approx(0.60)
    assert r.structured_content["cost_basis"] == "estimate"
    entry = [e for e in ledger.values() if e["tool"] == "narrate"][0]
    assert entry["cost_usd"] == pytest.approx(0.60)  # spend reached the ledger


def test_render_dialogue_records_cost(monkeypatch):
    _stub_writes(monkeypatch, "render_dialogue")
    ledger = {}
    server = _local_server(ledger=ledger)
    r = _call(
        server, "render_dialogue", {"turns": [["A", "x" * 500], ["B", "y" * 500]]}
    )
    assert r.structured_content["cost_usd"] == pytest.approx(0.30)
    assert [e for e in ledger.values() if e["tool"] == "render_dialogue"][0][
        "cost_usd"
    ] > 0


def test_render_production_costs_the_plan_and_stays_in_workspace(monkeypatch, tmp_path):
    calls = {}

    def _stub(scr, *, out_path, **kw):
        calls.update(kw)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"EP")
        return Path(out_path)

    monkeypatch.setattr(braidio, "render_production", _stub)
    ledger = {}
    server = _local_server(ledger=ledger)
    r = _call(
        server,
        "render_production",
        {
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [{"type": "narration", "text": "a" * 1000}],
            }
        },
    )
    assert r.structured_content["cost_usd"] == pytest.approx(0.30)
    assert r.structured_content["cost_basis"] == "estimate"
    # intermediates go into the workspace, not the CWD
    assert str(tmp_path) in calls["tts_dir"] and str(tmp_path) in calls["clips_dir"]


def test_render_production_requires_source_for_segments():
    server = _local_server(ledger={})
    with pytest.raises(Exception) as ei:
        _call(
            server,
            "render_production",
            {
                "script": {
                    "title": "t",
                    "id_slug": "01",
                    "beats": [{"type": "segment", "reference": "clip"}],
                }
            },
        )
    assert "no `source`" in str(ei.value)


def test_render_production_wires_bed_and_sting_assets(monkeypatch):
    # braidio#41: bed_asset_id/sting_asset_id resolve through the workspace (like
    # source.asset_id) into a MusicBed / MusicStructure(sting=...), never a raw path.
    import base64

    from braidio.music import MusicBed
    from braidio.structure import MusicStructure

    calls = {}

    def _stub(scr, *, out_path, **kw):
        calls.update(kw)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"EP")
        return Path(out_path)

    monkeypatch.setattr(braidio, "render_production", _stub)
    server = _local_server(ledger={})
    bed_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"BED").decode()}
    ).structured_content["itemId"]
    sting_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"STING").decode()}
    ).structured_content["itemId"]
    _call(
        server,
        "render_production",
        {
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [{"type": "narration", "text": "hi"}],
            },
            "bed_asset_id": bed_id,
            "sting_asset_id": sting_id,
        },
    )
    ws = Workspace.for_email(OWNER)
    assert isinstance(calls["music_bed"], MusicBed)
    assert calls["music_bed"].asset_path == ws.asset_path(bed_id)
    assert isinstance(calls["structure"], MusicStructure)
    assert calls["structure"].sting.asset_path == ws.asset_path(sting_id)


def test_render_production_omits_bed_and_sting_by_default(monkeypatch):
    # Additive-only: no bed/sting ids means byte-identical kwargs to before #41.
    calls = {}

    def _stub(scr, *, out_path, **kw):
        calls.update(kw)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"EP")
        return Path(out_path)

    monkeypatch.setattr(braidio, "render_production", _stub)
    server = _local_server(ledger={})
    _call(
        server,
        "render_production",
        {
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [{"type": "narration", "text": "hi"}],
            }
        },
    )
    assert calls["music_bed"] is None
    assert calls["structure"] is None


def test_render_format_wires_bed_and_sting_assets(monkeypatch):
    import base64

    calls = {}

    def _stub(fmt, scr, *, out_path, **kw):
        calls.update(kw)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"EP")
        return Path(out_path)

    monkeypatch.setattr(braidio, "render_format", _stub)
    server = _local_server(ledger={})
    bed_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"BED").decode()}
    ).structured_content["itemId"]
    sting_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"STING").decode()}
    ).structured_content["itemId"]
    _call(
        server,
        "render_format",
        {
            "format_id": "solo_explainer",
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [{"type": "narration", "text": "hi"}],
            },
            "bed_asset_id": bed_id,
            "sting_asset_id": sting_id,
        },
    )
    ws = Workspace.for_email(OWNER)
    assert calls["bed_asset"] == ws.asset_path(bed_id)
    assert calls["sting_asset"] == ws.asset_path(sting_id)


def test_render_production_rejects_empty_asset_id():
    server = _local_server(ledger={})
    with pytest.raises(Exception) as ei:
        _call(
            server,
            "render_production",
            {
                "script": {
                    "title": "t",
                    "id_slug": "01",
                    "beats": [{"type": "narration", "text": "hi"}],
                },
                "bed_asset_id": "",
            },
        )
    assert "empty" in str(ei.value)


def test_render_format_refuses_bed_asset_under_music_bed_none_format(monkeypatch):
    # braidio#43: a music_bed="none" format (e.g. interview_host_removed / the
    # "song_exploder" style) never renders a bed — bed_asset_id must be refused
    # BEFORE any render, and braidio.render_format must never even be called.
    import base64

    called = []
    monkeypatch.setattr(
        braidio, "render_format", lambda *a, **kw: called.append(1) or Path("x")
    )
    server = _local_server(ledger={})
    bed_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"BED").decode()}
    ).structured_content["itemId"]
    with pytest.raises(Exception) as ei:
        _call(
            server,
            "render_format",
            {
                "format_id": "interview_host_removed",
                "script": {
                    "title": "t",
                    "id_slug": "01",
                    "beats": [{"type": "narration", "text": "hi"}],
                },
                "bed_asset_id": bed_id,
            },
        )
    assert "music_bed" in str(ei.value)
    assert not called


def test_render_format_reports_sting_applied_in_result(monkeypatch):
    def _stub(fmt, scr, *, out_path, **kw):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"EP")
        return Path(out_path)

    monkeypatch.setattr(braidio, "render_format", _stub)
    server = _local_server(ledger={})
    result = _call(
        server,
        "render_format",
        {
            "format_id": "deep_dive",
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [{"type": "scene_break"}],
            },
        },
    ).structured_content
    assert result["sting_applied"] is None  # no sting_asset_id supplied

    import base64

    sting_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"STING").decode()}
    ).structured_content["itemId"]
    applied = _call(
        server,
        "render_format",
        {
            "format_id": "deep_dive",
            "script": {
                "title": "t",
                "id_slug": "02",
                "beats": [{"type": "scene_break"}],
            },
            "sting_asset_id": sting_id,
        },
    ).structured_content
    assert applied["sting_applied"] is True
    assert applied["sting_ignored_reason"] is None

    ignored = _call(
        server,
        "render_format",
        {
            "format_id": "interview_host_removed",  # scene_marker default "none"
            "script": {
                "title": "t",
                "id_slug": "03",
                "beats": [{"type": "scene_break"}],
            },
            "sting_asset_id": sting_id,
        },
    ).structured_content
    assert ignored["sting_applied"] is False
    assert ignored["sting_ignored_reason"]


def test_render_format_omits_bed_and_sting_by_default(monkeypatch):
    calls = {}

    def _stub(fmt, scr, *, out_path, **kw):
        calls.update(kw)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"EP")
        return Path(out_path)

    monkeypatch.setattr(braidio, "render_format", _stub)
    server = _local_server(ledger={})
    _call(
        server,
        "render_format",
        {
            "format_id": "solo_explainer",
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [{"type": "narration", "text": "hi"}],
            },
        },
    )
    assert calls["bed_asset"] is None
    assert calls["sting_asset"] is None


def test_metering_records_error_status(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(braidio, "narrate", _boom)
    ledger = {}
    server = _local_server(ledger=ledger)
    with pytest.raises(Exception):
        _call(server, "narrate", {"text": "hi"})
    entry = [e for e in ledger.values() if e["tool"] == "narrate"][0]
    assert entry["status"] == "error"
    assert "kaboom" in entry["error"]


def test_ledger_refuses_when_unwritable(monkeypatch):
    class _Broken(dict):
        def __setitem__(self, k, v):
            raise OSError("disk full")

    server = _local_server(ledger=_Broken())
    with pytest.raises(Exception) as ei:
        _call(server, "list_formats", {})
    assert "ledger unavailable" in str(ei.value)


def test_unknown_pool_and_format_raise():
    server = _local_server(ledger={})
    with pytest.raises(Exception):
        _call(server, "assign_voices", {"n": 2, "pool": "nope"})


@_NW
def test_create_and_list_projects():
    server = _local_server(ledger={})
    _call(server, "create_project", {"project_id": "demo", "title": "Demo"})
    r = _call(server, "list_projects", {})
    (row,) = r.structured_content["projects"]
    assert row["project_id"] == "demo" and row["title"] == "Demo"
    # `modified` stays on the row now (the delivery seam's cross-genre union
    # sorts on it); `created` is None because project.json records no stamp.
    assert row["modified"] is not None and row["created"] is None


@_NW
def test_weave_project_records_cost(monkeypatch):
    server = _local_server(ledger={})
    _call(server, "create_project", {"project_id": "demo", "title": "Demo"})

    episode = type(
        "Ann",
        (),
        {
            "id": "9a23da78-0a3e-4acf-a557-48bd6e519038",
            # artifact_id here is the lacing CONTENT hash — deliberately NOT
            # what resolve() accepts. The tool must hand back the annotation
            # id, the one id a download claim can be minted against
            # (braidio#32: returning only the body left callers holding an id
            # every retrieval tool refused).
            "body": {
                "url": "file:///x.mp3",
                "duration_s": 1.0,
                "artifact_id": "c" * 64,
            },
        },
    )()
    monkeypatch.setattr(braidio, "weave_project", lambda proj, scr, **kw: episode)
    ledger = {}
    server2 = _local_server(ledger=ledger)
    r = _call(
        server2,
        "weave_project",
        {
            "project_id": "demo",
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [{"type": "narration", "text": "n" * 1000}],
            },
        },
    )
    assert r.structured_content["cost_usd"] == pytest.approx(0.30)
    assert r.structured_content["url"] == "file:///x.mp3"
    retrieval = r.structured_content["retrieval"]
    assert retrieval["download"] == {
        "genre": "braidio",
        "project_id": "demo",
        "artifact_id": "9a23da78-0a3e-4acf-a557-48bd6e519038",
    }
    assert "9a23da78-0a3e-4acf-a557-48bd6e519038" in retrieval["note"]
    assert "c" * 64 not in retrieval["note"]


# --- assistance + document ingestion ----------------------------------------


def test_help_tool_served():
    server = _local_server(ledger={})
    r = _call(server, "help", {})
    assert "what_is_braidio" in r.structured_content
    assert "render_COSTED" in r.structured_content["tools"]
    assert any("ingest_document" in step for step in r.structured_content["workflow"])


def test_docs_extract_text_html_and_plain():
    from braidio.mcp import _docs

    html = b"<html><body><p>Hi <b>there</b></p><script>x=1</script></body></html>"
    assert _docs.extract_text(html, "text/html") == "Hi there"
    assert _docs.extract_text(b"plain body", "text/plain") == "plain body"


@pytest.mark.parametrize(
    "bad_uri",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "http://localhost/x",
        "http://127.0.0.1/x",
        "http://192.168.0.1/x",
        "http://169.254.169.254/latest/meta-data",  # cloud metadata SSRF
    ],
)
def test_docs_fetch_ssrf_guard(bad_uri):
    from braidio.mcp import _docs

    with pytest.raises(ValueError):
        _docs.fetch_uri(bad_uri)  # rejected before any network call


@_NW
def test_ingest_document_requires_project_then_stores_text():
    server = _local_server(ledger={})
    with pytest.raises(Exception) as ei:
        _call(server, "ingest_document", {"project_id": "p1", "text": "hello"})
    assert "create_project" in str(ei.value)

    _call(server, "create_project", {"project_id": "p1", "title": "P1"})
    r = _call(server, "ingest_document", {"project_id": "p1", "text": "A " * 100})
    assert r.structured_content["characters"] == 200
    assert r.structured_content["source"] == "inline"
    assert r.structured_content["text"].startswith("A ")


@_NW
def test_save_script_links_narration_beats():
    server = _local_server(ledger={})
    _call(server, "create_project", {"project_id": "p2", "title": "P2"})
    r = _call(
        server,
        "save_script",
        {
            "project_id": "p2",
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [{"type": "narration", "text": "hello"}],
            },
        },
    )
    beats = r.structured_content["beats"]
    assert len(beats) == 1 and beats[0]["kind"] == "narration"


# --- delivery register (braidio#1) ------------------------------------------


def test_narrate_delivery_selects_register(monkeypatch):
    captured = {}

    def _fake(text, out, **kw):
        captured.update(kw)
        return Path(out)

    monkeypatch.setattr(braidio, "narrate", _fake)
    server = _local_server(ledger={})

    _call(server, "narrate", {"text": "hi", "delivery": "conversational"})
    assert captured["model_id"] == "eleven_v3"
    assert captured["voice_settings"] == braidio.CONVERSATIONAL.voice_settings

    captured.clear()
    _call(server, "narrate", {"text": "hi"})  # default register = narration
    assert captured["model_id"] == "eleven_multilingual_v2"
    assert captured["voice_settings"] == braidio.NARRATION.voice_settings


def test_unknown_delivery_raises(monkeypatch):
    monkeypatch.setattr(braidio, "narrate", lambda *a, **k: Path("/tmp/x"))
    server = _local_server(ledger={})
    with pytest.raises(Exception) as ei:
        _call(server, "narrate", {"text": "hi", "delivery": "bogus"})
    assert "unknown delivery" in str(ei.value)


# --- assets (braidio#10) ----------------------------------------------------
# Assets are per-USER (no project needed), so one-shot renders share them too.


def test_upload_asset_dedups_lists_and_gets():
    import base64

    from dol import content_hash

    server = _local_server(ledger={})  # no create_project — assets are project-less
    payload = b"FAKE-AUDIO-BYTES"
    b64 = base64.b64encode(payload).decode()
    meta = _call(
        server, "upload_asset", {"data_b64": b64, "name": "song.mp3"}
    ).structured_content
    assert meta["itemId"] == content_hash(payload)  # content-addressed
    assert meta["hash"] == meta["itemId"] and meta["size"] == len(payload)
    assert meta["mimeType"].startswith("audio/") and meta["name"] == "song.mp3"

    # identical bytes dedupe to one stored asset
    again = _call(server, "upload_asset", {"data_b64": b64}).structured_content
    assert again["itemId"] == meta["itemId"]
    listed = _call(server, "list_assets", {}).structured_content["assets"]
    assert len(listed) == 1 and listed[0]["itemId"] == meta["itemId"]
    got = _call(server, "get_asset", {"asset_id": meta["itemId"]}).structured_content
    assert got["itemId"] == meta["itemId"]


def test_upload_asset_requires_uri_or_data():
    server = _local_server(ledger={})
    with pytest.raises(Exception) as ei:  # neither uri nor data_b64
        _call(server, "upload_asset", {})
    assert "uri" in str(ei.value) or "data_b64" in str(ei.value)


def test_asset_reference_resolves_to_local_path():
    import base64
    from pathlib import Path as P

    server = _local_server(ledger={})
    item_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"MEDIA").decode()}
    ).structured_content["itemId"]

    ws = Workspace.for_email(
        OWNER
    )  # the resolver weave/render use for `source.asset_id`
    path = ws.asset_path(item_id)
    assert P(path).exists() and P(path).read_bytes() == b"MEDIA"
    with pytest.raises(FileNotFoundError):
        ws.asset_path("deadbeefdeadbeef")


def test_oneshot_render_resolves_asset_id(monkeypatch):
    # A project-LESS render_production resolves source.asset_id to the uploaded file.
    import base64
    from pathlib import Path as P

    captured = {}

    def _stub(scr, *, out_path, **kw):
        captured.update(kw)
        P(out_path).parent.mkdir(parents=True, exist_ok=True)
        P(out_path).write_bytes(b"EP")
        return P(out_path)

    monkeypatch.setattr(braidio, "render_production", _stub)
    server = _local_server(ledger={})
    item_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"SONG").decode()}
    ).structured_content["itemId"]
    _call(
        server,
        "render_production",
        {
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [{"type": "segment", "reference": "line-0"}],
            },
            "source": {
                "lines": [{"index": 0, "start_s": 0, "end_s": 1, "text": "hi"}],
                "asset_id": item_id,
            },
        },
    )
    src = captured["source"]  # the TimedLineSegmentSource braidio.render_production got
    assert str(src._asset) == Workspace.for_email(OWNER).asset_path(item_id)
    assert P(src._asset).read_bytes() == b"SONG"


def test_docs_rejects_disallowed_port(monkeypatch):
    from braidio.mcp import _docs

    monkeypatch.setattr(_docs, "_host_is_public", lambda h, **kw: True)
    with pytest.raises(ValueError, match="port"):
        _docs._validate_target("http://example.com:8080/x")
    _docs._validate_target("https://example.com/x")  # 443 default is fine


def _scripted_connector(responses, hops):
    """A graze ``connector`` seam: records the pinned address, serves canned HTTP."""
    import socket
    import threading

    def connect(address, timeout):
        hops.append(address[0])
        client, server = socket.socketpair()

        def serve():
            server.recv(65536)
            server.sendall(responses[len(hops) - 1])
            server.close()

        threading.Thread(target=serve, daemon=True).start()
        return client

    return connect


def test_docs_redirect_to_private_is_rejected():
    # A public URL that 302s to an internal host must be refused, and the internal
    # target must never be connected to.
    from braidio.mcp import _docs

    hops = []
    redirect = b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/secret\r\n\r\n"
    with pytest.raises(ValueError):
        _docs.fetch_uri(
            "http://public.example/start",
            resolver=lambda host, port: (
                ["93.184.216.34"] if host == "public.example" else [host]
            ),
            connector=_scripted_connector([redirect], hops),
        )
    assert hops == ["93.184.216.34"]  # never connected to the private target


def test_docs_fetch_pins_the_address_it_validated():
    # DNS rebinding: the resolver answers public the first time, internal after.
    # The connection must go to the address that was checked, with one lookup.
    from braidio.mcp import _docs

    lookups, hops = [], []

    def rebinding_resolver(host, port):
        lookups.append(host)
        return ["93.184.216.34"] if len(lookups) == 1 else ["127.0.0.1"]

    body = b"HTTP/1.1 200 OK\r\nContent-Type: Text/Plain; charset=utf-8\r\nContent-Length: 2\r\n\r\nhi"
    data, ctype = _docs.fetch_uri(
        "http://rebind.example/x",
        resolver=rebinding_resolver,
        connector=_scripted_connector([body], hops),
    )
    assert (data, ctype) == (b"hi", "text/plain")
    assert hops == ["93.184.216.34"] and len(lookups) == 1


def test_docs_fetch_enforces_the_byte_cap():
    from braidio.mcp import _docs

    body = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n" + b"x" * 100
    with pytest.raises(ValueError, match="limit"):
        _docs.fetch_uri(
            "http://big.example/x",
            resolver=lambda host, port: ["93.184.216.34"],
            connector=_scripted_connector([body], []),
            max_bytes=10,
        )


@_NW
def test_save_script_ingests_dialogue_beats_under_the_formats_cast():
    """braidio#46: the graph pipeline used to refuse a dialogue beat outright.
    It now links it, cast by the format (deep_dive's host_a / host_b), so
    weave_project can render it in one Text-to-Dialogue pass."""
    server = _local_server(ledger={})
    _call(server, "create_project", {"project_id": "pd", "title": "PD"})
    out = _call(
        server,
        "save_script",
        {
            "project_id": "pd",
            "format_id": "deep_dive",
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [
                    {"type": "narration", "text": "one"},
                    {
                        "type": "dialogue",
                        "turns": [["host_a", "hi"], ["host_b", "hey"]],
                    },
                ],
            },
        },
    ).structured_content
    assert [b["kind"] for b in out["beats"]] == ["narration", "dialogue"]


@_NW
def test_save_script_refuses_a_dialogue_role_the_cast_lacks_before_writing():
    """A turn role the cast does not name fails in the plan, naming the roles
    the cast has — never a KeyError inside a paid render, never a half-written
    graph (braidio#46, #49)."""
    server = _local_server(ledger={})
    _call(server, "create_project", {"project_id": "pr", "title": "PR"})
    with pytest.raises(Exception) as ei:
        _call(
            server,
            "save_script",
            {
                "project_id": "pr",
                "format_id": "deep_dive",
                "script": {
                    "title": "t",
                    "id_slug": "01",
                    "beats": [{"type": "dialogue", "turns": [["A", "hi"]]}],
                },
            },
        )
    assert "host_a" in str(ei.value) and "['A']" in str(ei.value)


def test_save_script_ingests_scene_break_beats():
    # braidio#39: the graph pipeline used to refuse a scene_break outright.
    # It now links it as an ordered structural boundary, so weave_project can
    # mark it with a sting (or a pause).
    server = _local_server(ledger={})
    _call(server, "create_project", {"project_id": "ps", "title": "PS"})
    out = _call(
        server,
        "save_script",
        {
            "project_id": "ps",
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [
                    {"type": "narration", "text": "one"},
                    {"type": "scene_break", "label": "act 2"},
                    {"type": "narration", "text": "two"},
                ],
            },
        },
    ).structured_content
    assert [b["kind"] for b in out["beats"]] == [
        "narration",
        "scene_break",
        "narration",
    ]


@_NW
def test_save_script_applies_the_rights_profile(tmp_path, monkeypatch):
    """braidio#47: the graph tools had no `profile` at all, so a caller could
    link (and then render) a personal-only clip into a published episode.

    `published` now refuses the clip at ingest — it never becomes a node — and
    the tool reports what the profile decided.
    """
    import base64

    monkeypatch.setenv("BRAIDIO_DATA_HOME", str(tmp_path))
    server = _local_server(ledger={})
    _call(server, "create_project", {"project_id": "pr", "title": "PR"})
    item_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"SONG").decode()}
    ).structured_content["itemId"]
    args = {
        "project_id": "pr",
        "script": {
            "title": "t",
            "id_slug": "01",
            "beats": [
                {"type": "narration", "text": "one"},
                {
                    "type": "segment",
                    "reference": "the famous hook",
                    "label": "hook",
                    "rights": "owned-local",
                },
            ],
        },
        "source": {
            "lines": [
                {"index": 0, "start_s": 0, "end_s": 1, "text": "the famous hook"}
            ],
            "asset_id": item_id,
        },
    }

    published = _call(
        server, "save_script", {**args, "profile": "published"}
    ).structured_content
    assert [b["kind"] for b in published["beats"]] == ["narration"]
    assert published["dropped"] == ["hook"] and published["profile"] == "published"

    # …and the default cut is unchanged: personal still plays it.
    _call(server, "create_project", {"project_id": "pr2", "title": "PR2"})
    personal = _call(
        server, "save_script", {**args, "project_id": "pr2"}
    ).structured_content
    assert [b["kind"] for b in personal["beats"]] == ["narration", "segment"]
    assert personal["profile"] == "personal" and personal["dropped"] == []


@pytest.mark.parametrize(
    "tool,args",
    [
        ("plan_production", {}),
        ("render_production", {}),
        ("render_format", {"format_id": "solo_explainer"}),
    ],
)
def test_an_unknown_profile_is_a_tool_error_naming_the_choices(tool, args):
    """A bad `profile` must arrive as a ToolError listing the valid values, not
    as whatever raw ValueError `Profile()` happens to raise (braidio#47 review).
    """
    server = _local_server(ledger={})
    with pytest.raises(Exception) as ei:
        _call(
            server,
            tool,
            {
                **args,
                "script": {"title": "t", "id_slug": "01", "beats": []},
                "profile": "commercial",
            },
        )
    message = str(ei.value)
    assert "commercial" in message and "personal" in message and "published" in message


@_NW
def test_weave_project_costs_the_profile_it_was_given(monkeypatch):
    """The tool reported ``_render_cost(scr, "personal")`` whatever profile was
    asked for. Under ``published`` a dropped clip's beats must not be costed as
    if they rendered (braidio#47)."""
    captured = {}

    episode = type(
        "Ann",
        (),
        {
            "id": "9a23da78-0a3e-4acf-a557-48bd6e519038",
            "body": {"url": "file:///x.mp3"},
        },
    )()

    def _weave(proj, scr, **kw):
        captured.update(kw)
        return episode

    monkeypatch.setattr(braidio, "weave_project", _weave)
    server = _local_server(ledger={})
    _call(server, "create_project", {"project_id": "wp", "title": "WP"})
    r = _call(
        server,
        "weave_project",
        {
            "project_id": "wp",
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [
                    {
                        "type": "narration",
                        "text": "n" * 1000,
                        "published_text": "short",
                    }
                ],
            },
            "profile": "published",
        },
    )
    assert captured["profile"] is braidio.Profile.PUBLISHED
    # the published rewrite is what gets synthesized, so it is what gets costed
    assert r.structured_content["characters"] == len("short")


def test_save_script_records_the_format_and_sting_asset_in_the_graph():
    # braidio#39: the tool takes an asset ID (never a server path) and the graph
    # records the production's structural-music decision, so weave_project can
    # play a sting at each scene_break.
    import base64

    import nw

    server = _local_server(ledger={})
    _call(server, "create_project", {"project_id": "pf", "title": "PF"})
    asset = _call(
        server,
        "upload_asset",
        {"data_b64": base64.b64encode(b"STING-BYTES").decode(), "name": "hit.mp3"},
    ).structured_content
    _call(
        server,
        "save_script",
        {
            "project_id": "pf",
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [
                    {"type": "narration", "text": "one"},
                    {"type": "scene_break"},
                ],
            },
            "format_id": "debate",
            "sting_asset_id": asset["itemId"],
        },
    )
    proj = bmcp.workspace.Workspace.for_email(OWNER).open_project("pf")
    (structure,) = nw.annotations_at_tier(proj.root, "production-structures")
    assert structure.body["sting_asset_id"]
    assert structure.body["sting_url"].startswith("file://")
    assert (
        structure.body["structure"]["scene_marker"]
        == braidio.FORMATS["debate"].structure.scene_marker
    )


@_NW
def test_save_script_refuses_bed_asset_under_music_bed_none_format(monkeypatch):
    # braidio#53: the graph path has the same bed_for_intensity swallow bug #43
    # fixed on the fast path — save_script must refuse bed_asset_id BEFORE
    # ingest (no beats/structure written), never silently drop it.
    import base64

    import braidio.transforms as transforms

    called = []
    monkeypatch.setattr(transforms, "ingest_script", lambda *a, **kw: called.append(1))
    server = _local_server(ledger={})
    _call(server, "create_project", {"project_id": "pf2", "title": "PF2"})
    bed_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"BED").decode()}
    ).structured_content["itemId"]
    with pytest.raises(Exception) as ei:
        _call(
            server,
            "save_script",
            {
                "project_id": "pf2",
                "script": {
                    "title": "t",
                    "id_slug": "01",
                    "beats": [{"type": "narration", "text": "hi"}],
                },
                "format_id": "interview_host_removed",
                "bed_asset_id": bed_id,
            },
        )
    assert "music_bed" in str(ei.value)
    assert bed_id in str(ei.value)
    assert not called
    import nw

    proj = bmcp.workspace.Workspace.for_email(OWNER).open_project("pf2")
    assert nw.annotations_at_tier(proj.root, "narrative-beats") == []


@_NW
def test_weave_project_refuses_bed_asset_under_music_bed_none_format(monkeypatch):
    # Same refusal, on the render entry point: braidio.weave_project (the
    # transforms-level pipeline) must never even be called.
    import base64

    called = []
    monkeypatch.setattr(braidio, "weave_project", lambda *a, **kw: called.append(1))
    server = _local_server(ledger={})
    _call(server, "create_project", {"project_id": "wp2", "title": "WP2"})
    bed_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"BED").decode()}
    ).structured_content["itemId"]
    with pytest.raises(Exception) as ei:
        _call(
            server,
            "weave_project",
            {
                "project_id": "wp2",
                "script": {
                    "title": "t",
                    "id_slug": "01",
                    "beats": [{"type": "narration", "text": "hi"}],
                },
                "format_id": "interview_host_removed",
                "bed_asset_id": bed_id,
            },
        )
    assert "music_bed" in str(ei.value)
    assert bed_id in str(ei.value)
    assert not called


@_NW
def test_save_script_without_a_format_never_refuses_bed_asset():
    # No format declared means no music_bed to refuse against — the guard only
    # fires when format_id resolves to a Format (mirrors render_production,
    # which has no format param at all and never refuses). A supplied sting
    # still gets reported: with no format, the fallback base structure is the
    # MusicStructure default (scene_marker="sting"), so a bare scene_break
    # DOES wire it in — sting_applied must be True, not None (braidio#53
    # review nit: the guard being skipped must not also skip reporting).
    import base64

    import nw

    server = _local_server(ledger={})
    _call(server, "create_project", {"project_id": "wp3", "title": "WP3"})
    bed_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"BED").decode()}
    ).structured_content["itemId"]
    sting_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"STING").decode()}
    ).structured_content["itemId"]
    result = _call(
        server,
        "save_script",
        {
            "project_id": "wp3",
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [
                    {"type": "narration", "text": "hi"},
                    {"type": "scene_break"},
                ],
            },
            "bed_asset_id": bed_id,
            "sting_asset_id": sting_id,
        },
    ).structured_content
    assert result["sting_applied"] is True
    assert result["sting_ignored_reason"] is None
    proj = bmcp.workspace.Workspace.for_email(OWNER).open_project("wp3")
    (structure,) = nw.annotations_at_tier(proj.root, "production-structures")
    assert structure.body["bed_asset_id"] == bed_id


@_NW
def test_save_script_reports_sting_applied_in_result():
    import base64

    server = _local_server(ledger={})
    _call(server, "create_project", {"project_id": "st1", "title": "ST1"})
    result = _call(
        server,
        "save_script",
        {
            "project_id": "st1",
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [{"type": "scene_break"}],
            },
            "format_id": "deep_dive",
        },
    ).structured_content
    assert result["sting_applied"] is None  # no sting_asset_id supplied

    sting_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"STING").decode()}
    ).structured_content["itemId"]
    applied = _call(
        server,
        "save_script",
        {
            "project_id": "st1",
            "script": {
                "title": "t",
                "id_slug": "02",
                "beats": [{"type": "scene_break"}],
            },
            "format_id": "deep_dive",
            "sting_asset_id": sting_id,
        },
    ).structured_content
    assert applied["sting_applied"] is True
    assert applied["sting_ignored_reason"] is None

    ignored = _call(
        server,
        "save_script",
        {
            "project_id": "st1",
            "script": {
                "title": "t",
                "id_slug": "03",
                "beats": [{"type": "scene_break"}],
            },
            "format_id": "interview_host_removed",  # scene_marker default "none"
            "sting_asset_id": sting_id,
        },
    ).structured_content
    assert ignored["sting_applied"] is False
    assert ignored["sting_ignored_reason"]


@_NW
def test_weave_project_reports_sting_applied_in_result(monkeypatch):
    episode = type(
        "Ann",
        (),
        {
            "id": "9a23da78-0a3e-4acf-a557-48bd6e519038",
            "body": {"url": "file:///x.mp3"},
        },
    )()
    monkeypatch.setattr(braidio, "weave_project", lambda proj, scr, **kw: episode)
    server = _local_server(ledger={})
    _call(server, "create_project", {"project_id": "st2", "title": "ST2"})

    import base64

    sting_id = _call(
        server, "upload_asset", {"data_b64": base64.b64encode(b"STING").decode()}
    ).structured_content["itemId"]
    ignored = _call(
        server,
        "weave_project",
        {
            "project_id": "st2",
            "script": {
                "title": "t",
                "id_slug": "01",
                "beats": [{"type": "scene_break"}],
            },
            "format_id": "interview_host_removed",  # scene_marker default "none"
            "sting_asset_id": sting_id,
        },
    ).structured_content
    assert ignored["sting_applied"] is False
    assert ignored["sting_ignored_reason"]


# --- download_audio (yt-dlp via yb) + identity fallback (reelee#232) ---------


def test_download_audio_stores_asset_without_network(monkeypatch, tmp_path):
    # download_audio lazy-imports yb.download; inject a fake so no yt-dlp/network runs.
    import sys
    import types

    import braidio.mcp._docs as _docs

    monkeypatch.setattr(
        _docs, "_validate_target", lambda url: None
    )  # skip DNS/SSRF check

    mp3 = tmp_path / "grabbed.mp3"
    mp3.write_bytes(b"ID3\x03fake-mp3-bytes")

    class _Res:
        path = str(mp3)
        info = {"title": "My Song", "duration": 200}
        sidecars = {}

    fake = types.ModuleType("yb.download")
    fake.download_youtube_video = lambda url, **kw: _Res()
    fake.youtube_video_info = lambda url, **kw: {"title": "My Song", "duration": 200}
    monkeypatch.setitem(sys.modules, "yb", types.ModuleType("yb"))
    monkeypatch.setitem(sys.modules, "yb.download", fake)

    server = _local_server()
    out = _call(
        server, "download_audio", {"url": "https://example.com/song"}
    ).structured_content
    assert out["kind"] == "audio"
    assert out["name"] == "My Song"
    assert out["duration"] == 200
    assert out["source"] == "https://example.com/song"
    assert "copyright" in out["copyright_notice"].lower()
    # stored content-addressed in the library (same shape as upload_asset) + listed
    assert out["_tag"] == "ContentRef"
    listed = _call(server, "list_assets", {}).structured_content["assets"]
    assert any(a.get("name") == "My Song" and a.get("kind") == "audio" for a in listed)


def test_download_audio_registered_free():
    assert "download_audio" in bmcp.FREE_TOOLS
    assert "download_audio" not in bmcp.COSTED_TOOLS


def test_download_audio_rejects_ssrf_target():
    # A server-side fetch to a loopback/metadata host must be refused (no network:
    # 127.0.0.1 is a literal IP, so _validate_target rejects it without DNS).
    from fastmcp.exceptions import ToolError

    from braidio.mcp.tools import download_audio

    with pytest.raises(ToolError):
        download_audio("http://127.0.0.1:8010/api/secrets")
    with pytest.raises(ToolError):
        download_audio("http://169.254.169.254/latest/meta-data/")


def test_current_email_falls_back_to_token(monkeypatch):
    # reelee#232 decoupling: under a NON-braidio middleware (no braidio contextvar set),
    # current_email resolves the caller directly from the verified OAuth token — so
    # braidio's tools work under the connector's shared enlace_metering middleware.
    _mock_token(monkeypatch, "Owner@X.com")
    assert metering.current_email() == "owner@x.com"


# --- commentary_weave project-factory + tool aggregation (braidio#18) --------


def test_commentary_weave_project_factory_creates_in_caller_workspace(tmp_path):
    # The nw project-factory a host connector calls (via nw.create_genre_project) to
    # create a commentary project in the CALLER's own braidio workspace. (_isolated_home
    # already points BRAIDIO_DATA_HOME at tmp_path.)
    import nw

    assert nw.has_genre_project_factory("commentary_weave")
    info = nw.create_genre_project(
        "commentary_weave",
        "owner@example.com",
        "myshow",
        title="My Show",
        template="solo_explainer",
    )
    assert info["project_id"] == "myshow"
    assert info["genre"] == "commentary_weave"
    assert info["params"] == {"format_id": "solo_explainer"}
    # created in the caller's per-user workspace, addressable by project_id
    ws = Workspace.for_email("owner@example.com", root=tmp_path)
    proj = ws.open_project("myshow")  # raises if absent
    assert proj is not None


def test_register_tools_prefixes_and_excludes():
    from fastmcp import FastMCP

    srv = FastMCP(name="agg-test", on_duplicate="error")
    registered = bmcp.register_tools(
        srv, prefix="braidio_", exclude={"create_project", "describe_genre"}
    )
    assert "braidio_download_audio" in registered
    assert "braidio_create_project" not in registered  # excluded (host owns create)
    assert (
        "braidio_describe_genre" not in registered
    )  # excluded (host catalog covers it)
    assert all(n.startswith("braidio_") for n in registered)


def test_download_audio_passes_configured_cookies_to_ytdlp(monkeypatch, tmp_path):
    """braidio#23: credentials must reach BOTH yt-dlp calls, without widening a bound.

    The bot gate fires during metadata extraction, so cookies on the download call
    alone would still fail — and braidio's own `max_filesize` must survive the
    merge of the deployment's options.
    """
    import sys
    import time
    import types

    import braidio.mcp._docs as _docs
    from braidio.mcp import _media

    monkeypatch.setattr(_docs, "_validate_target", lambda url: None)

    jar = tmp_path / "cookies.txt"
    jar.write_text(
        "# Netscape HTTP Cookie File\n"
        f".youtube.com\tTRUE\t/\tTRUE\t{int(time.time() + 86400)}\tLOGIN_INFO\tv\n"
    )
    monkeypatch.setenv(_media.COOKIES_FILE_ENV_VAR, str(jar))

    mp3 = tmp_path / "grabbed.mp3"
    mp3.write_bytes(b"ID3\x03fake-mp3-bytes")
    seen = {}

    class _Res:
        path = str(mp3)
        info = {"title": "S", "duration": 10}
        sidecars = {}

    def _info(url, **kw):
        seen["info"] = kw.get("extra_opts") or {}
        return {"title": "S", "duration": 10}

    def _download(url, **kw):
        seen["download"] = kw.get("extra_opts") or {}
        return _Res()

    fake = types.ModuleType("yb.download")
    fake.youtube_video_info = _info
    fake.download_youtube_video = _download
    monkeypatch.setitem(sys.modules, "yb", types.ModuleType("yb"))
    monkeypatch.setitem(sys.modules, "yb.download", fake)

    _call(_local_server(), "download_audio", {"url": "https://example.com/s"})

    assert seen["info"]["cookiefile"] == str(jar)
    assert seen["download"]["cookiefile"] == str(jar)
    # braidio's bound is written after the deployment's options, so it stands.
    from braidio.mcp.tools import _AUDIO_MAX_BYTES

    assert seen["download"]["max_filesize"] == _AUDIO_MAX_BYTES


def test_download_audio_surfaces_bot_gate_as_auth_error(monkeypatch, tmp_path):
    """A bot gate becomes an error naming its own fix, not a raw yt-dlp string."""
    import sys
    import types

    import braidio.mcp._docs as _docs
    from braidio.mcp import _media
    from braidio.mcp.tools import download_audio

    monkeypatch.setattr(_docs, "_validate_target", lambda url: None)
    for var in (
        _media.COOKIES_FILE_ENV_VAR,
        _media.COOKIES_FROM_BROWSER_ENV_VAR,
        _media.EXTRACTOR_ARGS_ENV_VAR,
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BRAIDIO_DATA_HOME", str(tmp_path))

    def _gated(url, **kw):
        raise RuntimeError(
            "ERROR: [youtube] x: Sign in to confirm you're not a bot. "
            "Use --cookies-from-browser or --cookies for the authentication."
        )

    fake = types.ModuleType("yb.download")
    fake.youtube_video_info = _gated
    fake.download_youtube_video = _gated
    monkeypatch.setitem(sys.modules, "yb", types.ModuleType("yb"))
    monkeypatch.setitem(sys.modules, "yb.download", fake)

    with pytest.raises(_media.SourceAuthRequired) as excinfo:
        download_audio("https://example.com/s")
    message = str(excinfo.value)
    assert _media.COOKIES_FILE_ENV_VAR in message
    assert "--cookies-from-browser" not in message  # no raw yt-dlp string leaks
