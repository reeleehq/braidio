"""Per-request BYO API-key threading through braidio's TTS path (braidio-1).

Proves that an explicit ``api_key`` given to a braidio entry point reaches the
ElevenLabs boundary, and that omitting it threads ``None`` down — delegating
the ``$ELEVENLABS_API_KEY`` fallback to ``mixing`` (unchanged behavior). These
are network-free: every synthesizer boundary is monkeypatched, so no real
ElevenLabs call happens.
"""

from __future__ import annotations

from pathlib import Path

from braidio import Dialogue, Narration, Script
from braidio.multivoice import POOL_4, render_multivoice
from braidio.render import render_production

_KEY = "SECRET-USER-KEY"


def test_narrate_threads_api_key(tmp_path, monkeypatch):
    """narrate forwards api_key to mixing.text_to_speech; omitting it → None."""
    import braidio.tts as tts

    captured: dict = {}

    def fake_text_to_speech(
        text, voice_id, *, api_key=None, return_cache_status=False, **kw
    ):
        captured["api_key"] = api_key
        audio = b"AUDIO"
        return (audio, False) if return_cache_status else audio

    monkeypatch.setattr(tts, "text_to_speech", fake_text_to_speech)

    tts.narrate("hello", tmp_path / "a.mp3", api_key=_KEY)
    assert captured["api_key"] == _KEY

    tts.narrate("hello", tmp_path / "b.mp3")  # omitted → delegates env resolution
    assert captured["api_key"] is None


def test_render_dialogue_threads_api_key(tmp_path, monkeypatch):
    """render_dialogue forwards api_key to text_to_dialogue; omitting it → None."""
    import braidio.conversation as conv

    captured: dict = {}

    def fake_text_to_dialogue(turns, *, api_key=None, return_cache_status=False, **kw):
        captured["api_key"] = api_key
        return (b"DIALOGUE", False) if return_cache_status else b"DIALOGUE"

    monkeypatch.setattr(conv, "text_to_dialogue", fake_text_to_dialogue)

    conv.render_dialogue(
        [("A", "hi there"), ("B", "hey")],
        out_path=tmp_path / "d.mp3",
        api_key=_KEY,
        tighten_gaps_s=0,  # skip the ffmpeg tighten pass
    )
    assert captured["api_key"] == _KEY

    conv.render_dialogue(
        [("A", "hi there"), ("B", "hey")],
        out_path=tmp_path / "d2.mp3",
        tighten_gaps_s=0,
    )
    assert captured["api_key"] is None


def test_render_multivoice_threads_api_key(tmp_path, monkeypatch):
    """render_multivoice forwards api_key to every narrate call."""
    import braidio.multivoice as mv

    seen: list = []

    def fake_narrate(text, out_path, *, api_key=None, **kw):
        seen.append(api_key)
        Path(out_path).write_bytes(b"x")
        return Path(out_path)

    monkeypatch.setattr(mv, "narrate", fake_narrate)
    monkeypatch.setattr(mv, "level_part", lambda src, dst, **kw: src)
    monkeypatch.setattr(
        mv, "concatenate_audio", lambda *a, output, **kw: Path(output).write_bytes(b"x")
    )

    segments = ["Hello there.", "How are you.", "Fine thanks.", "Good good."]
    render_multivoice(
        segments,
        POOL_4,
        out_path=tmp_path / "mv.mp3",
        work_dir=tmp_path / "work",
        api_key=_KEY,
    )
    assert seen and all(k == _KEY for k in seen)

    seen.clear()
    render_multivoice(
        segments,
        POOL_4,
        out_path=tmp_path / "mv2.mp3",
        work_dir=tmp_path / "work2",
    )
    assert seen and all(k is None for k in seen)  # omitted → None threaded down


def _patch_render_boundaries(monkeypatch, tmp_path):
    """Monkeypatch render_production's synth/ffmpeg boundaries; return capture dict."""
    import braidio.render as render_mod

    captured: dict = {}

    def fake_narrate(text, out_path, *, api_key=None, **kw):
        captured["narrate_api_key"] = api_key
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"x")
        return Path(out_path)

    def fake_render_dialogue(turns, cast=None, *, api_key=None, out_path, **kw):
        captured["dialogue_api_key"] = api_key
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"x")
        return Path(out_path)

    monkeypatch.setattr(render_mod, "narrate", fake_narrate)
    monkeypatch.setattr(render_mod, "render_dialogue", fake_render_dialogue)
    monkeypatch.setattr(render_mod, "level_part", lambda src, dst, **kw: src)
    monkeypatch.setattr(
        render_mod,
        "concatenate_audio",
        lambda *a, output, **kw: Path(output).write_bytes(b"x"),
    )
    return captured


def test_render_production_threads_api_key_narration(tmp_path, monkeypatch):
    """render_production threads api_key into the narration-beat branch."""
    captured = _patch_render_boundaries(monkeypatch, tmp_path)
    script = Script(title="t", id_slug="1", beats=[Narration("Hello world.")])

    render_production(
        script,
        source=object(),
        api_key=_KEY,
        out_path=tmp_path / "out.mp3",
        tts_dir=tmp_path / "tts",
        clips_dir=tmp_path / "clips",
        episodes_dir=tmp_path / "eps",
    )
    assert captured.get("narrate_api_key") == _KEY

    # omitted → None threaded down (env fallback happens in mixing)
    captured.clear()
    render_production(
        script,
        source=object(),
        out_path=tmp_path / "out2.mp3",
        tts_dir=tmp_path / "tts2",
        clips_dir=tmp_path / "clips2",
        episodes_dir=tmp_path / "eps2",
    )
    assert captured.get("narrate_api_key") is None


def test_render_production_threads_api_key_dialogue(tmp_path, monkeypatch):
    """The dialogue-beat branch of render_production also threads api_key.

    This is the adversarial case: a code path where the key could easily be
    dropped because dialogue is synthesized via render_dialogue, not narrate.
    """
    captured = _patch_render_boundaries(monkeypatch, tmp_path)
    script = Script(
        title="t",
        id_slug="1",
        beats=[Dialogue(turns=(("A", "hi there"), ("B", "hey good to see you")))],
    )

    render_production(
        script,
        source=object(),
        api_key=_KEY,
        out_path=tmp_path / "out.mp3",
        tts_dir=tmp_path / "tts",
        clips_dir=tmp_path / "clips",
        episodes_dir=tmp_path / "eps",
    )
    assert captured.get("dialogue_api_key") == _KEY
