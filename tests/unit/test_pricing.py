"""Unit tests for core/pricing.py (Sprint 3, ADR-008 — cost per execution)."""

from ai_etl.core.pricing import MODEL_PRICING_USD_PER_MILLION_TOKENS, compute_cost_usd


def test_compute_cost_usd_known_model() -> None:
    tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "total_tokens": 2_000_000}
    cost = compute_cost_usd("gpt-4o-mini", tokens)
    assert cost == 0.15 + 0.60


def test_compute_cost_usd_scales_with_token_count() -> None:
    tokens = {"input_tokens": 500_000, "output_tokens": 0, "total_tokens": 500_000}
    cost = compute_cost_usd("gpt-4o-mini", tokens)
    assert cost == 0.075


def test_compute_cost_usd_zero_tokens_is_zero_not_none() -> None:
    tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert compute_cost_usd("gpt-4o-mini", tokens) == 0.0


def test_compute_cost_usd_unknown_model_returns_none() -> None:
    tokens = {"input_tokens": 1000, "output_tokens": 1000, "total_tokens": 2000}
    assert compute_cost_usd("some-future-model", tokens) is None


def test_compute_cost_usd_missing_keys_default_to_zero() -> None:
    assert compute_cost_usd("gpt-4o", {}) == 0.0


# Sprint 30 (ADR-031) — multi-provider pricing. Before this sprint, every model
# below returned `None` ("cost unknown"), silently defeating the Sprint 29 budget
# cap for any tenant running Anthropic/Google.


def test_compute_cost_usd_anthropic_model() -> None:
    tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "total_tokens": 2_000_000}
    cost = compute_cost_usd("claude-sonnet-5", tokens)
    assert cost == 3.00 + 15.00


def test_compute_cost_usd_google_model() -> None:
    tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "total_tokens": 2_000_000}
    cost = compute_cost_usd("gemini-2.0-flash", tokens)
    assert cost == 0.10 + 0.40


def test_compute_cost_usd_ollama_model_is_explicit_zero_not_none() -> None:
    """`0.0` (not `None`) — Ollama is genuinely free, distinct from "unpriced"."""
    tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "total_tokens": 2_000_000}
    assert compute_cost_usd("llama3.1", tokens) == 0.0


def test_every_llm_allowed_model_has_a_pricing_entry() -> None:
    """`core.llm.ALLOWED_MODELS_BY_PROVIDER` and this module's pricing table must
    stay in sync — an allowlisted model with no pricing entry would silently price
    as `None` ("unknown") for any tenant who selects it (ADR-031)."""
    from ai_etl.core.llm import ALLOWED_MODELS_BY_PROVIDER

    for models in ALLOWED_MODELS_BY_PROVIDER.values():
        for model in models:
            assert (
                model in MODEL_PRICING_USD_PER_MILLION_TOKENS
            ), f"{model!r} is allowed but has no pricing entry"
