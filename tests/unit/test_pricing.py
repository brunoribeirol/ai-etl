"""Unit tests for core/pricing.py (Sprint 3, ADR-008 — cost per execution)."""

from ai_etl.core.pricing import compute_cost_usd


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
