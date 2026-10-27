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
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

import braidio
from braidio.tts import register_tts, unregister_tts

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
    tones = {"tone": _Tone(), "tone2": _Tone(), "unpriced": _Tone()}
    for name, speak in tones.items():
        rate = None if name == "unpriced" else 0.0
        register_tts(name, speak, usd_per_1k_chars=rate)
    yield tones
    for name in tones:
        unregister_tts(name)


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
    from braidio.transforms._common import url_to_path

    assert braidio.duration_s(url_to_path(episode.body["url"])) > 0.5
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


def _narrate_one(project, script, *, config):
    """Ingest, cast and run the narration transform on the first beat."""
    import nw
    from nw import TransformInputs

    braidio.transforms.ingest_script(project, script, config=config)
    beat = nw.annotations_at_tier(project.root, "narrative-beats")[0]
    voice = nw.get_transform("beat_to_voice_assignment.default")
    voice.execute(project, *voice.plan(project, TransformInputs(primary=(beat,))))
    narration = nw.get_transform("narration_render.tts")
    plan, skel = narration.plan(project, TransformInputs(primary=(beat,)))
    return narration, beat, narration.execute(project, plan, skel)


def _one_line(text="Hello there."):
    return braidio.Script(
        title="One", id_slug="one", beats=[braidio.Narration(text=text)]
    )


def test_a_backend_prices_at_its_own_rate(tmp_path, backends):
    """A free backend costs $0, known; an unpriced one is flagged, not faked as $0."""
    _, _, free = _narrate_one(
        braidio.Project.init(tmp_path / "a", title="a"),
        _one_line(),
        config=braidio.WeaveConfig(tts="tone"),
    )
    assert free.cost_usd_actual == 0.0 and free.has_unknown_costs is False
    _, _, unknown = _narrate_one(
        braidio.Project.init(tmp_path / "b", title="b"),
        _one_line(),
        config=braidio.WeaveConfig(tts="unpriced"),
    )
    assert unknown.has_unknown_costs is True


def test_a_cached_render_is_served_without_the_backend_registered(tmp_path, backends):
    """A runner that only serves fresh nodes need not register the backend."""
    from nw import TransformInputs

    project = braidio.Project.init(tmp_path / "p", title="p")
    narration, beat, first = _narrate_one(
        project, _one_line(), config=braidio.WeaveConfig(tts="tone")
    )
    unregister_tts("tone")
    plan, skel = narration.plan(project, TransformInputs(primary=(beat,)))
    again = narration.execute(project, plan, skel)
    assert again.annotations[0].id == first.annotations[0].id
    assert again.has_unknown_costs is True  # the rate is not known here


def test_a_paced_beat_keeps_its_backends_format_and_leaves_no_parts(tmp_path, backends):
    """One paced turn is that turn's own file: it must not be renamed .mp3."""
    from braidio.transforms._common import url_to_path

    config = braidio.WeaveConfig(tts="tone", segmentation_unit="sentence", min_turn=1)
    project = braidio.Project.init(tmp_path / "p", title="p")
    _, _, result = _narrate_one(
        project, _one_line("Only one sentence here."), config=config
    )
    node = result.annotations[0]
    path = url_to_path(node.body["url"])
    assert path.read_bytes()[:4] == b"RIFF" and path.suffix == ".wav"
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


def test_a_dialogue_render_leaves_only_its_take(tmp_path, backends):
    import nw

    project = braidio.Project.init(tmp_path / "p", title="p")
    braidio.weave_project(project, _script(), config=braidio.WeaveConfig(tts="tone"))
    (take,) = nw.annotations_at_tier(project.root, "dialogue-renders")
    folder = project.root / "data" / "dialogue"
    assert [p.name for p in folder.iterdir()] == [f"{take.id}.mp3"]


def test_a_rate_must_be_a_real_price():
    for bad in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finite rate"):
            register_tts(
                "bad", lambda text, *, voice: (b"", "wav"), usd_per_1k_chars=bad
            )
