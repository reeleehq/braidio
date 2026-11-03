"""Suite-wide guard: no test can reach ElevenLabs, by accident or by cache.

braidio's ElevenLabs synthesis is replaced in tests by patching one name,
``braidio.narrate`` / ``braidio.render_dialogue`` (braidio#137). A patch that
misses would be a real, billed call. Two things make that a failure instead:

- the ElevenLabs keys are unset for every test, so a real call cannot
  authenticate (a test that needs a key sets its own);
- mixing's TTS cache and braidio's dialogue cache point at a per-session
  temporary directory, because both are read *before* the key is resolved:
  a line this machine once synthesized would otherwise come back from disk
  and hide the missed patch.
"""

from __future__ import annotations

import pytest

#: Env keys a real ElevenLabs call authenticates with.
ELEVENLABS_KEY_ENV_VARS = ("ELEVENLABS_API_KEY", "ELEVEN_API_KEY")
#: Env keys naming the on-disk synthesis caches (mixing's TTS, braidio's dialogue).
SYNTHESIS_CACHE_ENV_VARS = ("MIXING_TTS_CACHE_DIR", "BRAIDIO_DIALOGUE_CACHE_DIR")


@pytest.fixture(scope="session")
def _empty_synthesis_cache(tmp_path_factory):
    return tmp_path_factory.mktemp("synthesis-cache")


@pytest.fixture(autouse=True)
def _no_real_synthesis(monkeypatch, _empty_synthesis_cache):
    for name in ELEVENLABS_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in SYNTHESIS_CACHE_ENV_VARS:
        monkeypatch.setenv(name, str(_empty_synthesis_cache / name.lower()))
