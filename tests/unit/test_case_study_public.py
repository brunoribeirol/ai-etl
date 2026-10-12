"""Unit tests for the public-data case-study tooling (`case_study/data/
fetch_public_data.py` and `case_study/scripts/run_case_study.py`).

Only the pure helpers are covered: the reproducible extract (`select_trips`), the
quality description, the outcome classification, the summary and the output
checks (`case_study/scripts/output_checks.py`). Downloading, seeding PostgreSQL
and the real-model runs are I/O exercised by the manual `case-study` workflow,
not here.
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
output_checks = _load("output_checks", "case_study/scripts/output_checks.py")

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
                output_check="match",
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
                output_check="mismatch",  # completed, yet the content is wrong
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
        assert entry["output_check"] == {"match": 1, "mismatch": 1, "not_checked": 1}
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


# --- Output checks: a completed run is compared with a plain-pandas reference ---------

_WEEK = [f"2025-01-{day:02d} 08:30:00" for day in range(6, 13)]


def _raw_trips() -> pd.DataFrame:
    """Trips as the pipeline reads them from the CSV: timestamps are still text."""
    return pd.DataFrame(
        {
            "tpep_pickup_datetime": _WEEK,
            "tpep_dropoff_datetime": [value.replace("08:30", "08:50") for value in _WEEK],
            "PULocationID": [1, 1, 2, 2, 264, 265, 2],
            "trip_distance": [1.0, 2.0, 0.0, 3.0, 4.0, 5.0, 6.0],
            "fare_amount": [10.0, 20.0, 30.0, -5.0, 40.0, 50.0, 60.0],
            "total_amount": [12.0, 24.0, 33.0, -5.0, 44.0, 55.0, 66.0],
        }
    )


def _zones() -> pd.DataFrame:
    """Shaped like the published table, including its two zones with a null in the key."""
    return pd.DataFrame(
        {
            "LocationID": [1, 2, 264, 265],
            "Borough": ["Queens", "Manhattan", "Unknown", None],
            "Zone": ["Astoria", "Midtown", None, "Outside of NYC"],
        }
    )


def _scenario1_output(trips: pd.DataFrame) -> pd.DataFrame:
    kept = trips[(trips["fare_amount"] > 0) & (trips["trip_distance"] > 0)].drop_duplicates()
    return kept.rename(
        columns={"tpep_pickup_datetime": "pickup_at", "tpep_dropoff_datetime": "dropoff_at"}
    )


class TestCheckScenario1:
    def test_a_correct_output_matches(self) -> None:
        trips = _raw_trips()
        result = output_checks.check_scenario1(_scenario1_output(trips), trips)
        assert result["verdict"] == "match"
        assert result["facts"]["rows"] == result["facts"]["reference_rows"] == 5

    def test_day_and_month_swapped_is_a_mismatch_although_every_row_is_there(self) -> None:
        """The real finding of 2026-10-12: right row count, right columns, wrong dates."""
        trips = _raw_trips()
        output = _scenario1_output(trips)
        output["pickup_at"] = pd.to_datetime(output["pickup_at"], dayfirst=True).astype(str)

        result = output_checks.check_scenario1(output, trips)

        assert result["verdict"] == "mismatch"
        assert result["facts"]["rows"] == result["facts"]["reference_rows"]
        assert result["facts"]["pickup_at"]["equal_to_reference"] is False
        assert result["facts"]["pickup_at"]["max"] > result["facts"]["pickup_at"]["reference_max"]
        assert result["facts"]["dropoff_at"]["equal_to_reference"] is True

    def test_a_filter_left_out_is_a_mismatch(self) -> None:
        trips = _raw_trips()
        output = trips.rename(
            columns={"tpep_pickup_datetime": "pickup_at", "tpep_dropoff_datetime": "dropoff_at"}
        )
        result = output_checks.check_scenario1(output, trips)
        assert result["verdict"] == "mismatch"
        assert (result["facts"]["rows"], result["facts"]["reference_rows"]) == (7, 5)

    def test_columns_not_renamed_is_a_mismatch(self) -> None:
        trips = _raw_trips()
        kept = trips[(trips["fare_amount"] > 0) & (trips["trip_distance"] > 0)]
        result = output_checks.check_scenario1(kept, trips)
        assert result["verdict"] == "mismatch"
        assert result["facts"]["pickup_at"]["present"] is False


class TestCheckScenario2:
    def test_sql_style_output_with_null_key_groups_matches(self) -> None:
        trips, zones = _raw_trips(), _zones()
        output = output_checks.reference_scenario2(trips, zones)
        result = output_checks.check_scenario2(output, trips, zones)
        assert result["verdict"] == "match"
        assert result["facts"]["reference_groups_with_null_key"] == 2
        assert result["facts"]["trips"] == result["facts"]["reference_trips"] == 6

    def test_pandas_default_output_matches_and_reports_the_trips_it_left_out(self) -> None:
        """`groupby` drops null keys by default. The verdict does not judge that; the
        facts show how many trips are absent so the author can report it."""
        trips, zones = _raw_trips(), _zones()
        output = output_checks.reference_scenario2(trips, zones).dropna(subset=["Borough", "Zone"])
        result = output_checks.check_scenario2(output, trips, zones)
        assert result["verdict"] == "match"
        assert result["facts"]["groups_with_null_key"] == 0
        assert result["facts"]["reference_trips_in_null_key_groups"] == 2
        assert result["facts"]["trips"] == 4

    def test_a_wrong_average_is_a_mismatch(self) -> None:
        trips, zones = _raw_trips(), _zones()
        output = output_checks.reference_scenario2(trips, zones)
        output.loc[output["Zone"] == "Astoria", "avg_total_amount"] += 1.0
        result = output_checks.check_scenario2(output, trips, zones)
        assert result["verdict"] == "mismatch"
        assert result["facts"]["groups_with_different_average"] == 1

    def test_the_filter_left_out_is_a_mismatch(self) -> None:
        trips, zones = _raw_trips(), _zones()
        output = output_checks.reference_scenario2(trips.assign(fare_amount=1.0), zones)
        result = output_checks.check_scenario2(output, trips, zones)
        assert result["verdict"] == "mismatch"
        assert result["facts"]["groups_with_different_trips"] == 1

    def test_lower_case_column_names_are_the_same_columns(self) -> None:
        trips, zones = _raw_trips(), _zones()
        output = output_checks.reference_scenario2(trips, zones).rename(
            columns={"Borough": "borough", "Zone": "zone"}
        )
        assert output_checks.check_scenario2(output, trips, zones)["verdict"] == "match"

    def test_a_missing_column_is_a_mismatch(self) -> None:
        trips, zones = _raw_trips(), _zones()
        output = output_checks.reference_scenario2(trips, zones).drop(columns=["trips"])
        result = output_checks.check_scenario2(output, trips, zones)
        assert result["verdict"] == "mismatch"
        assert result["facts"]["missing_columns"] == ["trips"]


_WEATHER = {f"2025-01-{day:02d}": (float(day), day / 10) for day in range(6, 13)}


def _scenario3_output(trips: pd.DataFrame, zones: pd.DataFrame) -> pd.DataFrame:
    output = output_checks.reference_scenario3(trips, zones).copy()
    days = output["tpep_pickup_datetime"].str[:10]
    output["max_temp_c"] = days.map(lambda day: _WEATHER[day][0])
    output["precipitation_mm"] = days.map(lambda day: _WEATHER[day][1])
    return output


class TestCheckScenario3:
    def test_each_row_carrying_its_own_days_weather_matches(self) -> None:
        trips, zones = _raw_trips(), _zones()
        result = output_checks.check_scenario3(
            _scenario3_output(trips, zones), trips, zones, _WEATHER
        )
        assert result["verdict"] == "match"
        assert result["facts"]["dates_with_weather_different_from_api"] == 0

    def test_weather_of_another_day_is_a_mismatch(self) -> None:
        trips, zones = _raw_trips(), _zones()
        output = _scenario3_output(trips, zones)
        output["max_temp_c"] = output["max_temp_c"].iloc[::-1].to_numpy()
        result = output_checks.check_scenario3(output, trips, zones, _WEATHER)
        assert result["verdict"] == "mismatch"
        assert result["facts"]["dates_with_weather_different_from_api"] > 0

    def test_rows_left_without_weather_are_a_mismatch(self) -> None:
        trips, zones = _raw_trips(), _zones()
        output = _scenario3_output(trips, zones)
        output.loc[output.index[0], "max_temp_c"] = None
        result = output_checks.check_scenario3(output, trips, zones, _WEATHER)
        assert result["verdict"] == "mismatch"
        assert result["facts"]["rows_without_weather"] == 1

    def test_swapped_pickup_timestamps_are_a_mismatch(self) -> None:
        trips, zones = _raw_trips(), _zones()
        output = _scenario3_output(trips, zones)
        output["tpep_pickup_datetime"] = pd.to_datetime(
            output["tpep_pickup_datetime"], dayfirst=True
        )
        result = output_checks.check_scenario3(output, trips, zones, _WEATHER)
        assert result["verdict"] == "mismatch"
        assert result["facts"]["pickup"]["equal_to_reference"] is False

    def test_without_the_api_answer_the_facts_say_so(self) -> None:
        trips, zones = _raw_trips(), _zones()
        result = output_checks.check_scenario3(_scenario3_output(trips, zones), trips, zones, None)
        assert result["verdict"] == "match"
        assert result["facts"]["weather_reference"] == "unavailable"
        assert "dates_with_weather_different_from_api" not in result["facts"]


def test_weather_payload_is_read_as_one_pair_per_date() -> None:
    payload = {
        "daily": {
            "time": ["2025-01-06", "2025-01-07"],
            "temperature_2m_max": [1.5, -0.4],
            "precipitation_sum": [0.0, 3.2],
        }
    }
    assert output_checks.weather_from_api_payload(payload) == {
        "2025-01-06": (1.5, 0.0),
        "2025-01-07": (-0.4, 3.2),
    }


def test_a_failing_check_is_recorded_as_not_checked_never_as_a_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _unreadable(*_args: object, **_kwargs: object) -> pd.DataFrame:
        raise FileNotFoundError("scenario1_output.csv")

    monkeypatch.setattr(run_case_study.pd, "read_csv", _unreadable)
    result = run_case_study.verify_output(run_case_study.SCENARIOS[1], tmp_path)
    assert result["verdict"] == "not_checked"
    assert "FileNotFoundError" in result["facts"]["check_error"]
