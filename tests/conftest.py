"""Shared pytest fixtures."""

import pandas as pd
import pytest

from ai_etl.core.llm import reset_circuit_breakers


@pytest.fixture(autouse=True)
def _reset_llm_circuit_breakers() -> None:
    """core/llm.py's per-provider circuit breaker state (ADR-041) is module-level
    global state — without this, a test that drives a provider's circuit open
    (e.g. 5 consecutive mocked failures) would leak that OPEN state into every
    other test using the same provider name in the same pytest session.
    """
    reset_circuit_breakers()


@pytest.fixture
def sample_orders_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": [1, 2, 3, 4],
            "customer_id": [10, 10, 20, None],
            "product": ["A", "B", "A", "C"],
            "price": [100.0, 200.0, 100.0, 50.0],
            "qty": [1, 2, 1, 3],
            "status": ["active", "active", "cancelled", "active"],
        }
    )


@pytest.fixture
def sample_customers_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "customer_id": [10, 20, 30],
            "name": ["Alice", "Bob", "Carol"],
            "city": ["Recife", "São Paulo", "Rio de Janeiro"],
        }
    )


@pytest.fixture
def sample_dfs(sample_orders_df, sample_customers_df) -> dict[str, pd.DataFrame]:
    return {
        "orders": sample_orders_df,
        "customers": sample_customers_df,
    }
