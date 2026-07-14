"""Unit tests for the Quality agent."""

import pandas as pd

from ai_etl.agents.quality import (
    _check_duplicates,
    _check_logical_duplicates,
    _check_nulls,
    _check_outliers_iqr,
    _check_types,
    quality_node,
)
from ai_etl.core.state import initial_state


def _state_with_df(df: pd.DataFrame, source_schemas: dict | None = None) -> dict:
    state = initial_state(spec="test", run_id="q-test-1")
    return {**state, "transformed_data": df, "source_schemas": source_schemas or {}}


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
    assert result["error"]  # must not be None — callers render it directly


def test_quality_node_ok_severity_leaves_error_untouched() -> None:
    df = pd.DataFrame({"a": [1, 2, 3]})
    result = quality_node(_state_with_df(df))  # type: ignore[arg-type]
    assert result["error"] is None


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


# --- _check_types ---


def test_check_types_no_source_schemas_returns_empty() -> None:
    df = pd.DataFrame({"a": [1, 2, 3]})
    assert _check_types(df, {}) == []


def test_check_types_matching_dtype_returns_empty() -> None:
    df = pd.DataFrame({"a": [1, 2, 3]})
    source_schemas = {"orders": {"dtypes": {"a": "int64"}}}
    assert _check_types(df, source_schemas) == []


def test_check_types_mismatch_returns_warning() -> None:
    df = pd.DataFrame({"a": ["1", "2", "3"]})
    source_schemas = {"orders": {"dtypes": {"a": "int64"}}}
    checks = _check_types(df, source_schemas)
    assert len(checks) == 1
    assert checks[0]["check"] == "type_mismatch"
    assert checks[0]["column"] == "a"
    assert checks[0]["severity"] == "warning"
    assert checks[0]["actual_dtype"] != "int64"
    assert checks[0]["expected_dtype"] == ["int64"]


def test_check_types_ignores_columns_not_in_any_source() -> None:
    df = pd.DataFrame({"new_col": [1, 2, 3]})
    source_schemas = {"orders": {"dtypes": {"a": "int64"}}}
    assert _check_types(df, source_schemas) == []


def test_check_types_matches_if_any_source_dtype_agrees() -> None:
    df = pd.DataFrame({"customer_id": [1, 2, 3]})
    source_schemas = {
        "orders": {"dtypes": {"customer_id": "object"}},
        "customers": {"dtypes": {"customer_id": "int64"}},
    }
    assert _check_types(df, source_schemas) == []


def test_quality_node_includes_type_mismatch_in_severity() -> None:
    df = pd.DataFrame({"a": ["1", "2", "3"]})
    source_schemas = {"orders": {"dtypes": {"a": "int64"}}}
    result = quality_node(_state_with_df(df, source_schemas))  # type: ignore[arg-type]
    checks = result["quality_report"]["checks"]
    assert any(c["check"] == "type_mismatch" for c in checks)
    assert result["quality_report"]["severity"] == "warning"


# --- _check_logical_duplicates ---


def test_check_logical_duplicates_flags_repeated_names() -> None:
    names = [f"App {i}" for i in range(20)] + ["App 0", "App 0"]  # 2 extra repeats of "App 0"
    df = pd.DataFrame({"name": names})
    checks = _check_logical_duplicates(df)
    assert len(checks) == 1
    assert checks[0]["check"] == "duplicate_by_column"
    assert checks[0]["column"] == "name"
    assert checks[0]["group_count"] == 1
    assert checks[0]["row_count"] == 3
    assert checks[0]["severity"] == "warning"


def test_check_logical_duplicates_ignores_low_cardinality_category() -> None:
    df = pd.DataFrame({"category": ["Games", "Tools"] * 10})
    assert _check_logical_duplicates(df) == []


def test_check_logical_duplicates_no_repeats_returns_empty() -> None:
    df = pd.DataFrame({"name": [f"App {i}" for i in range(10)]})
    assert _check_logical_duplicates(df) == []


def test_check_logical_duplicates_ignores_numeric_columns() -> None:
    df = pd.DataFrame({"amount": [1, 1, 1, 2, 3, 4, 5, 6, 7, 8]})
    assert _check_logical_duplicates(df) == []
