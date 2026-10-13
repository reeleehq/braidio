"""``weave_to_episode.execute`` must not persist a member list its timeline does not match.

The persisted ``timeline`` is built over the members that are *in the graph*,
but ``ordered_member_ids`` names the whole skeleton list. ``member_spans``,
``episode_script`` + ``captions.cues_for`` and the picture track all pair a
member to a timeline beat by POSITION, so one missing member silently shifts
every member after it (braidio#98). The reconstruction path
(``episode_timeline``) already refuses that state; ``execute`` must too.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

import braidio

from _audio import FixedSource, needs_ffmpeg, tone

pytestmark = [
    pytest.mark.skipif(not braidio.HAS_NW, reason="nw (and lacing) not available"),
    needs_ffmpeg,
]


@pytest.fixture
def woven(tmp_path, monkeypatch):
    """A woven project: ``(project, episode)`` over three narration beats."""

    def fake_narrate(text, out, *, return_cache_status=False, **kw):
        p = tone(Path(out), 220, 1.0)
        return (p, False) if return_cache_status else p

    monkeypatch.setattr(braidio, "narrate", fake_narrate)
    project = braidio.Project.init(tmp_path / "proj", title="members")
    source = FixedSource(tone(tmp_path / "song.wav", 440, 8.0), start_s=1.0, end_s=3.0)
    script = braidio.Script(
        title="M",
        id_slug="m",
        beats=[braidio.Narration(text=t) for t in ("one", "two", "three")],
    )
    return project, braidio.weave_project(project, script, source=source)


def _execute(project, skeleton):
    from falaw import Plan

    from braidio.transforms._episode import WeaveToEpisode

    return WeaveToEpisode().execute(
        project, Plan(calls=()), (skeleton,), use_cache=False
    )


def test_a_member_missing_at_execute_time_is_refused_not_persisted(woven):
    project, episode = woven
    ids = list(episode.body["ordered_member_ids"])
    ghost = str(uuid.uuid4())
    skeleton = episode.model_copy(
        update={
            "id": uuid.uuid4(),
            "body": {**episode.body, "ordered_member_ids": [ids[0], ghost, *ids[1:]]},
        }
    )
    with pytest.raises(ValueError, match=ghost):
        _execute(project, skeleton)


def test_a_complete_member_list_keeps_ids_and_timeline_in_step(woven):
    project, episode = woven
    skeleton = episode.model_copy(update={"id": uuid.uuid4()})
    (done,) = _execute(project, skeleton).annotations
    assert len(done.body["timeline"]["beats"]) == len(done.body["ordered_member_ids"])
