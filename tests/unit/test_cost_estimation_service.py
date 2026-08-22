"""Unit tests for services/cost_estimation.py (Sprint 35, FinOps).

Mocks at the import site inside `services/cost_estimation.py`, same
convention as `test_api_budget.py` mocking `api.routers.budget`.
"""

import pytest

from ai_etl.core.llm import UnsupportedProviderOrModelError
from ai_etl.services.cost_estimation import HEURISTIC_WEIGHT, estimate_run_cost


def test_estimate_run_cost_uses_tenant_history_when_available(mocker) -> None:
    mocker.patch(
        "ai_etl.services.cost_estimation.get_avg_run_cost_usd",
        return_value=2.0,
    )
    global_mock = mocker.patch("ai_etl.services.cost_estimation.get_global_avg_run_cost_usd")

    result = estimate_run_cost(
        tenant_id="tenant-a",
        row_count=1000,
        col_count=10,
        provider="openai",
        model="gpt-4o-mini",
    )

    assert result["basis"] == "heuristic_and_tenant_history"
    assert result["historical_avg_cost_usd"] == 2.0
    global_mock.assert_not_called()
    expected = round(
        HEURISTIC_WEIGHT * result["heuristic_cost_usd"] + (1 - HEURISTIC_WEIGHT) * 2.0, 6
    )
    assert result["estimated_cost_usd"] == expected


def test_estimate_run_cost_falls_back_to_global_history_when_tenant_has_none(mocker) -> None:
    mocker.patch("ai_etl.services.cost_estimation.get_avg_run_cost_usd", return_value=None)
    mocker.patch(
        "ai_etl.services.cost_estimation.get_global_avg_run_cost_usd",
        return_value=0.5,
    )

    result = estimate_run_cost(
        tenant_id="new-tenant",
        row_count=500,
        col_count=8,
        provider="openai",
        model="gpt-4o-mini",
    )

    assert result["basis"] == "heuristic_and_global_history"
    assert result["historical_avg_cost_usd"] == 0.5


def test_estimate_run_cost_is_heuristic_only_with_zero_history_anywhere(mocker) -> None:
    mocker.patch("ai_etl.services.cost_estimation.get_avg_run_cost_usd", return_value=None)
    mocker.patch("ai_etl.services.cost_estimation.get_global_avg_run_cost_usd", return_value=None)

    result = estimate_run_cost(
        tenant_id="new-tenant",
        row_count=500,
        col_count=8,
        provider="openai",
        model="gpt-4o-mini",
    )

    assert result["basis"] == "heuristic_only"
    assert result["historical_avg_cost_usd"] is None
    assert result["estimated_cost_usd"] == result["heuristic_cost_usd"]


def test_estimate_run_cost_larger_dataset_costs_more(mocker) -> None:
    mocker.patch("ai_etl.services.cost_estimation.get_avg_run_cost_usd", return_value=None)
    mocker.patch("ai_etl.services.cost_estimation.get_global_avg_run_cost_usd", return_value=None)

    small = estimate_run_cost("t", 100, 5, "openai", "gpt-4o-mini")
    large = estimate_run_cost("t", 100, 200, "openai", "gpt-4o-mini")

    assert large["estimated_cost_usd"] > small["estimated_cost_usd"]


def test_estimate_run_cost_rejects_unsupported_provider_or_model(mocker) -> None:
    mocker.patch("ai_etl.services.cost_estimation.get_avg_run_cost_usd", return_value=None)
    mocker.patch("ai_etl.services.cost_estimation.get_global_avg_run_cost_usd", return_value=None)

    with pytest.raises(UnsupportedProviderOrModelError):
        estimate_run_cost("t", 100, 5, "openai", "not-a-real-model")


def test_estimate_run_cost_ollama_is_zero_cost(mocker) -> None:
    mocker.patch("ai_etl.services.cost_estimation.get_avg_run_cost_usd", return_value=None)
    mocker.patch("ai_etl.services.cost_estimation.get_global_avg_run_cost_usd", return_value=None)

    result = estimate_run_cost("t", 10_000, 30, "ollama", "llama3.1")

    assert result["heuristic_cost_usd"] == 0.0
    assert result["estimated_cost_usd"] == 0.0
