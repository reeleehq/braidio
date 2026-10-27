"""ElevenLabs narration synthesis.

Thin wrapper over :func:`mixing.text_to_speech` (the ElevenLabs entry point,
with on-disk caching) applying a voice preset: the ``eleven_multilingual_v2``
quality model and a locked voice + settings so a whole production sounds like
one narrator.

Voice defaults to "George — Warm, Captivating Storyteller"; override with the
``BRAIDIO_TTS_VOICE`` env var (:data:`VOICE_ENV_VAR`) or the ``voice_id`` arg.

ElevenLabs is the default, not the only voice: a :data:`Speak` callable
(``speak(text, *, voice) -> (audio_bytes, format)``) is the seam any other
backend plugs into — a local model through ``voxy`` for a free preview, a test,
CI. :func:`narrator_from_speak` adapts one to :func:`narrate`'s shape, which is
what the renderers call.
"""

from __future__ import annotations

import functools
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mixing import text_to_speech

# "George — Warm, Captivating Storyteller" (ElevenLabs premade voice).
DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
#: The narration model. eleven_v3 rather than eleven_multilingual_v2 because
#: v2 cannot render inline [audio tags] AT ALL, and tag density is the main
#: lever on whether a read sounds alive (braidio.style.audio_tag_rate). A v2
#: default caps every production at the flat end before an author writes a
#: word. Override per-render with `delivery=`, or persist your own in
#: ~/.config/braidio/config.json — see braidio.defaults.
DEFAULT_MODEL_ID = "eleven_v3"
VOICE_ENV_VAR = "BRAIDIO_TTS_VOICE"

# Preset from research: neutral-expressive documentary narration.
DEFAULT_VOICE_SETTINGS: dict[str, float | bool] = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
    "speed": 0.97,
}


#: How a render's settings record names the default voice backend (no ``speak=``).
DEFAULT_TTS = "elevenlabs"

_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,!?;:])")  # what a removed tag leaves

#: A text-to-speech backend: ``speak(text, *, voice) -> (audio_bytes, format)``,
#: ``format`` being the audio's file extension (``"wav"``, ``"mp3"``). ``voice``
#: is the voice braidio resolved for the line (``None``: the backend's default).
Speak = Callable[..., tuple[bytes, str]]


def narrator_from_speak(
    speak: Speak, *, strip_tags: bool = True
) -> Callable[..., Path]:
    """A :func:`narrate`-shaped synthesizer that speaks through ``speak``.

    ``voice_id`` is handed to ``speak`` as ``voice``: braidio's resolved id for
    the line, which under the shipped formats and the default cast is an
    ElevenLabs id (pass ``voice_id=`` / ``cast=`` in the backend's vocabulary,
    or map ids inside ``speak``); ``None`` only when nothing named a voice.
    Inline ``[audio tags]`` are eleven_v3 markup, so with ``strip_tags`` (the
    default) they are removed before ``speak`` sees the text, rather than read
    aloud. The ElevenLabs-only arguments (``api_key``, ``model_id``,
    ``voice_settings``, so a paced turn's ``speed`` too) are accepted and
    ignored. Nothing is cached: every call reaches ``speak``. The file is
    written at ``out_path`` with the suffix ``speak`` reported.

    >>> import tempfile
    >>> fake = lambda text, *, voice: (text.encode(), "wav")
    >>> with tempfile.TemporaryDirectory() as d:
    ...     out = narrator_from_speak(fake)("[pause] hi", Path(d) / "a.mp3", voice_id="v")
    ...     out.suffix, out.read_bytes()
    ('.wav', b'hi')
    """
    from braidio.style import strip_audio_tags

    def _narrate(text: str, out_path: str | Path, *, voice_id=None, **_) -> Path:
        if strip_tags:
            text = _SPACE_BEFORE_PUNCT.sub(
                r"\1", " ".join(strip_audio_tags(text).split())
            )
            if not text:
                raise ValueError(
                    "a line of nothing but [audio tags] has nothing to speak"
                )
        audio, fmt = speak(text, voice=voice_id)
        ext = str(fmt or "").strip().lstrip(".")
        if not ext:
            raise ValueError(
                f"{tts_name(speak)} returned no audio format for {text[:40]!r}"
            )
        out = Path(out_path).with_suffix("." + ext)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio)
        return out

    return _narrate


def tts_name(speak: Speak | None) -> str:
    """The voice backend a render used, as its settings record names it.

    ``None`` is the default backend; a callable is named ``module:qualname``,
    seeing through ``functools.partial`` (a ``name`` attribute wins).

    >>> import functools
    >>> tts_name(None)
    'elevenlabs'
    >>> tts_name(functools.partial(narrator_from_speak, strip_tags=False))
    'braidio.tts:narrator_from_speak'
    """
    if speak is None:
        return DEFAULT_TTS
    if isinstance(getattr(speak, "name", None), str):
        return speak.name
    while isinstance(speak, functools.partial):
        speak = speak.func
    name = getattr(speak, "__qualname__", None) or type(speak).__qualname__
    module = getattr(speak, "__module__", None) or type(speak).__module__
    return f"{module}:{name}"


@dataclass(frozen=True)
class TTSBackend:
    """A named voice backend: its ``speak`` and what it costs.

    ``usd_per_1k_chars`` is the backend's rate: ``0.0`` for a local model,
    ``None`` (default) when unknown, which a cost report then reads as
    *unpriced* rather than free (the same rule as :mod:`braidio.cost`).
    """

    speak: Speak
    usd_per_1k_chars: float | None = None

    def cost_usd(self, text: str) -> float | None:
        """The spend for ``text`` at this backend's rate (``None``: unpriced)."""
        if self.usd_per_1k_chars is None:
            return None
        return len(text) / 1000 * self.usd_per_1k_chars


#: The registered voice backends by name (:func:`register_tts`). A name, not a
#: callable, is what a :class:`~braidio.weave_config.WeaveConfig` records
#: (``tts=``), so the graph can persist it, fold it into a cache key, and
#: resolve it again at render time.
TTS_BACKENDS: dict[str, TTSBackend] = {}


def register_tts(
    name: str, speak: Speak, *, usd_per_1k_chars: float | None = None
) -> TTSBackend:
    """Register ``speak`` as the voice backend ``name`` (replacing any before).

    ``name`` is what ``WeaveConfig(tts=name)`` selects, on either render path.
    :data:`DEFAULT_TTS` is ElevenLabs itself and cannot be taken.

    >>> b = register_tts("doc-tone", lambda text, *, voice: (b"", "wav"), usd_per_1k_chars=0.0)
    >>> tts_backend("doc-tone") is b, b.cost_usd("hello")
    (True, 0.0)
    >>> _ = TTS_BACKENDS.pop("doc-tone")
    """
    if not name or name == DEFAULT_TTS:
        raise ValueError(f"{name!r} is not a registrable voice-backend name")
    backend = TTSBackend(speak=speak, usd_per_1k_chars=usd_per_1k_chars)
    TTS_BACKENDS[name] = backend
    return backend


def tts_backend(name: str) -> TTSBackend:
    """The backend registered as ``name``; ``KeyError`` naming the registered ones."""
    try:
        return TTS_BACKENDS[name]
    except KeyError:
        known = sorted(TTS_BACKENDS) or "none"
        raise KeyError(
            f"no voice backend {name!r} is registered (registered: {known}); "
            "register one with braidio.tts.register_tts(name, speak)"
        ) from None


def resolve_voice_id(voice_id: str | None = None) -> str:
    """Voice id from arg → :data:`VOICE_ENV_VAR` env → default."""
    return voice_id or os.environ.get(VOICE_ENV_VAR) or DEFAULT_VOICE_ID


def narrate(
    text: str,
    out_path: str | Path,
    *,
    api_key: str | None = None,
    voice_id: str | None = None,
    model_id: str = DEFAULT_MODEL_ID,
    voice_settings: dict | None = None,
    output_format: str = "mp3_44100_128",
    refresh: bool = False,
    return_cache_status: bool = False,
) -> Path | tuple[Path, bool]:
    """Synthesize ``text`` to ``out_path`` (mp3). Returns the path.

    Caching is handled by ``mixing.text_to_speech`` (keyed on text+voice+model);
    pass ``refresh=True`` to regenerate.

    ``api_key`` is an optional per-request ElevenLabs key: when given it wins
    over the environment; when ``None`` (default) resolution falls back to
    ``$ELEVENLABS_API_KEY`` (unchanged behavior). This is what lets a caller
    thread a per-user BYO key without touching the process environment.

    ``return_cache_status``: when ``True``, return ``(path, was_cached)`` where
    ``was_cached`` is ``True`` iff mixing served the audio from its on-disk cache
    (no ElevenLabs call = $0 real spend). Lets the caller attribute real cost
    (braidio#8). Default ``False`` keeps the ``Path`` return.
    """
    audio, was_cached = text_to_speech(
        text,
        resolve_voice_id(voice_id),
        api_key=api_key,
        model_id=model_id,
        output_format=output_format,
        voice_settings=voice_settings or DEFAULT_VOICE_SETTINGS,
        refresh=refresh,
        return_cache_status=True,
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio)
    return (out, was_cached) if return_cache_status else out


DIALOGUE_MODEL_ID = "eleven_v3"
DIALOGUE_CACHE_ENV_KEY = "BRAIDIO_DIALOGUE_CACHE_DIR"


def _dialogue_cache_dir():
    from mixing import _cache

    return _cache.default_cache_dir("braidio-dialogue", env_key=DIALOGUE_CACHE_ENV_KEY)


def text_to_dialogue(
    turns,
    *,
    model_id: str = DIALOGUE_MODEL_ID,
    output_format: str = "mp3_44100_128",
    settings: dict | None = None,
    seed: int | None = None,
    api_key: str | None = None,
    cache=True,
    refresh: bool = False,
    return_cache_status: bool = False,
) -> bytes | tuple[bytes, bool]:
    """Synthesize a multi-speaker exchange in ONE pass (ElevenLabs Text-to-Dialogue).

    Unlike per-line :func:`narrate`, this renders the whole conversation together
    so prosody is conditioned across turns — the key to sounding like people
    *talking to each other* rather than alternating monologues (see braidio#1 /
    ``docs/research/conversational-vs-narration-tts.md``). ``eleven_v3`` only.

    **Cached** (like :func:`narrate`/``mixing.text_to_speech``): the result is
    keyed on the SHA-256 of every parameter that affects the audio (turns,
    voices, model, settings, seed, format). A cache hit returns the *same* bytes
    instantly — no API call, deterministic master, and the basis for partial
    re-render (change one exchange → only its key changes). ``cache=True``
    (default) uses :data:`DIALOGUE_CACHE_ENV_KEY` / the default cache dir;
    ``cache=False`` disables it; a path uses that dir. ``refresh=True`` forces a
    re-render (to re-roll a v3 take). Because v3 is nondeterministic, caching a
    seedless call freezes one random take — pass a ``seed`` for reproducibility.

    Args:
        turns: ordered ``(voice_id, text)`` pairs (or ``{"voice_id", "text"}``).
            Keep each request under ~2000 chars total (API limit).
        settings: optional model settings dict (e.g. ``{"stability": 0.45}``).
        return_cache_status: when ``True``, return ``(audio, was_cached)`` where
            ``was_cached`` is ``True`` iff the bytes came from the on-disk cache
            (no ElevenLabs call = $0 real spend) — the same attribution
            :func:`narrate` offers (braidio#8). Default ``False`` keeps the
            ``bytes`` return.

    Returns: raw audio bytes in ``output_format``.
    """
    import json

    from mixing import _cache

    inputs = []
    for t in turns:
        if isinstance(t, (tuple, list)):
            inputs.append({"voice_id": t[0], "text": t[1]})
        else:
            inputs.append({"voice_id": t["voice_id"], "text": t["text"]})

    cache_dir = _cache.resolve_cache_dir(cache, default_factory=_dialogue_cache_dir)
    key = None
    if cache_dir is not None:
        key = _cache.sha256_key(
            "text_to_dialogue",
            json.dumps(inputs, sort_keys=True, ensure_ascii=False),
            model_id,
            output_format,
            json.dumps(settings or {}, sort_keys=True),
            "" if seed is None else str(seed),
        )
        if not refresh:
            cached = _cache.read_cache(cache_dir, key, suffix=".audio")
            if cached is not None:
                return (cached, True) if return_cache_status else cached

    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(
        api_key=api_key
        or os.environ.get("ELEVENLABS_API_KEY")
        or os.environ.get("ELEVEN_API_KEY")
    )
    kwargs = {"inputs": inputs, "model_id": model_id, "output_format": output_format}
    if settings is not None:
        kwargs["settings"] = settings
    if seed is not None:
        kwargs["seed"] = seed
    audio = b"".join(client.text_to_dialogue.convert(**kwargs))

    if cache_dir is not None and key is not None:
        _cache.write_cache(cache_dir, key, audio, suffix=".audio")
    return (audio, False) if return_cache_status else audio
