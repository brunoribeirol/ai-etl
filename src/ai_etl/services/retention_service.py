"""Automatic run-artifact retention cleanup (Sprint 36, ADR-035).

`core/celery_app.py`'s `beat_schedule` calls `cleanup_expired_retention_task`
on a fixed interval (`AI_ETL_RETENTION_INTERVAL_SECONDS`, default 24h — a
compliance sweep, not a latency-sensitive job, so a much coarser cadence than
`services/scheduler.py`'s 60s pipeline-firing tick is deliberate). Each tick:
list every tenant with a configured `retention_days` window
(`audit/db/retention.py::list_tenants_with_retention`), and for each, delete
every storage artifact (`audit/storage.py::StorageBackend.delete_bytes`,
ADR-025) belonging to a run older than that window.

Unlike `services/tenant_deletion_service.py` (ADR-025), this never deletes a
`runs`/`analysis_runs` DB row, nor the `users` row — only the storage bytes
those runs produced. The DB rows (run metadata, cost, status) stay forever
unless a tenant separately requests full account deletion; only the
underlying dataset artifacts (the actual personal-data exposure — see
ADR-025's own investigation) expire. This mirrors the product roadmap's own
framing: retention is about artifact lifecycle, not a second, quieter
account-deletion path.

One `retention_cleanup_log` row (migration `0018`) is written per tenant per
tick — evidence a cleanup pass ran, same "survives what it describes" shape
as `tenant_deletion_log` (ADR-025 Decision 2), but per pass rather than per
irreversible event.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from typing_extensions import TypedDict

from ai_etl.audit.connection import get_engine, tenant_scope
from ai_etl.audit.db.retention import list_tenants_with_retention
from ai_etl.audit.models import analysis_runs, retention_cleanup_log, runs
from ai_etl.audit.storage import get_storage_backend
from ai_etl.core.celery_app import celery_app
from ai_etl.services.tenant_deletion_service import candidate_storage_keys_for_run

logger = logging.getLogger(__name__)


class TenantRetentionCleanupSummary(TypedDict):
    tenant_id: str
    retention_days: int
    runs_scanned: int
    storage_keys_deleted: int
    status: str
    error: str | None


def _expired_run_storage_candidates(tenant_id: str, cutoff: datetime) -> dict[str, list[str]]:
    """`{run_id: [candidate storage keys]}` for every run/analysis run of
    `tenant_id` whose `timestamp` is older than `cutoff` — same candidate-key
    derivation as `tenant_deletion_service.tenant_run_storage_candidates`,
    just scoped to expired runs instead of every run a tenant owns (a
    retention sweep must never touch an artifact still inside its window).

    Reads through `tenant_scope()` (ADR-040 follow-up) — same restricted,
    RLS-backed role every other per-tenant read in this project now uses;
    `_write_cleanup_log` below stays on the bypass engine, since
    `retention_cleanup_log` has RLS enabled with no policy (ADR-040's
    documented exemption list).
    """
    with tenant_scope(tenant_id) as conn:
        run_ids = {
            row.run_id
            for row in conn.execute(
                select(runs.c.run_id).where(
                    runs.c.tenant_id == tenant_id, runs.c.timestamp < cutoff
                )
            )
        }
        analysis_rows = list(
            conn.execute(
                select(
                    analysis_runs.c.run_id,
                    analysis_runs.c.gold_subtasks,
                    analysis_runs.c.science_subtasks,
                ).where(analysis_runs.c.tenant_id == tenant_id, analysis_runs.c.timestamp < cutoff)
            )
        )
    analysis_by_run = {row.run_id: row for row in analysis_rows}
    run_ids |= set(analysis_by_run)

    result: dict[str, list[str]] = {}
    for run_id in sorted(run_ids):
        analysis_row = analysis_by_run.get(run_id)
        result[run_id] = candidate_storage_keys_for_run(
            run_id,
            gold_subtasks=analysis_row.gold_subtasks if analysis_row else 0,
            science_subtasks=analysis_row.science_subtasks if analysis_row else 0,
            has_analysis=analysis_row is not None,
        )
    return result


def _write_cleanup_log(
    engine: Engine,
    *,
    tenant_id: str,
    retention_days: int,
    started_at: datetime,
    completed_at: datetime | None,
    runs_scanned: int,
    storage_keys_deleted: int,
    status: str,
    error: str | None,
) -> None:
    stmt = pg_insert(retention_cleanup_log).values(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        retention_days=retention_days,
        started_at=started_at,
        completed_at=completed_at,
        runs_scanned=runs_scanned,
        storage_keys_deleted=storage_keys_deleted,
        status=status,
        error=error,
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def cleanup_expired_retention_for_tenant(
    tenant_id: str,
    retention_days: int,
    log_dir: str = "./runs",
    now: datetime | None = None,
) -> TenantRetentionCleanupSummary:
    """Delete every storage artifact of `tenant_id` belonging to a run older
    than `retention_days`. Best-effort per artifact (mirrors
    `tenant_deletion_service._delete_tenant_storage`): a single storage
    failure is recorded in `error` and does not stop the rest of the sweep
    for this tenant — `status` stays `"completed"` with a non-empty `error`
    noting the partial failure, never raised.

    Never touches `runs`/`analysis_runs` DB rows — see module docstring.
    """
    # `retention_cleanup_log` (below) is bypass-engine-only by design (ADR-040's
    # exemption list) — `log_engine` names that connection is specifically for it,
    # not for the tenant-scoped reads above.
    log_engine = get_engine()
    started_at = datetime.now(tz=timezone.utc)
    cutoff = (now or started_at) - timedelta(days=retention_days)

    candidates_by_run = _expired_run_storage_candidates(tenant_id, cutoff)
    storage = get_storage_backend(log_dir, tenant_id)

    deleted = 0
    errors: list[str] = []
    for run_id, candidates in candidates_by_run.items():
        for key in candidates:
            try:
                if storage.exists(key):
                    storage.delete_bytes(key)
                    deleted += 1
            except Exception as exc:  # noqa: BLE001 - best-effort, see docstring
                errors.append(f"{key}: {exc}")
                logger.warning(
                    "retention_cleanup_storage_error",
                    extra={"tenant_id": tenant_id, "run_id": run_id, "key": key, "error": str(exc)},
                )

    completed_at = datetime.now(tz=timezone.utc)
    error = "; ".join(errors) if errors else None
    runs_scanned = len(candidates_by_run)
    _write_cleanup_log(
        log_engine,
        tenant_id=tenant_id,
        retention_days=retention_days,
        started_at=started_at,
        completed_at=completed_at,
        runs_scanned=runs_scanned,
        storage_keys_deleted=deleted,
        status="completed",
        error=error,
    )
    logger.info(
        "retention_cleanup_completed",
        extra={
            "tenant_id": tenant_id,
            "retention_days": retention_days,
            "runs_scanned": runs_scanned,
            "storage_keys_deleted": deleted,
        },
    )
    return TenantRetentionCleanupSummary(
        tenant_id=tenant_id,
        retention_days=retention_days,
        runs_scanned=runs_scanned,
        storage_keys_deleted=deleted,
        status="completed",
        error=error,
    )


@celery_app.task(name="ai_etl.cleanup_expired_retention")  # type: ignore[untyped-decorator]  # celery has no type stubs for @task
def cleanup_expired_retention_task() -> dict[str, Any]:
    """Sweep every tenant with a configured retention window. Returns a
    small summary dict (tenants processed, total storage keys deleted) —
    useful for beat log inspection, not consumed by any caller.

    A single tenant's `StorageBackend` blowing up entirely (not just one
    key) is caught and skipped so it never blocks the sweep for every other
    tenant in the same tick — same "one bad unit must not break the tick"
    contract as `services/scheduler.py`'s `check_scheduled_pipelines_task`.
    """
    processed: list[str] = []
    skipped: list[str] = []
    total_keys_deleted = 0

    for policy in list_tenants_with_retention():
        tenant_id = policy["tenant_id"]
        retention_days = policy["retention_days"]
        if retention_days is None:
            continue
        try:
            summary = cleanup_expired_retention_for_tenant(tenant_id, retention_days)
        except Exception:  # nosec B110 — one bad tenant must not break the tick
            skipped.append(tenant_id)
            continue
        processed.append(tenant_id)
        total_keys_deleted += summary["storage_keys_deleted"]

    return {
        "processed": processed,
        "skipped": skipped,
        "total_storage_keys_deleted": total_keys_deleted,
    }
