"""Unit tests for `api/serialization.py` (ADR-011).

Previously untested at the unit level (2026-09-04 coverage gap-closing pass) —
`_serialize_figure`/`_serialize_analysis_entry`'s DataFrame/Figure branches were
only ever exercised indirectly, if at all, through `test_api_runs.py`'s mocked
`load_full_result` results (none of which happen to carry a real Figure).
"""

import pandas as pd
import plotly.express as px

from ai_etl.api.serialization import (
    _serialize_analysis_entry,
    _serialize_dataframe,
    _serialize_figure,
    nan_to_none_records,
    serialize_full_result,
)


def test_nan_to_none_records_converts_nan_to_none() -> None:
    records = [{"a": 1.0, "b": float("nan")}]

    result = nan_to_none_records(records)

    assert result == [{"a": 1.0, "b": None}]


def test_nan_to_none_records_leaves_non_float_values_untouched() -> None:
    records = [{"name": "Widget A", "active": True, "count": 3}]

    result = nan_to_none_records(records)

    assert result == [{"name": "Widget A", "active": True, "count": 3}]


def test_serialize_dataframe_converts_to_records_and_cleans_nan() -> None:
    df = pd.DataFrame({"product": ["A", "B"], "amount": [100.0, float("nan")]})

    result = _serialize_dataframe(df)

    assert result == [{"product": "A", "amount": 100.0}, {"product": "B", "amount": None}]


def test_serialize_figure_returns_plotly_json_shape() -> None:
    fig = px.bar(pd.DataFrame({"x": ["A", "B"], "y": [1, 2]}), x="x", y="y")

    result = _serialize_figure(fig)

    # Plotly.js's `Plotly.newPlot(el, data, layout)` reads this exact shape.
    assert "data" in result
    assert "layout" in result


def test_serialize_analysis_entry_converts_df_and_fig_when_present() -> None:
    df = pd.DataFrame({"product": ["A"], "amount": [100.0]})
    fig = px.bar(df, x="product", y="amount")
    entry = {"gold_df": df, "fig": fig, "narrative": "Widget A sells the most."}

    result = _serialize_analysis_entry(entry, "gold_df")

    assert result["gold_df"] == [{"product": "A", "amount": 100.0}]
    assert "data" in result["fig"]
    assert result["narrative"] == "Widget A sells the most."


def test_serialize_analysis_entry_leaves_entry_untouched_when_df_and_fig_absent() -> None:
    """A failed sub-task's entry (auto-repair exhausted) carries an error
    string instead of gold_df/fig — must pass through unchanged, not raise."""
    entry = {"error": "sandbox execution failed"}

    result = _serialize_analysis_entry(entry, "gold_df")

    assert result == {"error": "sandbox execution failed"}


def test_serialize_full_result_converts_transformed_data() -> None:
    df = pd.DataFrame({"id": [1, 2]})
    result = serialize_full_result({"state": {"transformed_data": df}})

    assert result["state"]["transformed_data"] == [{"id": 1}, {"id": 2}]


def test_serialize_full_result_converts_every_gold_and_science_entry() -> None:
    gold_df = pd.DataFrame({"product": ["A"], "amount": [10.0]})
    science_df = pd.DataFrame({"product": ["A"], "predicted": [12.0]})
    result = serialize_full_result(
        {
            "state": {},
            "gold": [{"gold_df": gold_df, "narrative": "n1"}],
            "science": [{"predictions_df": science_df, "narrative": "n2"}],
        }
    )

    assert result["gold"][0]["gold_df"] == [{"product": "A", "amount": 10.0}]
    assert result["science"][0]["predictions_df"] == [{"product": "A", "predicted": 12.0}]


def test_serialize_full_result_defaults_missing_optional_fields() -> None:
    """A Silver-only run (no Agentic BI analysis attached) has no
    `gold`/`science`/`advisor`/`question`/`tokens` keys at all."""
    result = serialize_full_result({"bronze": None, "state": {}})

    assert result == {
        "bronze": None,
        "state": {},
        "gold": [],
        "science": [],
        "advisor": {},
        "question": "",
        "tokens": {},
    }


def test_serialize_full_result_handles_missing_state_key() -> None:
    """`load_full_result` always sets `state`, but this guards the `or {}`
    fallback rather than assuming it — a missing/`None` state must not crash
    `dict()`."""
    result = serialize_full_result({})

    assert result["state"] == {}
