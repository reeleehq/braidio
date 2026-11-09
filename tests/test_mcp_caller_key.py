"""The MCP boundary resolves the caller's ElevenLabs key from the request, once.

thorwhalen/braidio#58: every costed tool threads ``api_key=`` from
:func:`braidio.mcp.credentials.caller_elevenlabs_key` — the ``X-Elevenlabs-Key``
header — so the one-shot renders and ``weave_project`` bill the same key, and
a request without the header bills the server's (``api_key=None``). The key
is never a tool argument and never reaches the usage ledger.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import braidio
import braidio.mcp as bmcp
from braidio.mcp import credentials

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402

SENTINEL = "sk-SENTINEL-3c9d1e5f-must-never-persist"
OWNER = "owner@example.com"


@pytest.fixture(autouse=True)
def _isolated_data_home(monkeypatch, tmp_path):
    """Per-user projects/renders under tmp, never the real data root."""
    monkeypatch.setenv("BRAIDIO_DATA_HOME", str(tmp_path))


def _local_server(**kw):
    kw.setdefault("local_user", OWNER)
    return bmcp.build_server(**kw)


def _call(server, tool, args):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args)

    return asyncio.run(go())


@pytest.fixture
def header(monkeypatch):
    """Make the in-flight request carry ``X-Elevenlabs-Key`` (or not)."""
    import fastmcp.server.dependencies as deps

    state = {"value": None}

    def _headers(include_all=False, include=None):
        return (
            {credentials.ELEVENLABS_KEY_HEADER: state["value"]}
            if state["value"]
            else {}
        )

    monkeypatch.setattr(deps, "get_http_headers", _headers)
    return state


def test_caller_key_reads_the_header_case_insensitively(monkeypatch):
    import fastmcp.server.dependencies as deps

    monkeypatch.setattr(
        deps, "get_http_headers", lambda **kw: {"X-Elevenlabs-Key": f" {SENTINEL} "}
    )
    assert credentials.caller_elevenlabs_key() == SENTINEL
    monkeypatch.setattr(deps, "get_http_headers", lambda **kw: {})
    assert credentials.caller_elevenlabs_key() is None
    monkeypatch.setattr(
        deps, "get_http_headers", lambda **kw: {credentials.ELEVENLABS_KEY_HEADER: ""}
    )
    assert credentials.caller_elevenlabs_key() is None


def test_no_http_request_means_no_key():
    """stdio / dev: fastmcp returns no headers, and the answer is the env fallback."""
    assert credentials.caller_elevenlabs_key() is None


def test_weave_project_threads_the_header_key_and_none_without_it(monkeypatch, header):
    seen = []
    episode = type(
        "Ann",
        (),
        {
            "id": "9a23da78-0a3e-4acf-a557-48bd6e519038",
            "body": {
                "url": "file:///x.mp3",
                "duration_s": 1.0,
                "artifact_id": "c" * 64,
            },
        },
    )()

    def _weave(proj, scr, **kw):
        seen.append(kw.get("api_key"))
        return braidio.WeaveReceipt(episode)

    monkeypatch.setattr(braidio, "weave_project", _weave)
    ledger = {}
    server = _local_server(ledger=ledger)
    _call(server, "create_project", {"project_id": "demo", "title": "Demo"})
    args = {
        "project_id": "demo",
        "script": {
            "title": "t",
            "id_slug": "01",
            "beats": [{"type": "narration", "text": "hello"}],
        },
    }

    header["value"] = SENTINEL
    _call(server, "weave_project", args)
    header["value"] = None
    _call(server, "weave_project", args)

    assert seen == [SENTINEL, None]
    # The ledger records the call, never the key.
    assert SENTINEL not in json.dumps(ledger, default=str)


@pytest.mark.parametrize(
    "tool, fn, args",
    [
        ("narrate", "narrate", {"text": "hi"}),
        ("render_dialogue", "render_dialogue", {"turns": [["A", "hi"], ["B", "yo"]]}),
        ("render_multivoice", "render_multivoice", {"segments": ["a", "b"]}),
        ("compose_narration", "compose_narration", {"segments": ["a", "b"]}),
    ],
)
def test_every_one_shot_costed_tool_threads_the_same_key(
    monkeypatch, header, tool, fn, args
):
    seen = []

    def _stub(*a, **kw):
        seen.append(kw.get("api_key"))
        out = kw.get("out_path") or a[1]
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"MP3")
        return Path(out)

    monkeypatch.setattr(braidio, fn, _stub)
    server = _local_server(ledger={})
    header["value"] = SENTINEL
    _call(server, tool, args)
    header["value"] = None
    _call(server, tool, args)
    assert seen == [SENTINEL, None]


def test_render_production_and_format_thread_the_key(monkeypatch, header):
    seen = {}

    def _stub(name):
        def _f(*a, out_path, **kw):
            seen.setdefault(name, []).append(kw.get("api_key"))
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"EP")
            return Path(out_path)

        return _f

    monkeypatch.setattr(braidio, "render_production", _stub("production"))
    monkeypatch.setattr(braidio, "render_format", _stub("format"))
    server = _local_server(ledger={})
    script = {
        "title": "t",
        "id_slug": "01",
        "beats": [{"type": "narration", "text": "hello"}],
    }
    header["value"] = SENTINEL
    _call(server, "render_production", {"script": script})
    _call(server, "render_format", {"format_id": "solo_explainer", "script": script})
    assert seen == {"production": [SENTINEL], "format": [SENTINEL]}


def test_the_key_is_not_a_tool_argument():
    """A key must come from the request, never from the model-visible schema."""
    from py2mcp.util import import_object
    import inspect

    for name in bmcp.COSTED_TOOLS:
        fn = import_object(f"braidio.mcp.tools:{name}")
        params = inspect.signature(fn).parameters
        assert not any("key" in p.lower() for p in params), (name, list(params))


def test_a_failing_costed_call_leaves_the_ledger_and_the_error_clean(
    monkeypatch, header
):
    """ElevenLabs quotes the key back on a 401; the ledger row that records
    the failure, and the tool error the model sees, must not carry it."""

    def _rejects(*a, **kw):
        raise RuntimeError(f"ElevenLabs 401: invalid api key {kw.get('api_key')}")

    monkeypatch.setattr(braidio, "narrate", _rejects)
    ledger = {}
    server = _local_server(ledger=ledger)
    header["value"] = SENTINEL
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as info:
        _call(server, "narrate", {"text": "hi"})
    assert SENTINEL not in str(info.value)
    assert "<redacted:elevenlabs>" in str(info.value)
    dumped = json.dumps(ledger, default=str)
    assert SENTINEL not in dumped
    assert "<redacted:elevenlabs>" in dumped
    (row,) = [e for e in ledger.values() if e.get("status") == "error"]
    assert row["tool"] == "narrate"


def test_redaction_helpers_without_a_key_in_flight_are_identity():
    text = f"no key: {SENTINEL}"
    assert credentials.redact_caller_key(text) == text  # no header → nothing to redact
    err = RuntimeError(text)
    assert credentials.scrub_caller_key(err) is err
