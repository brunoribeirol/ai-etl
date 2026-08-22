"""Tests for `ai_etl.core.output_validation` (Sprint 21, ADR-026).

Includes the roadmap's own definition of done: a deliberately wrong result
(injected here, not hypothetical) must be flagged (`severity != "ok"`), never
silently accepted.
"""

from __future__ import annotations

import pandas as pd

from ai_etl.core.output_validation import check_gold_output, check_science_output

# ---------------------------------------------------------------------------
# check_gold_output
# ---------------------------------------------------------------------------


def test_check_gold_output_flags_sum_exceeding_silver_total() -> None:
    """The roadmap's own worked example: 'a soma bate com o total esperado do
    Silver?' — a deliberately fabricated gold_df whose sum is larger than the
    Silver total it claims to summarize."""
    silver_df = pd.DataFrame({"category": ["a", "b", "c"], "amount": [10, 20, 30]})  # total = 60
    gold_df = pd.DataFrame({"category": ["a", "b"], "amount": [500, 500]})  # fabricated, sum=1000

    result = check_gold_output(gold_df, silver_df, narrative="Total de 1000")

    assert result["severity"] == "warning"
    sum_checks = [c for c in result["checks"] if c["check"] == "sum_conservation"]
    assert len(sum_checks) == 1
    assert sum_checks[0]["severity"] == "warning"


def test_check_gold_output_passes_a_legitimate_aggregation() -> None:
    silver_df = pd.DataFrame({"category": ["a", "b", "c"], "amount": [10, 20, 30]})
    gold_df = silver_df.groupby("category", as_index=False)["amount"].sum()

    result = check_gold_output(gold_df, silver_df, narrative="Resumo por categoria")

    assert result["severity"] == "ok"
    assert all(c["severity"] == "ok" for c in result["checks"])


def test_check_gold_output_flags_more_rows_than_silver() -> None:
    silver_df = pd.DataFrame({"a": [1, 2]})
    gold_df = pd.DataFrame({"a": [1, 2, 3, 4]})

    result = check_gold_output(gold_df, silver_df, narrative="")

    assert result["severity"] == "warning"
    assert any(
        c["check"] == "row_count_bound" and c["severity"] == "warning" for c in result["checks"]
    )


def test_check_gold_output_flags_empty_result_with_numeric_narrative() -> None:
    silver_df = pd.DataFrame({"a": [1, 2]})
    gold_df = pd.DataFrame()

    result = check_gold_output(gold_df, silver_df, narrative="O total foi de 42 unidades")

    assert result["severity"] == "warning"
    assert any(c["check"] == "empty_result" for c in result["checks"])


def test_check_gold_output_no_applicable_checks_returns_ok() -> None:
    result = check_gold_output(pd.DataFrame({"x": [1]}), pd.DataFrame(), narrative="")

    assert result["severity"] == "ok"
    assert result["checks"] == []


# ---------------------------------------------------------------------------
# check_science_output
# ---------------------------------------------------------------------------


def test_check_science_output_flags_metric_outside_valid_range() -> None:
    predictions_df = pd.DataFrame({"actual": [1, 2], "predicted": [1, 2]})
    model_info = {"task": "regression", "target": "y", "metrics": {"r2": 1.9}}

    result = check_science_output(predictions_df, model_info, pd.DataFrame())

    assert result["severity"] == "warning"
    assert any(
        c["check"] == "metric_range" and c["severity"] == "warning" for c in result["checks"]
    )


def test_check_science_output_accepts_a_legitimately_bad_but_valid_fit() -> None:
    predictions_df = pd.DataFrame({"actual": [1, 2], "predicted": [1, 2]})
    model_info = {"task": "regression", "target": "y", "metrics": {"r2": -0.3, "mae": 4.2}}

    result = check_science_output(predictions_df, model_info, pd.DataFrame())

    assert result["severity"] == "ok"


def test_check_science_output_flags_prediction_far_outside_historical_range() -> None:
    """A deliberately wrong prediction: historical target ranges 10-100, but the
    model 'predicts' 1,000,000 — an order-of-magnitude sanity failure."""
    silver_df = pd.DataFrame({"y": [10, 20, 50, 100]})
    predictions_df = pd.DataFrame({"date": ["2026-01", "2026-02"], "forecast": [110, 1_000_000]})
    model_info = {"task": "forecast", "target": "y", "metrics": {}}

    result = check_science_output(predictions_df, model_info, silver_df)

    assert result["severity"] == "warning"
    assert any(
        c["check"] == "prediction_range" and c["severity"] == "warning" for c in result["checks"]
    )


def test_check_science_output_allows_reasonable_extrapolation() -> None:
    silver_df = pd.DataFrame({"y": [10, 20, 50, 100]})
    predictions_df = pd.DataFrame({"date": ["2026-01", "2026-02"], "forecast": [110, 150]})
    model_info = {"task": "forecast", "target": "y", "metrics": {}}

    result = check_science_output(predictions_df, model_info, silver_df)

    assert result["severity"] == "ok"


def test_check_science_output_skips_prediction_range_for_non_forecast_tasks() -> None:
    silver_df = pd.DataFrame({"y": [10, 20]})
    predictions_df = pd.DataFrame({"cluster": [0, 1]})
    model_info = {"task": "clustering", "target": "segments", "metrics": {}}

    result = check_science_output(predictions_df, model_info, silver_df)

    assert result["severity"] == "ok"
    assert result["checks"] == []


def test_check_science_output_ignores_non_dict_metrics_key_gracefully() -> None:
    result = check_science_output(
        pd.DataFrame({"a": [1]}), {"metrics": "not-a-dict"}, pd.DataFrame()
    )

    assert result["severity"] == "ok"
    assert result["checks"] == []
