"""Tenant data deletion — LGPD/GDPR right to erasure (Sprint 24, ADR-025).

Hard-deletes everything a tenant's own account owns: `runs`,
`analysis_runs`, `stage_latencies`, `saved_pipelines`, `tenant_secrets`
rows, the storage artifacts those runs produced (`audit/storage.py`), and
finally the `users` row itself. A minimal `tenant_deletion_log` row survives
the deletion it describes — evidence for the SOC2 self-assessment that an
erasure request was fulfilled, without recreating the personal-data problem
it solves (see ADR-025 Decision 2).

Deletion order (ADR-025 Decision 3): storage artifacts first (their key
derivation needs the DB rows to still exist), then one DB transaction in FK
dependency order — `stage_latencies` -> `analysis_runs` -> `runs` ->
`saved_pipelines` -> `tenant_secrets` -> `users`. No existing FK's
`ondelete` behavior is touched; this module is the entire cascade.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from typing_extensions import TypedDict

from ai_etl.audit.connection import get_engine
from ai_etl.audit.models import (
    analysis_runs,
    runs,
    saved_pipelines,
    stage_latencies,
    tenant_deletion_log,
    tenant_secrets,
    users,
)
from ai_etl.audit.storage import StorageBackend, get_storage_backend

logger = logging.getLogger(__name__)


class TenantNotFoundError(LookupError):
    """Raised when `tenant_id` has no `users` row — nothing to delete."""

    def __init__(self, tenant_id: str):
        super().__init__(f"No tenant found for id={tenant_id!r}")
        self.tenant_id = tenant_id


class TenantDeletionSummary(TypedDict):
    tenant_id: str
    requested_at: datetime
    completed_at: datetime
    runs_deleted: int
    analysis_runs_deleted: int
    stage_latencies_deleted: int
    saved_pipelines_deleted: int
    secrets_deleted: int
    storage_keys_deleted: int
    status: str
    error: str | None


def _candidate_storage_keys(
    run_id: str, gold_subtasks: int, science_subtasks: int, has_analysis: bool
) -> list[str]:
    """Every key `save_run`/`save_analysis` (`audit/db.py`) might have written
    for one run, per their documented naming convention. A key that was never
    written for a particular run (e.g. no `_transform.py` because the spec had
    no LLM-generated transform) is simply not found and skipped —
    `delete_bytes` never raises for a missing key, but we still check
    `exists()` first here so `storage_keys_deleted` counts only keys that
    really existed, not every candidate attempted.
    """
    keys = [f"{run_id}.json", f"{run_id}_transform.py", f"{run_id}_silver.csv"]
    if has_analysis:
        keys.append(f"{run_id}_analysis.json")
        for i in range(gold_subtasks):
            keys.append(f"{run_id}_gold_{i}.csv")
            keys.append(f"{run_id}_gold_{i}_fig.json")
        for i in range(science_subtasks):
            keys.append(f"{run_id}_science_{i}.csv")
            keys.append(f"{run_id}_science_{i}_fig.json")
    return keys


def _delete_tenant_storage(
    engine: Engine, tenant_id: str, storage: StorageBackend
) -> tuple[int, list[str]]:
    """Best-effort deletion of every storage artifact a tenant's runs produced.

    Returns `(keys_deleted, errors)`. Never raises — a storage failure (e.g.
    a transient S3 error) is recorded in `errors` and does not block the
    DB-side erasure that follows (ADR-025 Decision 3: the DB rows are the
    higher-confidence compliance signal).
    """
    with engine.connect() as conn:
        run_ids = {
            row.run_id
            for row in conn.execute(select(runs.c.run_id).where(runs.c.tenant_id == tenant_id))
        }
        analysis_rows = list(
            conn.execute(
                select(
                    analysis_runs.c.run_id,
                    analysis_runs.c.gold_subtasks,
                    analysis_runs.c.science_subtasks,
                ).where(analysis_runs.c.tenant_id == tenant_id)
            )
        )
    analysis_by_run = {row.run_id: row for row in analysis_rows}
    run_ids |= set(analysis_by_run)

    deleted = 0
    errors: list[str] = []
    for run_id in sorted(run_ids):
        analysis_row = analysis_by_run.get(run_id)
        candidates = _candidate_storage_keys(
            run_id,
            gold_subtasks=analysis_row.gold_subtasks if analysis_row else 0,
            science_subtasks=analysis_row.science_subtasks if analysis_row else 0,
            has_analysis=analysis_row is not None,
        )
        for key in candidates:
            try:
                if storage.exists(key):
                    storage.delete_bytes(key)
                    deleted += 1
            except Exception as exc:  # noqa: BLE001 - best-effort, see docstring
                errors.append(f"{key}: {exc}")
                logger.warning(
                    "tenant_deletion_storage_error",
                    extra={"tenant_id": tenant_id, "key": key, "error": str(exc)},
                )
    return deleted, errors


def _write_deletion_log(
    engine: Engine,
    *,
    tenant_id: str,
    requested_by: str,
    requested_at: datetime,
    completed_at: datetime | None,
    counts: dict[str, int],
    status: str,
    error: str | None,
) -> None:
    stmt = pg_insert(tenant_deletion_log).values(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        requested_by=requested_by,
        requested_at=requested_at,
        completed_at=completed_at,
        status=status,
        error=error,
        **counts,
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def delete_tenant_data(
    tenant_id: str, requested_by: str, log_dir: str = "./runs"
) -> TenantDeletionSummary:
    """Hard-delete `tenant_id`'s data end-to-end (ADR-025).

    Args:
        tenant_id: The tenant whose data is erased. Always the caller's own
            resolved tenant id at the API layer (`DELETE /tenant` is
            self-service only — see ADR-025 Decision 1).
        requested_by: Who initiated the deletion — same as `tenant_id` for
            today's self-service-only scope, kept as a separate field so a
            future admin-initiated deletion doesn't need a schema change.
        log_dir: Passed through to `get_storage_backend` for the `local`
            backend (`s3` ignores it, same as every other caller).

    Raises:
        TenantNotFoundError: No `users` row exists for `tenant_id` — nothing
            to delete. Callers (the API router) map this to `404`.
    """
    engine = get_engine()
    requested_at = datetime.now(tz=timezone.utc)

    with engine.connect() as conn:
        user_row = conn.execute(select(users.c.id).where(users.c.id == tenant_id)).first()
    if user_row is None:
        raise TenantNotFoundError(tenant_id)

    storage = get_storage_backend(log_dir, tenant_id)
    storage_keys_deleted, storage_errors = _delete_tenant_storage(engine, tenant_id, storage)

    try:
        with engine.begin() as conn:
            stage_latencies_deleted = conn.execute(
                delete(stage_latencies).where(stage_latencies.c.tenant_id == tenant_id)
            ).rowcount
            analysis_runs_deleted = conn.execute(
                delete(analysis_runs).where(analysis_runs.c.tenant_id == tenant_id)
            ).rowcount
            runs_deleted = conn.execute(delete(runs).where(runs.c.tenant_id == tenant_id)).rowcount
            saved_pipelines_deleted = conn.execute(
                delete(saved_pipelines).where(saved_pipelines.c.tenant_id == tenant_id)
            ).rowcount
            secrets_deleted = conn.execute(
                delete(tenant_secrets).where(tenant_secrets.c.tenant_id == tenant_id)
            ).rowcount
            conn.execute(delete(users).where(users.c.id == tenant_id))
    except Exception as exc:
        _write_deletion_log(
            engine,
            tenant_id=tenant_id,
            requested_by=requested_by,
            requested_at=requested_at,
            completed_at=None,
            counts={
                "runs_deleted": 0,
                "analysis_runs_deleted": 0,
                "stage_latencies_deleted": 0,
                "saved_pipelines_deleted": 0,
                "secrets_deleted": 0,
                "storage_keys_deleted": storage_keys_deleted,
            },
            status="failed",
            error=str(exc),
        )
        raise

    completed_at = datetime.now(tz=timezone.utc)
    error = "; ".join(storage_errors) if storage_errors else None
    counts = {
        "runs_deleted": runs_deleted,
        "analysis_runs_deleted": analysis_runs_deleted,
        "stage_latencies_deleted": stage_latencies_deleted,
        "saved_pipelines_deleted": saved_pipelines_deleted,
        "secrets_deleted": secrets_deleted,
        "storage_keys_deleted": storage_keys_deleted,
    }
    _write_deletion_log(
        engine,
        tenant_id=tenant_id,
        requested_by=requested_by,
        requested_at=requested_at,
        completed_at=completed_at,
        counts=counts,
        status="completed",
        error=error,
    )
    logger.info(
        "tenant_data_deleted",
        extra={"tenant_id": tenant_id, "requested_by": requested_by, **counts},
    )
    return TenantDeletionSummary(
        tenant_id=tenant_id,
        requested_at=requested_at,
        completed_at=completed_at,
        status="completed",
        error=error,
        runs_deleted=runs_deleted,
        analysis_runs_deleted=analysis_runs_deleted,
        stage_latencies_deleted=stage_latencies_deleted,
        saved_pipelines_deleted=saved_pipelines_deleted,
        secrets_deleted=secrets_deleted,
        storage_keys_deleted=storage_keys_deleted,
    )
