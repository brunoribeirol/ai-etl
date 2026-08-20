"""Unit tests for Sprint 8's model comparison harness (`case_study/scripts/
model_comparison.py`). Only the pure, deterministic helpers are covered here —
`score_quality` (the objective quality metric) and `compute_stability` (the
variance/stability computation) — per the sr-standard's guidance that this
script is "majoritariamente um experimento" rather than new business logic.
`run_scenario1`/`main` are I/O-heavy orchestration (subprocess-shaped, real
sandbox execution) exercised manually and documented in
`case_study/results/sprint8/README.md`, not unit-tested here.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "case_study" / "scripts" / "model_comparison.py"
)
_spec = importlib.util.spec_from_file_location("model_comparison", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
model_comparison = importlib.util.module_from_spec(_spec)
sys.modules["model_comparison"] = model_comparison
_spec.loader.exec_module(model_comparison)

score_quality = model_comparison.score_quality
compute_stability = model_comparison.compute_stability
RunResult = model_comparison.RunResult


class TestScoreQuality:
    def test_failed_pipeline_scores_zero(self) -> None:
        state = {"status": "failed", "quality_report": {"severity": "ok"}}
        assert score_quality(state) == 0.0

    def test_full_score_ok_severity_one_attempt_no_reference(self) -> None:
        state = {
            "status": "completed",
            "quality_report": {"severity": "ok"},
            "load_result": {"rows_loaded": 100},
            "transformation_attempts": 1,
        }
        # 40 (completed) + 30 (ok) + 20 (rows_loaded > 0, no reference) + 10 (1 attempt)
        assert score_quality(state) == 100.0

    def test_warning_severity_and_two_attempts(self) -> None:
        state = {
            "status": "completed",
            "quality_report": {"severity": "warning"},
            "load_result": {"rows_loaded": 100},
            "transformation_attempts": 2,
        }
        # 40 + 18 (warning) + 20 (rows_loaded>0, no reference) + 5 (2 attempts)
        assert score_quality(state) == 83.0

    def test_error_severity_scores_zero_for_that_component(self) -> None:
        state = {
            "status": "completed",
            "quality_report": {"severity": "error"},
            "load_result": {"rows_loaded": 100},
            "transformation_attempts": 1,
        }
        # 40 + 0 (error) + 20 + 10
        assert score_quality(state) == 70.0

    def test_zero_rows_loaded_scores_no_completeness_points(self) -> None:
        state = {
            "status": "completed",
            "quality_report": {"severity": "ok"},
            "load_result": {"rows_loaded": 0},
            "transformation_attempts": 1,
        }
        # 40 + 30 + 0 (rows_loaded == 0) + 10
        assert score_quality(state) == 80.0

    def test_reference_rows_scales_completeness_proportionally(self) -> None:
        state = {
            "status": "completed",
            "quality_report": {"severity": "ok"},
            "load_result": {"rows_loaded": 50},
            "transformation_attempts": 1,
        }
        # completeness = 20 * min(1.0, 50/100) = 10
        assert score_quality(state, reference_rows=100) == 90.0

    def test_reference_rows_caps_completeness_at_full_marks(self) -> None:
        state = {
            "status": "completed",
            "quality_report": {"severity": "ok"},
            "load_result": {"rows_loaded": 150},
            "transformation_attempts": 1,
        }
        # min(1.0, 150/100) == 1.0 -> still 20, not 30
        assert score_quality(state, reference_rows=100) == 100.0

    def test_three_or_more_attempts_scores_zero_efficiency_points(self) -> None:
        state = {
            "status": "completed",
            "quality_report": {"severity": "ok"},
            "load_result": {"rows_loaded": 10},
            "transformation_attempts": 3,
        }
        assert score_quality(state) == 90.0  # 40 + 30 + 20 + 0

    def test_missing_optional_fields_default_gracefully(self) -> None:
        # A minimal completed state (e.g. a degenerate mocked run) must not raise.
        state = {"status": "completed"}
        assert score_quality(state) == 40.0 + 0.0 + 0.0 + 10.0  # no severity, no rows, 1 attempt


class TestComputeStability:
    def _run(self, **overrides: Any) -> Any:
        defaults: dict[str, Any] = dict(
            model="gpt-4o-mini",
            provider="openai",
            scenario=1,
            run_index=1,
            data_source="mock",
            status="completed",
            elapsed_ms=1000.0,
            rows_loaded=100,
            quality_score=88.0,
        )
        defaults.update(overrides)
        return RunResult(**defaults)

    def test_identical_runs_have_zero_stdev(self) -> None:
        rows = [self._run(run_index=i) for i in range(1, 6)]
        stability = compute_stability(rows)
        assert stability["n"] == 5
        assert stability["n_completed"] == 5
        assert stability["elapsed_ms"]["stdev"] == 0.0
        assert stability["elapsed_ms"]["mean"] == 1000.0

    def test_variance_is_detected_and_cv_computed(self) -> None:
        rows = [
            self._run(run_index=1, elapsed_ms=1000.0),
            self._run(run_index=2, elapsed_ms=2000.0),
        ]
        stability = compute_stability(rows)
        assert stability["elapsed_ms"]["mean"] == 1500.0
        assert stability["elapsed_ms"]["stdev"] == pytest.approx(707.107, abs=0.01)
        assert stability["elapsed_ms"]["cv"] == pytest.approx(0.4714, abs=0.001)

    def test_single_run_has_zero_stdev_not_a_crash(self) -> None:
        rows = [self._run(run_index=1)]
        stability = compute_stability(rows)
        assert stability["elapsed_ms"]["stdev"] == 0.0

    def test_failed_runs_excluded_from_stability_metrics(self) -> None:
        rows = [
            self._run(run_index=1, status="completed", elapsed_ms=1000.0),
            self._run(run_index=2, status="failed", elapsed_ms=None, quality_score=0.0),
        ]
        stability = compute_stability(rows)
        assert stability["n"] == 2
        assert stability["n_completed"] == 1
        assert stability["elapsed_ms"]["mean"] == 1000.0

    def test_no_completed_runs_yields_none_metrics(self) -> None:
        rows = [self._run(run_index=1, status="failed", elapsed_ms=None, quality_score=None)]
        stability = compute_stability(rows)
        assert stability["n_completed"] == 0
        assert stability["elapsed_ms"] is None


class TestProviderAndAccess:
    def test_ollama_model_never_reports_real_without_binary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(model_comparison.shutil, "which", lambda _: None)
        provider, has_access = model_comparison._provider_and_access("ollama:llama3.1")
        assert provider == "ollama"
        assert has_access is False

    def test_openai_model_requires_env_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        provider, has_access = model_comparison._provider_and_access("gpt-4o-mini")
        assert provider == "openai"
        assert has_access is False

        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-test")
        _, has_access_with_key = model_comparison._provider_and_access("gpt-4o-mini")
        assert has_access_with_key is True
