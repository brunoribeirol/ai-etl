"""Unit tests for the sandbox module."""

import pandas as pd

from ai_etl.core.sandbox import execute_in_sandbox


def test_valid_transform_returns_dataframe() -> None:
    code = """
def transform(dfs):
    df = dfs["orders"].copy()
    df["total"] = df["price"] * df["qty"]
    return df
"""
    dfs = {"orders": pd.DataFrame({"price": [10.0, 20.0], "qty": [2, 3]})}
    result, error = execute_in_sandbox(code, dfs)
    assert error is None
    assert result is not None
    assert "total" in result.columns
    assert list(result["total"]) == [20.0, 60.0]


def test_syntax_error_returns_error_message() -> None:
    code = "def transform(dfs):\n    return dfs['x'  # missing bracket"
    result, error = execute_in_sandbox(code, {})
    assert result is None
    assert error is not None
    assert "SyntaxError" in error


def test_runtime_error_returns_error_message() -> None:
    code = """
def transform(dfs):
    return dfs["nonexistent_key"]
"""
    result, error = execute_in_sandbox(code, {"orders": pd.DataFrame()})
    assert result is None
    assert error is not None


def test_missing_transform_function_returns_error() -> None:
    code = "x = 1 + 1"
    result, error = execute_in_sandbox(code, {})
    assert result is None
    assert error is not None
    assert "transform" in error


def test_non_dataframe_return_returns_error() -> None:
    code = """
def transform(dfs):
    return [1, 2, 3]
"""
    result, error = execute_in_sandbox(code, {})
    assert result is None
    assert error is not None
    assert "pd.DataFrame" in error


def test_blocked_imports_raise_error() -> None:
    code = """
def transform(dfs):
    import os
    return dfs.get("x", __import__("pandas").DataFrame())
"""
    result, error = execute_in_sandbox(code, {})
    assert result is None or error is not None
