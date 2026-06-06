"""Integration tests for the Quality Agent."""

import pandas as pd
import pytest

from ai_etl.agents.quality import quality_node
from ai_etl.core.state import initial_state


def _make_state(df: pd.DataFrame) -> dict:
    state = initial_state(spec="test", run_id="test-run")
    return {**state, "transformed_data": df, "pipeline_plan": {"quality_checks": []}}


def test_clean_dataframe_has_ok_severity() -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    result = quality_node(_make_state(df))
    assert result["quality_report"]["severity"] == "ok"


def test_high_null_ratio_produces_error() -> None:
    df = pd.DataFrame({"a": [None] * 25 + [1] * 5})  # 83% nulls
    result = quality_node(_make_state(df))
    severities = [c["severity"] for c in result["quality_report"]["checks"]]
    assert "error" in severities
    assert result["quality_report"]["severity"] == "error"


def test_duplicates_produce_warning() -> None:
    df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})
    result = quality_node(_make_state(df))
    dup_checks = [c for c in result["quality_report"]["checks"] if c["check"] == "duplicate"]
    assert len(dup_checks) == 1
    assert dup_checks[0]["severity"] == "warning"


def test_error_state_is_passed_through() -> None:
    state = initial_state(spec="test", run_id="test-run")
    state_with_error = {**state, "error": "upstream failed", "transformed_data": None, "pipeline_plan": {}}
    result = quality_node(state_with_error)
    assert result["error"] == "upstream failed"
