"""Unit tests for the public-data case-study tooling (`case_study/data/
fetch_public_data.py` and `case_study/scripts/run_case_study.py`).

Only the pure helpers are covered: the reproducible extract (`select_trips`), the
quality description, the outcome classification and the summary. Downloading,
seeding PostgreSQL and the real-model runs are I/O exercised by the manual
`case-study` workflow, not here.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative_path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _REPO_ROOT / relative_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fetch_public_data = _load("fetch_public_data", "case_study/data/fetch_public_data.py")
run_case_study = _load("run_case_study", "case_study/scripts/run_case_study.py")

RunRecord = run_case_study.RunRecord


def _trips(pickups: list[str]) -> pd.DataFrame:
    timestamps = pd.to_datetime(pickups)
    return pd.DataFrame(
        {
            "tpep_pickup_datetime": timestamps,
            "tpep_dropoff_datetime": timestamps + pd.offsets.Minute(10),
            "passenger_count": [1.0, None, 0.0, 2.0, 1.0][: len(pickups)],
            "trip_distance": [1.5, 0.0, 3.2, 2.0, 9.9][: len(pickups)],
            "fare_amount": [10.0, -3.0, 0.0, 12.5, 40.0][: len(pickups)],
        }
    )


class TestSelectTrips:
    def test_window_is_inclusive_of_the_last_day(self) -> None:
        trips = _trips(
            [
                "2025-01-05 23:59:59",
                "2025-01-06 00:00:00",
                "2025-01-12 23:59:59",
                "2025-01-13 00:00:00",
            ]
        )
        selected = fetch_public_data.select_trips(trips, "2025-01-06", "2025-01-12", 100, 42)
        assert list(selected["tpep_pickup_datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")) == [
            "2025-01-06 00:00:00",
            "2025-01-12 23:59:59",
        ]

    def test_same_seed_gives_the_same_sample(self) -> None:
        trips = _trips([f"2025-01-06 0{i}:00:00" for i in range(5)])
        first = fetch_public_data.select_trips(trips, "2025-01-06", "2025-01-06", 3, 42)
        second = fetch_public_data.select_trips(trips, "2025-01-06", "2025-01-06", 3, 42)
        assert len(first) == 3
        pd.testing.assert_frame_equal(first, second)

    def test_values_are_passed_through_unaltered(self) -> None:
        """The extract must keep the published nulls, zeros and negatives: repairing
        them here would hide exactly what the pipeline is evaluated on."""
        trips = _trips([f"2025-01-06 0{i}:00:00" for i in range(5)])
        selected = fetch_public_data.select_trips(trips, "2025-01-06", "2025-01-06", 100, 42)
        pd.testing.assert_frame_equal(selected.reset_index(drop=True), trips)


def test_describe_quality_counts_the_published_problems() -> None:
    trips = _trips([f"2025-01-06 0{i}:00:00" for i in range(5)])
    described = fetch_public_data.describe_quality(trips)
    assert described["rows"] == 5
    assert described["exact_duplicate_rows"] == 0
    assert described["null_counts"] == {"passenger_count": 1}
    assert described["fare_amount_le_zero"] == 2
    assert described["trip_distance_le_zero"] == 1
    assert described["passenger_count_zero"] == 1


class TestClassifyOutcome:
    @pytest.mark.parametrize(
        ("status", "severity", "error", "expected"),
        [
            ("completed", "warning", None, "complete_success"),
            ("failed", "error", "quality gate blocked the load", "partial_success"),
            ("failed", None, "transformer exhausted 3 attempts", "recoverable_failure"),
            ("crashed", None, "KeyError: 'x'", "unrecoverable_failure"),
            ("failed", None, None, "unrecoverable_failure"),
        ],
    )
    def test_maps_a_run_onto_the_protocol_classes(
        self, status: str, severity: str | None, error: str | None, expected: str
    ) -> None:
        assert run_case_study.classify_outcome(status, severity, error) == expected


class TestSummarize:
    def _record(self, run_index: int, status: str, **overrides: object) -> RunRecord:
        record = RunRecord(
            scenario=1, model="gpt-4o-mini", provider="openai", run_index=run_index, status=status
        )
        for key, value in overrides.items():
            setattr(record, key, value)
        return record

    def test_counts_and_means_cover_completed_runs_only(self) -> None:
        records = [
            self._record(
                1,
                "completed",
                outcome="complete_success",
                elapsed_ms=1000.0,
                transformation_attempts=1,
                rows_loaded=100,
                cost_usd=0.01,
                quality_severity="warning",
            ),
            self._record(
                2,
                "completed",
                outcome="complete_success",
                elapsed_ms=3000.0,
                transformation_attempts=3,
                rows_loaded=100,
                cost_usd=0.03,
                quality_severity="warning",
            ),
            self._record(
                3, "failed", outcome="recoverable_failure", elapsed_ms=9000.0, note="boom"
            ),
        ]
        (entry,) = run_case_study.summarize(records)
        assert entry["runs_requested"] == 3
        assert entry["runs_executed"] == 3
        assert entry["runs_completed"] == 2
        assert entry["outcomes"] == {"complete_success": 2, "recoverable_failure": 1}
        assert entry["quality_severity"] == {"warning": 2, "none": 1}
        assert entry["elapsed_ms"]["mean"] == 2000.0  # the failed run's 9000 ms is excluded
        assert entry["transformation_attempts"]["max"] == 3.0
        assert entry["cost_usd"]["mean"] == 0.02

    def test_skipped_runs_are_reported_separately_never_as_results(self) -> None:
        records = [self._record(1, "skipped", note="no credential for provider 'openai'")]
        (entry,) = run_case_study.summarize(records)
        assert entry["runs_requested"] == 1
        assert entry["runs_executed"] == 0
        assert entry["runs_completed"] == 0
        assert entry["elapsed_ms"] is None
        assert entry["skipped_reasons"] == ["no credential for provider 'openai'"]


def test_run_once_skips_instead_of_mocking_when_the_credential_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing provider key must yield a `skipped` record with the reason, and
    must never reach the pipeline: this runner has no mocked mode by design."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def _must_not_run(**_: object) -> None:
        raise AssertionError("run_full_analysis must not be called without a credential")

    monkeypatch.setattr(run_case_study.pipeline_service, "run_full_analysis", _must_not_run)
    record = run_case_study.run_once(
        run_case_study.SCENARIOS[1], "gpt-4o-mini", 1, tmp_path, persist_audit=False
    )
    assert record.status == "skipped"
    assert "no credential" in record.note


def test_every_scenario_points_at_files_that_exist() -> None:
    for scenario in run_case_study.SCENARIOS.values():
        assert scenario.spec(), scenario.spec_file
        if scenario.question_file is not None:
            assert scenario.question(), scenario.question_file
