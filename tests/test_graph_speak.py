"""A registered voice backend on the graph path (braidio#132).

``WeaveConfig(tts=<name>)`` selects a backend registered with
:func:`braidio.tts.register_tts`: the name is persisted (weave-config, and the
dialogue cast), folded into the render cache keys, and resolved at execute
time. ElevenLabs is booby-trapped throughout, so these prove it is never
reached; the backend is a free local tone, so the suite needs no key.
"""

from __future__ import annotations

import io
import shutil

import numpy as np
import pytest
from scipy.io import wavfile

import braidio
from braidio.tts import TTS_BACKENDS, register_tts

pytestmark = [
    pytest.mark.skipif(not braidio.HAS_NW, reason="nw layer not installed"),
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg"),
]

_RATE = 24000


class _Tone:
    """A free ``speak``: a short tone per line, counting its calls."""

    def __init__(self):
        self.calls = []

    def __call__(self, text, *, voice):
        self.calls.append((text, voice))
        n = int(_RATE * 0.1 * max(1, len(text.split())))
        tone = 0.1 * np.sin(2 * np.pi * 220 * np.arange(n) / _RATE)
        buf = io.BytesIO()
        wavfile.write(buf, _RATE, tone.astype(np.float32))
        return buf.getvalue(), "wav"


@pytest.fixture
def backends(monkeypatch):
    """Two registered free backends, ElevenLabs refused, the registry restored."""

    def _refuse(*a, **k):
        raise AssertionError("ElevenLabs was called")

    monkeypatch.setattr(braidio, "narrate", _refuse)
    monkeypatch.setattr(braidio, "render_dialogue", _refuse)
    monkeypatch.setattr(braidio.tts, "text_to_speech", _refuse)
    monkeypatch.setattr(braidio.conversation, "text_to_dialogue", _refuse)
    saved = dict(TTS_BACKENDS)
    tones = {"tone": _Tone(), "tone2": _Tone()}
    for name, speak in tones.items():
        register_tts(name, speak, usd_per_1k_chars=0.0)
    yield tones
    TTS_BACKENDS.clear()
    TTS_BACKENDS.update(saved)


def _script():
    return braidio.Script(
        title="Graph",
        id_slug="graph",
        beats=[
            braidio.Narration(text="[pause] A line for the narrator."),
            braidio.Dialogue(turns=[("A", "Is that so?"), ("B", "It is.")]),
        ],
    )


def _calls(tones):
    return sum(len(t.calls) for t in tones.values())


def test_weave_project_speaks_through_the_registered_backend(tmp_path, backends):
    import nw

    project = braidio.Project.init(tmp_path / "p", title="graph")
    config = braidio.WeaveConfig(tts="tone")
    episode = braidio.weave_project(project, _script(), config=config)

    tone = backends["tone"]
    # narration (tags stripped) and each dialogue turn, in the cast's voices
    assert [t for t, _ in tone.calls] == [
        "A line for the narrator.",
        "Is that so?",
        "It is.",
    ]
    assert braidio.duration_s(episode.body["url"].removeprefix("file://")) > 0.5
    # the name is persisted where freshness can see it
    (cast,) = nw.annotations_at_tier(project.root, "dialogue-casts")
    assert cast.body["tts"] == "tone"
    (cfg,) = nw.annotations_at_tier(project.root, "weave-configs")
    assert cfg.body["config"]["tts"] == "tone"

    # unchanged: a re-weave is served from the graph, nothing spoken again
    braidio.weave_project(project, _script(), config=config)
    assert len(tone.calls) == 3

    # switching backends re-stales exactly the spoken renders
    braidio.weave_project(project, _script(), config=config.with_(tts="tone2"))
    assert len(backends["tone2"].calls) == 3


def test_an_elevenlabs_project_records_no_backend(tmp_path, monkeypatch):
    """The absence rule: no ``tts`` key anywhere, so an existing project keeps
    its graph and its cache keys byte for byte."""
    import nw

    project = braidio.Project.init(tmp_path / "p", title="graph")
    braidio.transforms.ingest_script(project, _script())
    (cast,) = nw.annotations_at_tier(project.root, "dialogue-casts")
    (cfg,) = nw.annotations_at_tier(project.root, "weave-configs")
    assert "tts" not in cast.body and "tts" not in cfg.body["config"]


def test_the_backend_name_is_part_of_the_cache_keys():
    from braidio.transforms._dialogue import DialogueRenderTTS, _dialogue_cache_key
    from braidio.transforms._narration import NarrationRenderTTS, _narration_cache_key

    n, d = NarrationRenderTTS(), DialogueRenderTTS()
    args = ("text", "voice", "model", {})
    assert _narration_cache_key(n, *args) == _narration_cache_key(n, *args, tts=None)
    assert _narration_cache_key(n, *args) != _narration_cache_key(n, *args, tts="tone")
    cast, turns = braidio.ConversationCast(), [("A", "hi")]
    assert _dialogue_cache_key(d, turns, cast) != _dialogue_cache_key(
        d, turns, cast, tts="tone"
    )


def test_the_fast_path_selects_the_backend_by_config_name(tmp_path, backends):
    script = braidio.Script(
        title="Fast", id_slug="fast", beats=[braidio.Narration(text="Hello there.")]
    )
    _, timeline = braidio.render_production(
        script,
        source=None,
        config=braidio.WeaveConfig(tts="tone"),
        out_path=tmp_path / "f.mp3",
        return_timeline=True,
        tts_dir=tmp_path / "tts",
        episodes_dir=tmp_path / "eps",
    )
    assert backends["tone"].calls == [("Hello there.", None)]
    assert timeline.settings["resolved"]["tts"] == "tone"


def test_an_unregistered_backend_names_the_registered_ones(tmp_path, backends):
    project = braidio.Project.init(tmp_path / "p", title="graph")
    with pytest.raises(KeyError, match="tone"):
        braidio.weave_project(
            project, _script(), config=braidio.WeaveConfig(tts="nope")
        )
    assert _calls(backends) == 0
    import nw

    assert (
        nw.annotations_at_tier(project.root, "weave-configs") == []
    )  # nothing written
