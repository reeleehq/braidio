"""Tests for :class:`braidio.sources.SpanSource` (braidio#231).

A caller that already knows each excerpt's file and times should not have to
write a resolver class: the reference itself says ``<asset>@<start>-<end>``.
"""

from pathlib import Path

import pytest

import braidio
from braidio.sources import SegmentSource, SpanSource


def _src():
    return SpanSource({"ep1": "ep1.mp3", "ep2": Path("ep2.mp3")})


def test_resolves_an_explicit_span():
    seg = _src().resolve("ep1@412.5-448.0")
    assert (seg.asset_path, seg.start_s, seg.end_s) == (Path("ep1.mp3"), 412.5, 448.0)
    assert seg.duration_s == 35.5


def test_is_a_segment_source_and_exported():
    assert isinstance(_src(), SegmentSource)
    assert braidio.SpanSource is SpanSource


@pytest.mark.parametrize(
    "ref",
    ["ep9@1-2", "ep1", "ep1@", "ep1@a-b", "ep1@5-5", "ep1@9-3", "ep1@-1-2", "@1-2", ""],
)
def test_unknown_asset_or_malformed_span_is_none(ref):
    assert _src().resolve(ref) is None


def test_asset_names_may_contain_an_at_sign():
    src = SpanSource({"a@b": "x.mp3"})
    assert src.resolve("a@b@1-2").asset_path == Path("x.mp3")
