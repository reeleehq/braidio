"""Tests for :mod:`braidio.cost` — the ElevenLabs TTS cost model.

Covers the configurable rate (env override / disable / invalid / per-model), the
per-character cost, and the Script-aware :func:`braidio.estimate_cost` (narration
+ dialogue are billed, segments are free).
"""

import pytest

from braidio import Script, Narration, Dialogue, SegmentBeat
import braidio.cost as cost_module
from braidio.cost import (
    RATE_ENV_VAR,
    DEFAULT_USD_PER_1K_CHARS,
    MODEL_USD_PER_1K_CHARS,
    CostRollup,
    billable_chars,
    usd_per_1k_chars,
    tts_cost_usd,
    estimate_cost,
)
from braidio.tts import DEFAULT_MODEL_ID, DIALOGUE_MODEL_ID


@pytest.fixture(autouse=True)
def _clear_rate_env(monkeypatch):
    """Start each test with the rate env var unset (module default in effect)."""
    monkeypatch.delenv(RATE_ENV_VAR, raising=False)


# --- rate resolution ---------------------------------------------------------


def test_billable_chars_counts_whole_string():
    assert billable_chars("hello") == 5
    assert billable_chars("") == 0
    assert billable_chars(None) == 0


def test_default_rate_when_env_unset():
    assert usd_per_1k_chars() == DEFAULT_USD_PER_1K_CHARS


def test_env_overrides_rate(monkeypatch):
    monkeypatch.setenv(RATE_ENV_VAR, "0.5")
    assert usd_per_1k_chars() == 0.5


def test_env_can_disable_pricing(monkeypatch):
    monkeypatch.setenv(RATE_ENV_VAR, "none")
    assert usd_per_1k_chars() is None


def test_bad_env_rate_is_unpriced(monkeypatch):
    monkeypatch.setenv(RATE_ENV_VAR, "not-a-number")
    assert usd_per_1k_chars() is None


@pytest.mark.parametrize("bad", ["-0.5", "inf", "-inf", "nan"])
def test_negative_or_nonfinite_rate_is_unpriced(monkeypatch, bad):
    # A misconfigured rate must never become a dishonest negative/non-finite spend.
    monkeypatch.setenv(RATE_ENV_VAR, bad)
    assert usd_per_1k_chars() is None
    assert tts_cost_usd("real text") is None


def test_per_model_rate_wins_over_default(monkeypatch):
    # A confirmed per-model rate is used even with the env unset (default path).
    # Deliberately a model id no default uses: narration and dialogue both run on
    # eleven_v3 now, so keying this off those constants would have the "priced"
    # and "unaffected" models be the same one.
    other = "test-only-model"
    monkeypatch.setitem(MODEL_USD_PER_1K_CHARS, other, 0.9)
    assert usd_per_1k_chars(other) == 0.9
    assert usd_per_1k_chars(DEFAULT_MODEL_ID) == DEFAULT_USD_PER_1K_CHARS  # unaffected


# --- per-text cost -----------------------------------------------------------


def test_tts_cost_empty_is_zero_not_none():
    assert tts_cost_usd("") == 0.0


def test_tts_cost_scales_with_chars():
    assert tts_cost_usd("x" * 1000) == pytest.approx(DEFAULT_USD_PER_1K_CHARS)
    assert tts_cost_usd("x" * 2000) == pytest.approx(2 * DEFAULT_USD_PER_1K_CHARS)


def test_tts_cost_unpriced_returns_none_but_empty_still_zero(monkeypatch):
    monkeypatch.setenv(RATE_ENV_VAR, "none")
    assert tts_cost_usd("some real text") is None
    assert tts_cost_usd("") == 0.0


# --- estimate over a Script --------------------------------------------------


def test_estimate_str():
    est = estimate_cost("a" * 500)
    assert isinstance(est, CostRollup)
    assert est.characters == 500
    assert est.usd == pytest.approx(0.5 * DEFAULT_USD_PER_1K_CHARS)
    assert est.unpriced is False


def test_estimate_script_bills_narration_and_dialogue_only():
    narration = "a" * 100
    dialogue = ("b" * 30, "c" * 20)
    script = Script(
        title="t",
        id_slug="01",
        beats=[
            Narration(text=narration),
            SegmentBeat(reference="clip:1"),  # extracted media = free
            Dialogue(turns=(("A", dialogue[0]), ("B", dialogue[1]))),
        ],
    )
    est = estimate_cost(script)
    assert est.characters == 100 + 50  # narration + dialogue turns; clip is free
    # Expected sum computed from the model — robust to future per-model rates.
    expected = tts_cost_usd(narration, model_id=DEFAULT_MODEL_ID) + tts_cost_usd(
        "".join(dialogue), model_id=DIALOGUE_MODEL_ID
    )
    assert est.usd == pytest.approx(expected)
    assert len(est.lines) == 2  # the segment beat contributes no billable line
    assert {ln.kind for ln in est.lines} == {"narration", "dialogue"}


def test_estimate_dialogue_uses_dialogue_model_rate(monkeypatch):
    # A distinct per-model dialogue rate: estimate_cost sums the mixed rates.
    # Dialogue is pinned to its own id here because it otherwise shares
    # eleven_v3 with narration, which would make "mixed rates" untestable.
    monkeypatch.setattr(cost_module, "DIALOGUE_MODEL_ID", "test-dialogue-model")
    monkeypatch.setitem(MODEL_USD_PER_1K_CHARS, "test-dialogue-model", 0.9)
    script = Script(
        title="t",
        id_slug="01",
        beats=[Narration(text="n" * 1000), Dialogue(turns=(("A", "d" * 1000),))],
    )
    est = estimate_cost(script)
    assert est.usd == pytest.approx(DEFAULT_USD_PER_1K_CHARS + 0.9)


def test_estimate_prices_the_script_at_the_named_model(monkeypatch):
    # braidio#143: a Script used to be priced at DEFAULT_MODEL_ID whatever
    # model_id said. Distinct confirmed rates make the model visible in the sum.
    monkeypatch.setattr(cost_module, "DIALOGUE_MODEL_ID", "test-dialogue-model")
    monkeypatch.setitem(MODEL_USD_PER_1K_CHARS, DEFAULT_MODEL_ID, 0.1)
    monkeypatch.setitem(MODEL_USD_PER_1K_CHARS, "test-narration-model", 0.5)
    monkeypatch.setitem(MODEL_USD_PER_1K_CHARS, "test-dialogue-model", 0.9)
    script = Script(
        title="t",
        id_slug="01",
        beats=[Narration(text="n" * 1000), Dialogue(turns=(("A", "d" * 1000),))],
    )
    est = estimate_cost(script, model_id="test-narration-model")
    # the narration at the named model; dialogue stays on its own model
    assert est.usd == pytest.approx(0.5 + 0.9)
    assert [ln.model_id for ln in est.lines] == [
        "test-narration-model",
        "test-dialogue-model",
    ]
    # no confirmed rate of its own: the general rate, never the default model's
    unknown = estimate_cost(script, model_id="test-unrated-model")
    assert unknown.usd == pytest.approx(DEFAULT_USD_PER_1K_CHARS + 0.9)
    # no model named: the default model, as before
    assert estimate_cost(script).usd == pytest.approx(0.1 + 0.9)


def test_estimate_all_segments_is_free():
    script = Script(
        title="t",
        id_slug="01",
        beats=[SegmentBeat(reference="a"), SegmentBeat(reference="b")],
    )
    est = estimate_cost(script)
    assert est.characters == 0
    assert est.usd == 0.0
    assert est.unpriced is False


def test_estimate_unpriced_flags_but_still_counts_chars(monkeypatch):
    monkeypatch.setenv(RATE_ENV_VAR, "none")
    script = Script(title="t", id_slug="01", beats=[Narration(text="x" * 100)])
    est = estimate_cost(script)
    assert est.characters == 100
    assert est.unpriced is True
    assert est.usd is None


def test_estimate_summary_is_human_readable():
    est = estimate_cost("a" * 1000)
    assert "1,000 chars" in est.summary
    assert "$" in est.summary
