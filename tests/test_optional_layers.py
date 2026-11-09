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
import os
import subprocess
import sys
import textwrap

import pytest

import braidio

#: Set by CI: where a missing extra is a configuration error, not a reason to skip.
CI_ENV_VAR = "CI"


def _importable(*names: str) -> bool:
    return all(importlib.util.find_spec(n) is not None for n in names)


def _need(*names: str) -> None:
    """Skip when ``names`` are not installed, except in CI, where that fails.

    A guard that skips when its dependencies are absent is silent exactly when the
    extra that provides them was dropped from the CI install (braidio#207).
    """
    if _importable(*names):
        return
    message = f"{', '.join(names)} not installed"
    if os.environ.get(CI_ENV_VAR):
        pytest.fail(f"{message}: CI must install the extras these guards check")
    pytest.skip(message)


def test_the_graph_layer_loads_when_lacing_does():
    _need("lacing")
    assert braidio.HAS_GRAPH, (
        f"lacing imports, braidio's graph layer did not: {braidio._GRAPH_IMPORT_ERROR!r}"
    )


def test_the_nw_layer_loads_when_nw_does():
    _need("lacing", "nw", "falaw")
    assert braidio.HAS_NW, (
        f"nw imports, braidio's nw layer did not: {braidio._NW_IMPORT_ERROR!r}"
    )


def test_the_video_layer_loads_when_its_dependencies_are_found():
    """Same hole on the video path: ``missing_dependencies`` treats a burns that
    is *found* but fails to import as absent, so the render tests skip."""
    _need("burns", "PIL")
    from braidio import video

    assert braidio.HAS_VIDEO, (
        f"burns and PIL are installed, yet video reports {video.missing_dependencies()} missing"
    )


_BLOCK_AND_IMPORT = textwrap.dedent(
    """
    import sys, warnings
    from importlib.abc import MetaPathFinder

    class Block(MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name == {blocked!r}:
                raise {exc}

    sys.meta_path.insert(0, Block())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import braidio
    print(braidio.HAS_GRAPH, braidio.HAS_NW)
    for w in caught:
        if "braidio" in str(w.message):
            print("WARN", w.category.__name__, w.message)
    """
)


def _import_with_blocked(module: str, *, absent: bool = False) -> list[str]:
    exc = (
        f"ModuleNotFoundError({module!r}, name='lacing')"
        if absent
        else 'ImportError("blocked for the test")'
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", _BLOCK_AND_IMPORT.format(blocked=module, exc=exc)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.splitlines()


def test_a_layer_that_half_loads_says_which_part_did():
    """braidio#207: ``bodies`` registers, ``provenance`` fails -> the schemas stay
    registered with ``HAS_GRAPH`` False, and that is reported, not swallowed."""
    _need("lacing")
    first, *warned = _import_with_blocked("braidio.provenance")
    assert first.startswith("False")
    assert any(
        "ImportWarning" in w and "graph layer" in w and "bodies" in w for w in warned
    ), warned


def test_a_missing_dependency_after_a_part_registered_is_still_reported():
    """Absence is silent only when nothing registered first."""
    _need("lacing")
    first, *warned = _import_with_blocked("braidio.provenance", absent=True)
    assert first.startswith("False") and any("partially loaded" in w for w in warned)


def test_a_bare_install_stays_silent():
    """No lacing at all: nothing registered, so nothing to say (the supported case)."""
    out = _import_with_blocked("lacing", absent=True)
    assert out[0].startswith("False False") and len(out) == 1, out
