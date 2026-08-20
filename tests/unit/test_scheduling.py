"""Unit tests for core/scheduling.py (Sprint 13, ADR-016)."""

from datetime import datetime, timezone

import pytest

from ai_etl.core.scheduling import (
    SCHEDULABLE_SOURCE_TYPES,
    InvalidCronScheduleError,
    compute_next_run_at,
    validate_cron_schedule,
)


def test_schedulable_source_types_excludes_upload_only_sources() -> None:
    """ADR-016 Decision 3 — csv/document (browser uploads) must never be schedulable."""
    assert "csv" not in SCHEDULABLE_SOURCE_TYPES
    assert "document" not in SCHEDULABLE_SOURCE_TYPES
    assert SCHEDULABLE_SOURCE_TYPES == {"postgres", "sqlite", "mysql", "mongodb", "rest"}


def test_validate_cron_schedule_accepts_valid_expression() -> None:
    validate_cron_schedule("0 3 * * *")  # should not raise


def test_validate_cron_schedule_rejects_invalid_expression() -> None:
    with pytest.raises(InvalidCronScheduleError):
        validate_cron_schedule("not a cron expression")


def test_compute_next_run_at_returns_time_strictly_after_base() -> None:
    base = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    next_run = compute_next_run_at("0 3 * * *", base)  # daily at 03:00 UTC
    assert next_run > base
    assert next_run.hour == 3


def test_compute_next_run_at_short_interval_advances_by_one_minute() -> None:
    """The "runs 3 times consecutively" definition-of-done needs a
    short-interval schedule (e.g. every minute) to advance predictably."""
    base = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    next_run = compute_next_run_at("* * * * *", base)
    assert next_run == datetime(2026, 8, 19, 12, 1, 0, tzinfo=timezone.utc)


def test_compute_next_run_at_defaults_to_now_when_no_base_given() -> None:
    before = datetime.now(tz=timezone.utc)
    next_run = compute_next_run_at("* * * * *")
    assert next_run > before


def test_compute_next_run_at_rejects_invalid_schedule() -> None:
    with pytest.raises(InvalidCronScheduleError):
        compute_next_run_at("garbage")


def test_compute_next_run_at_attaches_utc_to_naive_base_time() -> None:
    """A naive `base_time` (no tzinfo) must not silently propagate a naive
    result — croniter preserves the input's tz-awareness, so this covers
    the explicit UTC fallback."""
    naive_base = datetime(2026, 8, 19, 12, 0, 0)  # no tzinfo
    next_run = compute_next_run_at("* * * * *", naive_base)
    assert next_run.tzinfo is not None
