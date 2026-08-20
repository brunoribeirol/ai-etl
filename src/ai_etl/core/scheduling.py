"""Cron schedule parsing for saved pipelines (Sprint 13, ADR-016).

Pure functions, no I/O — `audit/db.py` (persistence) and
`services/scheduler.py` (Celery beat task) both call these rather than
duplicating cron math. Kept in `core/` per the layering rule in
`.claude/specs/sr-standard.md` ("core/ = infraestrutura de execução"):
this is execution-scheduling infrastructure, not a domain agent.
"""

from __future__ import annotations

from datetime import datetime, timezone

from croniter import croniter

# ADR-016 Decision 3 — only "live" source types (re-resolvable from
# connection info alone, no dependency on a browser-uploaded file) may be
# scheduled. csv/document (PDF/DOCX uploads) are deliberately excluded.
SCHEDULABLE_SOURCE_TYPES = frozenset({"postgres", "sqlite", "mysql", "mongodb", "rest"})


class InvalidCronScheduleError(ValueError):
    """Raised when a `cron_schedule` string is not a valid 5-field cron expression."""


def validate_cron_schedule(cron_schedule: str) -> None:
    """Raise `InvalidCronScheduleError` if `cron_schedule` cannot be parsed.

    Called at `POST /pipelines` / `PATCH /pipelines/{id}` time so an invalid
    schedule is rejected at save time (400), never silently persisted and
    only discovered when `services/scheduler.py`'s beat task chokes on it.
    """
    if not croniter.is_valid(cron_schedule):
        raise InvalidCronScheduleError(f"Invalid cron schedule: {cron_schedule!r}")


def compute_next_run_at(cron_schedule: str, base_time: datetime | None = None) -> datetime:
    """Return the next UTC fire time strictly after `base_time` (default: now).

    Assumes `cron_schedule` has already been validated via
    `validate_cron_schedule` — raises the same `InvalidCronScheduleError` if
    not, rather than letting croniter's own exception type leak to callers.
    """
    base = base_time or datetime.now(tz=timezone.utc)
    try:
        it = croniter(cron_schedule, base)
        next_time: datetime = it.get_next(datetime)
    except (ValueError, KeyError) as e:
        raise InvalidCronScheduleError(f"Invalid cron schedule: {cron_schedule!r}") from e
    if next_time.tzinfo is None:
        next_time = next_time.replace(tzinfo=timezone.utc)
    return next_time
