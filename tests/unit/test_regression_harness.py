"""Unit tests for Sprint 28's prompt/agent regression harness
(`case_study/scripts/regression_harness.py`, ADR-029).

Only the pure, deterministic functions are covered here — `scenario_metrics_from_result`,
`aggregate_report`, and `compare_against_baseline` — same "majoritariamente um experimento"
boundary Sprint 8's own `tests/unit/test_model_comparison.py` draws: `run_scenario`/`main` are
I/O-heavy orchestration (real sandbox execution, real LLM-shaped call sites) exercised manually
via `uv run python case_study/scripts/regression_harness.py`, not unit-tested here.

**This file includes the literal target of Sprint 28/ADR-029's definition of done**: a
deliberately worse result (a fabricated `gold_df` that double-counts, run through the REAL
`core/output_validation.check_gold_output` and `core/pricing`-free `score_quality`, exactly the
functions the harness itself calls) must be flagged as a regression by
`compare_against_baseline` before it would reach a promoted baseline — see
`test_compare_against_baseline_catches_injected_worse_result` below.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from ai_etl.core.output_validation import check_gold_output

_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "case_study" / "scripts" / "regression_harness.py"
)
_spec = importlib.util.spec_from_file_location("regression_harness", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
regression_harness = importlib.util.module_from_spec(_spec)
sys.modules["regression_harness"] = regression_harness
_spec.loader.exec_module(regression_harness)

scenario_metrics_from_result = regression_harness.scenario_metrics_from_result
aggregate_report = regression_harness.aggregate_report
compare_against_baseline = regression_harness.compare_against_baseline


# ---------------------------------------------------------------------------
# scenario_metrics_from_result
# ---------------------------------------------------------------------------


def _completed_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "status": "completed",
        "quality_report": {"severity": "ok"},
        "load_result": {"rows_loaded": 100},
        "transformation_attempts": 1,
    }
    state.update(overrides)
    return state


def test_scenario_metrics_from_result_happy_path() -> None:
    metrics = scenario_metrics_from_result(
        scenario_id="s1",
        category="nominal_descriptive",
        expect_failure=False,
        data_source="mock",
        elapsed_ms=123.456,
        state=_completed_state(),
        gold_results=[{"error": None, "sanity_check": {"severity": "ok"}}],
        science_results=[],
    )
    assert metrics["scenario_id"] == "s1"
    assert metrics["status"] == "completed"
    assert metrics["status_matches_expectation"] is True
    assert metrics["quality_score"] == 100.0
    assert metrics["sanity_warnings"] == 0
    assert metrics["elapsed_ms"] == 123.5


def test_scenario_metrics_from_result_counts_sanity_warnings() -> None:
    metrics = scenario_metrics_from_result(
        scenario_id="s2",
        category="adversarial_edge_case",
        expect_failure=False,
        data_source="mock",
        elapsed_ms=10.0,
        state=_completed_state(),
        gold_results=[
            {"error": None, "sanity_check": {"severity": "warning"}},
            {"error": None, "sanity_check": {"severity": "ok"}},
        ],
        science_results=[{"error": None, "sanity_check": {"severity": "warning"}}],
    )
    assert metrics["sanity_warnings"] == 2


def test_scenario_metrics_from_result_ignores_errored_subtask_sanity() -> None:
    """A failed sub-task (`error` set) never had a sanity_check computed for it
    (ADR-026) — must not be miscounted as a warning."""
    metrics = scenario_metrics_from_result(
        scenario_id="s3",
        category="nominal_descriptive",
        expect_failure=False,
        data_source="mock",
        elapsed_ms=10.0,
        state=_completed_state(),
        gold_results=[{"error": "sandbox failed", "sanity_check": {"severity": "warning"}}],
        science_results=[],
    )
    assert metrics["sanity_warnings"] == 0


def test_scenario_metrics_from_result_expected_failure_matches() -> None:
    metrics = scenario_metrics_from_result(
        scenario_id="malformed",
        category="adversarial_expected_failure",
        expect_failure=True,
        data_source="mock",
        elapsed_ms=5.0,
        state={"status": "failed"},
        gold_results=[],
        science_results=[],
    )
    assert metrics["status_matches_expectation"] is True
    assert metrics["quality_score"] is None  # nothing to score on an expected failure


def test_scenario_metrics_from_result_expected_failure_but_completed_flags_mismatch() -> None:
    """A regression in extraction robustness (e.g. someone loosens
    `_validate_row_lengths`, Sprint 22) shows up here: an expected-to-fail scenario that
    now completes must be flagged as NOT matching its expectation."""
    metrics = scenario_metrics_from_result(
        scenario_id="malformed",
        category="adversarial_expected_failure",
        expect_failure=True,
        data_source="mock",
        elapsed_ms=5.0,
        state=_completed_state(),
        gold_results=[],
        science_results=[],
    )
    assert metrics["status_matches_expectation"] is False


# ---------------------------------------------------------------------------
# aggregate_report
# ---------------------------------------------------------------------------


def test_aggregate_report_computes_mean_min_and_failing_list() -> None:
    per_scenario = [
        {
            "scenario_id": "a",
            "quality_score": 100.0,
            "sanity_warnings": 0,
            "status_matches_expectation": True,
        },
        {
            "scenario_id": "b",
            "quality_score": 60.0,
            "sanity_warnings": 2,
            "status_matches_expectation": True,
        },
        {
            "scenario_id": "c",
            "quality_score": None,
            "sanity_warnings": 0,
            "status_matches_expectation": False,
        },
    ]
    report = aggregate_report(per_scenario, data_source="mock")
    assert report["n_scenarios"] == 3
    assert report["mean_quality_score"] == 80.0
    assert report["min_quality_score"] == 60.0
    assert report["total_sanity_warnings"] == 2
    assert report["scenarios_failing_expectation"] == ["c"]


def test_aggregate_report_handles_no_scored_scenarios() -> None:
    report = aggregate_report(
        [
            {
                "scenario_id": "x",
                "quality_score": None,
                "sanity_warnings": 0,
                "status_matches_expectation": True,
            }
        ],
        data_source="mock",
    )
    assert report["mean_quality_score"] is None
    assert report["min_quality_score"] is None


# ---------------------------------------------------------------------------
# compare_against_baseline — the definition-of-done target
# ---------------------------------------------------------------------------


def _baseline_report() -> dict[str, Any]:
    return {
        "mean_quality_score": 94.0,
        "min_quality_score": 88.0,
        "total_sanity_warnings": 0,
        "scenarios_failing_expectation": [],
        "per_scenario": [
            {"scenario_id": "sales_revenue_by_region", "quality_score": 94.0},
            {"scenario_id": "sales_daily_forecast", "quality_score": 88.0},
        ],
    }


def test_compare_against_baseline_passes_on_identical_run() -> None:
    baseline = _baseline_report()
    result = compare_against_baseline(baseline, baseline)
    assert result["passed"] is True
    assert result["regressions"] == []


def test_compare_against_baseline_catches_injected_worse_result() -> None:
    """Sprint 28/ADR-029's own definition of done: a deliberately-wrong Gold result
    (a fabricated gold_df that double-counts the source, run through the REAL
    `check_gold_output` — the same function the harness's `run_full_analysis` call path
    exercises) must be caught before it would be promoted as a new baseline."""
    silver_df = pd.DataFrame({"region": ["norte", "sul"], "amount": [100.0, 200.0]})  # total=300
    bad_gold_df = pd.DataFrame({"region": ["norte", "sul"], "amount": [400.0, 400.0]})  # sum=800

    sanity_check = check_gold_output(bad_gold_df, silver_df, narrative="Faturamento de 800.")
    assert sanity_check["severity"] == "warning"  # confirms the injected result is really bad

    bad_state = _completed_state()
    bad_metrics = scenario_metrics_from_result(
        scenario_id="sales_revenue_by_region",
        category="nominal_descriptive",
        expect_failure=False,
        data_source="mock",
        elapsed_ms=50.0,
        state=bad_state,
        gold_results=[{"error": None, "sanity_check": sanity_check}],
        science_results=[],
    )
    good_metrics = {
        "scenario_id": "sales_daily_forecast",
        "category": "nominal_predictive",
        "quality_score": 88.0,
        "sanity_warnings": 0,
        "status_matches_expectation": True,
    }
    current = aggregate_report([bad_metrics, good_metrics], data_source="mock")

    result = compare_against_baseline(current, _baseline_report())

    assert result["passed"] is False
    assert any("sanity-check warnings increased" in r for r in result["regressions"])


def test_compare_against_baseline_catches_mean_quality_drop() -> None:
    baseline = _baseline_report()
    current = {**baseline, "mean_quality_score": 80.0}  # 14pt drop, beyond default tolerance
    result = compare_against_baseline(current, baseline)
    assert result["passed"] is False
    assert any("mean_quality_score regressed" in r for r in result["regressions"])


def test_compare_against_baseline_catches_new_expected_failure_regression() -> None:
    baseline = _baseline_report()
    current = {**baseline, "scenarios_failing_expectation": ["malformed_quoting_expected_failure"]}
    result = compare_against_baseline(current, baseline)
    assert result["passed"] is False
    assert any("newly failing" in r for r in result["regressions"])


def test_compare_against_baseline_ignores_drop_within_tolerance() -> None:
    baseline = _baseline_report()
    current = {**baseline, "mean_quality_score": 91.0}  # 3pt drop, within default 5.0 tolerance
    result = compare_against_baseline(current, baseline)
    assert result["passed"] is True


def test_compare_against_baseline_per_scenario_regression() -> None:
    baseline = _baseline_report()
    current = {
        **baseline,
        "per_scenario": [
            {"scenario_id": "sales_revenue_by_region", "quality_score": 60.0},
            {"scenario_id": "sales_daily_forecast", "quality_score": 88.0},
        ],
    }
    result = compare_against_baseline(current, baseline)
    assert result["passed"] is False
    assert any("sales_revenue_by_region" in r for r in result["regressions"])
