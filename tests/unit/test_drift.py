"""Unit tests for core/drift.py — pure KPI comparison math (Sprint 14, ADR-018)."""

from ai_etl.core.drift import compute_pct_change, detect_kpi_drift


def test_compute_pct_change_positive_increase() -> None:
    assert compute_pct_change(100.0, 150.0) == 50.0


def test_compute_pct_change_negative_decrease() -> None:
    assert compute_pct_change(100.0, 50.0) == -50.0


def test_compute_pct_change_previous_zero_is_none() -> None:
    assert compute_pct_change(0.0, 42.0) is None


def test_compute_pct_change_unchanged_is_zero() -> None:
    assert compute_pct_change(100.0, 100.0) == 0.0


def test_detect_kpi_drift_flags_metric_over_threshold() -> None:
    findings = detect_kpi_drift(
        previous={"rows_loaded": 1000.0}, current={"rows_loaded": 1300.0}, threshold_pct=20.0
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding["name"] == "rows_loaded"
    assert finding["previous"] == 1000.0
    assert finding["current"] == 1300.0
    assert finding["pct_change"] == 30.0
    assert finding["triggered"] is True


def test_detect_kpi_drift_does_not_flag_metric_under_threshold() -> None:
    findings = detect_kpi_drift(
        previous={"rows_loaded": 1000.0}, current={"rows_loaded": 1050.0}, threshold_pct=20.0
    )
    assert findings[0]["triggered"] is False


def test_detect_kpi_drift_exactly_at_threshold_is_triggered() -> None:
    """`>=`, not `>` — a change exactly at the configured threshold counts."""
    findings = detect_kpi_drift(
        previous={"cost_usd": 10.0}, current={"cost_usd": 12.0}, threshold_pct=20.0
    )
    assert findings[0]["triggered"] is True


def test_detect_kpi_drift_skips_metrics_missing_from_previous() -> None:
    """A KPI only present in the current run (e.g. a new Science sub-task
    question) has no meaningful prior value — must not be reported as
    infinite drift."""
    findings = detect_kpi_drift(
        previous={"rows_loaded": 1000.0},
        current={"rows_loaded": 1000.0, "science · new question · rmse": 5.0},
        threshold_pct=20.0,
    )
    assert len(findings) == 1
    assert findings[0]["name"] == "rows_loaded"


def test_detect_kpi_drift_zero_to_nonzero_is_triggered_with_no_pct() -> None:
    findings = detect_kpi_drift(
        previous={"rows_loaded": 0.0}, current={"rows_loaded": 500.0}, threshold_pct=20.0
    )
    assert findings[0]["pct_change"] is None
    assert findings[0]["triggered"] is True


def test_detect_kpi_drift_zero_to_zero_is_not_triggered() -> None:
    findings = detect_kpi_drift(
        previous={"rows_loaded": 0.0}, current={"rows_loaded": 0.0}, threshold_pct=20.0
    )
    assert findings[0]["pct_change"] is None
    assert findings[0]["triggered"] is False


def test_detect_kpi_drift_negative_change_uses_absolute_value_for_threshold() -> None:
    findings = detect_kpi_drift(
        previous={"cost_usd": 10.0}, current={"cost_usd": 5.0}, threshold_pct=20.0
    )
    assert findings[0]["pct_change"] == -50.0
    assert findings[0]["triggered"] is True


def test_detect_kpi_drift_returns_all_comparisons_not_just_triggered() -> None:
    findings = detect_kpi_drift(
        previous={"rows_loaded": 1000.0, "cost_usd": 1.0},
        current={"rows_loaded": 1001.0, "cost_usd": 5.0},
        threshold_pct=20.0,
    )
    assert len(findings) == 2
    triggered_names = {f["name"] for f in findings if f["triggered"]}
    assert triggered_names == {"cost_usd"}
