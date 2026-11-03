"""The optional layers load whenever their dependencies do (braidio#187).

``braidio/__init__`` imports the graph layer (``lacing``) and the nw layer
behind ``try/except ImportError`` so a bare install still imports. The same
guard hides a *broken* layer: an nw below braidio's floor (a new top-level nw
import, such as ``total_cost``), or a bad import inside braidio, flips
``HAS_NW`` off, and every test marked ``skipif(not braidio.HAS_NW)`` then
skips: green CI on a world where the graph path cannot even import. These
tests turn that into a failure whenever the dependencies themselves import.
"""

from __future__ import annotations

import importlib.util

import pytest

import braidio


def _importable(*names: str) -> bool:
    return all(importlib.util.find_spec(n) is not None for n in names)


@pytest.mark.skipif(not _importable("lacing"), reason="lacing not installed")
def test_the_graph_layer_loads_when_lacing_does():
    assert braidio.HAS_GRAPH, (
        f"lacing imports, braidio's graph layer did not: {braidio.GRAPH_IMPORT_ERROR!r}"
    )


@pytest.mark.skipif(
    not _importable("lacing", "nw", "falaw"), reason="nw-app extra not installed"
)
def test_the_nw_layer_loads_when_nw_does():
    assert braidio.HAS_NW, (
        f"nw imports, braidio's nw layer did not: {braidio.NW_IMPORT_ERROR!r}"
    )
