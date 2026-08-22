"""Unit tests for Sprint 34's load-test script (`case_study/scripts/
load_test.py`). Only the pure, deterministic helpers are covered here —
`_percentile`, `summarize`, and `_orchestrator_side_effect` — per the same
convention `test_model_comparison.py` documents: `main()`/`_run_one` are
I/O-heavy orchestration (real sandbox execution, real threads), exercised
manually and documented in `case_study/results/sprint34/README.md`, not
unit-tested here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[2] / "case_study" / "scripts" / "load_test.py"
_spec = importlib.util.spec_from_file_location("load_test", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
load_test = importlib.util.module_from_spec(_spec)
sys.modules["load_test"] = load_test
_spec.loader.exec_module(load_test)

_percentile = load_test._percentile
summarize = load_test.summarize
RequestResult = load_test.RequestResult
_orchestrator_side_effect = load_test._orchestrator_side_effect


class TestPercentile:
    def test_empty_list_returns_none(self) -> None:
        assert _percentile([], 0.95) is None

    def test_single_value(self) -> None:
        assert _percentile([5.0], 0.95) == 5.0

    def test_p50_of_sorted_values(self) -> None:
        # 5 values, p50 (median) is the middle one.
        assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0

    def test_p95_interpolates_between_top_values(self) -> None:
        values = [float(i) for i in range(1, 11)]  # 1..10
        result = _percentile(values, 0.95)
        assert result is not None
        assert 9.0 <= result <= 10.0

    def test_unsorted_input_is_sorted_first(self) -> None:
        assert _percentile([5.0, 1.0, 3.0], 0.0) == 1.0


class TestSummarize:
    def test_all_completed_zero_error_rate(self) -> None:
        results = [
            RequestResult(
                tenant_id="t1",
                run_index=0,
                status="completed",
                elapsed_ms=1000.0,
                stage_durations={"transformer": 0.5},
            ),
            RequestResult(
                tenant_id="t2",
                run_index=0,
                status="completed",
                elapsed_ms=2000.0,
                stage_durations={"transformer": 0.7},
            ),
        ]
        summary = summarize(results)

        assert summary["n_requests"] == 2
        assert summary["n_completed"] == 2
        assert summary["n_errored"] == 0
        assert summary["error_rate"] == 0.0
        assert summary["error_rate_within_slo"] is True

    def test_errors_increase_error_rate_and_can_breach_slo(self) -> None:
        results = [
            RequestResult(tenant_id="t1", run_index=0, status="completed", elapsed_ms=1000.0),
            RequestResult(tenant_id="t2", run_index=0, status="error", error="boom"),
        ]
        summary = summarize(results)

        assert summary["n_errored"] == 1
        assert summary["error_rate"] == 0.5
        assert summary["error_rate_within_slo"] is False  # 0.5 > SLO_ERROR_RATE_MAX (0.01)

    def test_empty_results(self) -> None:
        summary = summarize([])
        assert summary["n_requests"] == 0
        assert summary["error_rate"] == 0.0
        assert summary["error_rate_within_slo"] is True

    def test_per_stage_flags_slo_breach(self) -> None:
        # transformer SLO is 20.0s (SLO_P95_STAGE_SECONDS) — 25s p95 breaches it.
        results = [
            RequestResult(
                tenant_id="t1",
                run_index=0,
                status="completed",
                elapsed_ms=1.0,
                stage_durations={"transformer": 25.0},
            )
        ]
        summary = summarize(results)
        assert summary["per_stage"]["transformer"]["within_slo"] is False

    def test_stage_with_no_data_reports_none_and_within_slo(self) -> None:
        results = [RequestResult(tenant_id="t1", run_index=0, status="completed", elapsed_ms=1.0)]
        summary = summarize(results)
        assert summary["per_stage"]["orchestrator"]["p95"] is None
        assert summary["per_stage"]["orchestrator"]["within_slo"] is True


class TestOrchestratorSideEffect:
    def test_extracts_destination_path_from_prompt(self) -> None:
        prompt = (
            "You are a data pipeline planner.\n\nThe user provided this spec:\n"
            '"Read the file x.csv. Save the result to /tmp/tenant_a/output.csv."\n'
        )
        response = _orchestrator_side_effect(prompt)
        plan = json.loads(response.content)
        assert plan["destination"]["path"] == "/tmp/tenant_a/output.csv"

    def test_falls_back_when_pattern_not_found(self) -> None:
        response = _orchestrator_side_effect("no destination sentence here")
        plan = json.loads(response.content)
        assert plan["destination"]["path"]  # falls back to a non-empty default path

    def test_different_prompts_get_different_destinations(self) -> None:
        prompt_a = "Save the result to /tmp/a/output.csv."
        prompt_b = "Save the result to /tmp/b/output.csv."
        plan_a = json.loads(_orchestrator_side_effect(prompt_a).content)
        plan_b = json.loads(_orchestrator_side_effect(prompt_b).content)
        assert plan_a["destination"]["path"] != plan_b["destination"]["path"]
