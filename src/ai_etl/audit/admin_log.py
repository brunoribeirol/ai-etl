"""Persisted audit trail for platform-admin cross-tenant actions (Sprint 31, ADR-032).

Deliberately a standalone module, not an addition to `audit/db.py` — Sprint
31's isolation note (running in parallel with other sprints touching that
file) plus a real distinction in kind: `PipelineState.audit_log`
(`audit/logger.py`) is per-*run*, ephemeral pipeline-execution history;
`admin_action_log` (this module) is a permanent, cross-run record of a
*person* (a platform admin, resolved via `AI_ETL_PLATFORM_ADMINS`, never a
regular tenant) reaching outside their own tenant boundary. Every
`require_admin()`-gated route must call `log_admin_action()` before
returning — see `api/routers/admin.py`.

Never logs a decrypted secret or credential value — same non-negotiable
rule as `audit/logger.py`'s automatic key/token/secret redaction and
`services/secrets_service.py`'s logging discipline. `detail` is a short,
human-readable string (e.g. a target run id, a query filter) chosen by the
caller, not a dump of the accessed record.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import desc, insert, select
from typing_extensions import TypedDict

from ai_etl.audit.connection import get_engine
from ai_etl.audit.models import admin_action_log


class AdminActionRecord(TypedDict):
    id: str
    actor_user_id: str
    action: str
    target_tenant_id: Optional[str]
    detail: Optional[str]
    created_at: datetime


def log_admin_action(
    actor_user_id: str,
    action: str,
    *,
    target_tenant_id: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    """Persist one platform-admin action. Called by every `require_admin()`
    route handler (`api/routers/admin.py`) — never optional, never batched,
    so a crash between "action performed" and "action logged" is the only
    gap (accepted: this mirrors every other `log_action()` call site in the
    codebase, none of which are transactional with the action they record).

    Args:
        actor_user_id: The admin's individual Clerk user id (`AuthContext["user_id"]`
            from `api/deps.require_admin`), not a tenant id — an admin acting
            cross-tenant has no single "own tenant" that makes sense here.
        action: Short machine-readable action name, e.g. `"list_tenant_runs"`,
            `"view_tenant_budget"`. Not free text — keep this a small, stable
            vocabulary so `list_admin_actions()` stays queryable/filterable.
        target_tenant_id: The tenant whose data was accessed, if any single
            one applies.
        detail: Optional short human-readable context (e.g. a run id). Must
            never contain a decrypted secret/credential value.
    """
    stmt = insert(admin_action_log).values(
        id=str(uuid.uuid4()),
        actor_user_id=actor_user_id,
        action=action,
        target_tenant_id=target_tenant_id,
        detail=detail,
        created_at=datetime.now(tz=timezone.utc),
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)


def list_admin_actions(
    limit: int = 100,
    actor_user_id: Optional[str] = None,
    target_tenant_id: Optional[str] = None,
) -> list[AdminActionRecord]:
    """Return the most recent admin actions, most recent first.

    Used by `GET /admin/audit-log` (itself `require_admin()`-gated and
    logged as its own `"view_admin_audit_log"` action) — the DoD's
    "consultável" (queryable) requirement for admin access.
    """
    stmt = select(admin_action_log).order_by(desc(admin_action_log.c.created_at)).limit(limit)
    if actor_user_id is not None:
        stmt = stmt.where(admin_action_log.c.actor_user_id == actor_user_id)
    if target_tenant_id is not None:
        stmt = stmt.where(admin_action_log.c.target_tenant_id == target_tenant_id)

    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [
        AdminActionRecord(
            id=row["id"],
            actor_user_id=row["actor_user_id"],
            action=row["action"],
            target_tenant_id=row["target_tenant_id"],
            detail=row["detail"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
