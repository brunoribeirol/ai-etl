"""Shared pytest fixtures."""

import pandas as pd
import pytest


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
