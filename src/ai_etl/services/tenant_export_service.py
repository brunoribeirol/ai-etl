"""Tenant data export — LGPD Art. 9/18 II, GDPR Art. 15/20 (Sprint 36, ADR-035).

`GET /tenant/export` reuses `tenant_deletion_service`'s scope-strict pattern
(ADR-025): always the caller's own resolved `tenant_id` from the verified
JWT, never an arbitrary tenant, no cross-tenant access. Unlike deletion, this
is read-only and available to `viewer` role too (the same "a tenant can
already read back everything stored about its own account" access right
already documented in `docs/compliance/lgpd-gdpr-data-processing.md` §6).

Exports every row a tenant owns across `runs`, `analysis_runs`,
`stage_latencies`, `saved_pipelines` (the same table set
`tenant_deletion_service` erases) plus `tenant_secrets` **metadata only**
(id/name/timestamps — never the Fernet ciphertext, let alone a decrypted
value) and the list of storage artifact keys the tenant's runs produced
(reusing `tenant_deletion_service.tenant_run_storage_candidates`, filtered to
keys that actually `exists()` in the configured `StorageBackend` — see
ADR-035 for why this ships keys/metadata, not artifact bytes, inline).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from typing_extensions import TypedDict

from ai_etl.audit.connection import get_engine
from ai_etl.audit.models import (
    analysis_runs,
    runs,
    saved_pipelines,
    stage_latencies,
    tenant_secrets,
    users,
)
from ai_etl.audit.storage import get_storage_backend
from ai_etl.services.tenant_deletion_service import (
    TenantNotFoundError,
    tenant_run_storage_candidates,
)

__all__ = ["TenantExport", "export_tenant_data", "TenantNotFoundError"]


class TenantExport(TypedDict):
    tenant_id: str
    exported_at: datetime
    user: dict[str, Any]
    runs: list[dict[str, Any]]
    analysis_runs: list[dict[str, Any]]
    stage_latencies: list[dict[str, Any]]
    saved_pipelines: list[dict[str, Any]]
    tenant_secrets: list[dict[str, Any]]
    storage_artifacts: list[dict[str, Any]]


def _rows(engine: Any, table: Any, tenant_id: str) -> list[dict[str, Any]]:
    stmt = select(table).where(table.c.tenant_id == tenant_id)
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings().all()]


def export_tenant_data(tenant_id: str, log_dir: str = "./runs") -> TenantExport:
    """Build the full self-service export for `tenant_id`.

    Args:
        tenant_id: Always the caller's own resolved tenant id at the API
            layer (`GET /tenant/export` never accepts a path/query
            `tenant_id` — see the router docstring).
        log_dir: Passed through to `get_storage_backend` for the `local`
            backend, same as `delete_tenant_data`.

    Raises:
        TenantNotFoundError: No `users` row exists for `tenant_id`. Mapped to
            `404` by the API router, same contract as `DELETE /tenant`.
    """
    engine = get_engine()

    with engine.connect() as conn:
        user_row = conn.execute(
            select(users.c.id, users.c.created_at, users.c.monthly_budget_usd).where(
                users.c.id == tenant_id
            )
        ).first()
    if user_row is None:
        raise TenantNotFoundError(tenant_id)

    secrets_rows = _rows(engine, tenant_secrets, tenant_id)
    # Metadata only — `ciphertext` (the encrypted credential) is never part
    # of an export, decrypted or not (ADR-035 Decision, mirrors
    # `services/secrets_service.py`'s logging discipline).
    secrets_metadata = [{k: v for k, v in row.items() if k != "ciphertext"} for row in secrets_rows]

    storage = get_storage_backend(log_dir, tenant_id)
    # Not migrated to tenant_scope() in this pass (out of scope — see ADR-040
    # follow-up) — tenant_run_storage_candidates() now takes a Connection
    # (ADR-040 follow-up on tenant_deletion_service.py) rather than an
    # Engine, so this still-bypass-role call site just opens its own
    # connection instead of passing the engine straight through.
    with engine.connect() as conn:
        candidates_by_run = tenant_run_storage_candidates(conn, tenant_id)
    storage_artifacts: list[dict[str, Any]] = []
    for run_id, candidates in candidates_by_run.items():
        for key in candidates:
            if storage.exists(key):
                storage_artifacts.append({"run_id": run_id, "key": key})

    return TenantExport(
        tenant_id=tenant_id,
        exported_at=datetime.now(tz=timezone.utc),
        user={
            "id": user_row.id,
            "created_at": user_row.created_at,
            "monthly_budget_usd": user_row.monthly_budget_usd,
        },
        runs=_rows(engine, runs, tenant_id),
        analysis_runs=_rows(engine, analysis_runs, tenant_id),
        stage_latencies=_rows(engine, stage_latencies, tenant_id),
        saved_pipelines=_rows(engine, saved_pipelines, tenant_id),
        tenant_secrets=secrets_metadata,
        storage_artifacts=storage_artifacts,
    )
