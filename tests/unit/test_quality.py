"""Unit tests for the Quality agent."""

import pandas as pd

from ai_etl.agents.quality import (
    _check_duplicates,
    _check_nulls,
    _check_outliers_iqr,
    quality_node,
)
from ai_etl.core.state import initial_state


def _state_with_df(df: pd.DataFrame) -> dict:
    state = initial_state(spec="test", run_id="q-test-1")
    return {**state, "transformed_data": df}


# --- quality_node ---


def test_quality_node_short_circuits_on_upstream_error() -> None:
    state = initial_state(spec="test", run_id="q-err")
    state_with_error = {**state, "error": "upstream failed"}
    result = quality_node(state_with_error)  # type: ignore[arg-type]
    assert result["error"] == "upstream failed"
    assert result.get("quality_report") == {}  # short-circuit: quality_report untouched


def test_quality_node_produces_report() -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    result = quality_node(_state_with_df(df))  # type: ignore[arg-type]
    report = result["quality_report"]
    assert report is not None
    assert "severity" in report
    assert "checks" in report
    assert "summary" in report


def test_quality_node_ok_severity_for_clean_data() -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    result = quality_node(_state_with_df(df))  # type: ignore[arg-type]
    assert result["quality_report"]["severity"] == "ok"


def test_quality_node_adds_audit_log_entry() -> None:
    df = pd.DataFrame({"x": [1, 2]})
    result = quality_node(_state_with_df(df))  # type: ignore[arg-type]
    assert len(result["audit_log"]) == 1
    assert result["audit_log"][0]["agent"] == "quality"


def test_quality_node_error_severity_for_high_nulls() -> None:
    df = pd.DataFrame({"a": [1, None, None, None, None, None]})
    result = quality_node(_state_with_df(df))  # type: ignore[arg-type]
    assert result["quality_report"]["severity"] == "error"
    assert result["status"] == "failed"  # quality-blocked runs must not stay "running"


def test_quality_node_warning_severity_for_duplicates() -> None:
    df = pd.DataFrame({"a": [1, 1, 2], "b": [3, 3, 4]})
    result = quality_node(_state_with_df(df))  # type: ignore[arg-type]
    assert result["quality_report"]["severity"] == "warning"


# --- _check_nulls ---


def test_check_nulls_no_nulls_returns_empty() -> None:
    df = pd.DataFrame({"a": [1, 2, 3]})
    assert _check_nulls(df) == []


def test_check_nulls_low_nulls_returns_warning() -> None:
    df = pd.DataFrame(
        {"a": [None, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]}
    )
    checks = _check_nulls(df)
    assert len(checks) == 1
    assert checks[0]["severity"] == "warning"
    assert checks[0]["column"] == "a"


def test_check_nulls_high_nulls_returns_error() -> None:
    df = pd.DataFrame({"a": [None, None, None, None, None, 1]})
    checks = _check_nulls(df)
    assert checks[0]["severity"] == "error"


# --- _check_duplicates ---


def test_check_duplicates_no_dups_returns_ok() -> None:
    df = pd.DataFrame({"a": [1, 2, 3]})
    result = _check_duplicates(df)
    assert result["severity"] == "ok"
    assert result["count"] == 0


def test_check_duplicates_with_dups_returns_warning() -> None:
    df = pd.DataFrame({"a": [1, 1, 2]})
    result = _check_duplicates(df)
    assert result["severity"] == "warning"
    assert result["count"] == 1


# --- _check_outliers_iqr ---


def test_check_outliers_no_outliers_returns_empty() -> None:
    df = pd.DataFrame({"a": [1, 2, 3, 4, 5]})
    assert _check_outliers_iqr(df) == []


def test_check_outliers_detects_extreme_values() -> None:
    values = list(range(1, 21)) + [1000]
    df = pd.DataFrame({"a": values})
    checks = _check_outliers_iqr(df)
    assert len(checks) == 1
    assert checks[0]["column"] == "a"
    assert checks[0]["severity"] == "warning"
    assert checks[0]["outlier_count"] >= 1


def test_check_outliers_skips_zero_iqr_column() -> None:
    df = pd.DataFrame({"a": [5, 5, 5, 5, 5]})
    assert _check_outliers_iqr(df) == []


def test_check_outliers_ignores_string_columns() -> None:
    df = pd.DataFrame({"a": ["x", "y", "z"], "b": [1, 2, 1000]})
    checks = _check_outliers_iqr(df)
    for c in checks:
        assert c["column"] != "a"
