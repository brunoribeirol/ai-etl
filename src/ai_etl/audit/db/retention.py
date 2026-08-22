"""Per-tenant automatic run-retention policy (Sprint 36, ADR-035).

Mirrors `audit/db/budget.py`'s shape exactly (a single nullable scalar on
`users`, no history/versioning table needed — see ADR-035 for why this
follows ADR-019's precedent) but lives in its own module rather than being
added to `budget.py`, which is out of scope for this sprint (Sprint 35
territory, running in parallel in another worktree).
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select, update
from typing_extensions import TypedDict

from ai_etl.audit.connection import get_engine
from ai_etl.audit.models import users


class RetentionPolicy(TypedDict):
    tenant_id: str
    retention_days: Optional[int]


def get_retention_days(tenant_id: str) -> Optional[int]:
    """Return the tenant's configured `retention_days`, or `None` if the
    tenant has no automatic-retention policy set (default for every tenant)
    or does not exist yet — same "treat missing as unset" contract as
    `get_monthly_budget`."""
    stmt = select(users.c.retention_days).where(users.c.id == tenant_id)
    with get_engine().connect() as conn:
        row = conn.execute(stmt).first()
    return row[0] if row is not None else None


def list_tenants_with_retention() -> list[RetentionPolicy]:
    """Every tenant with a configured automatic-retention window
    (`retention_days IS NOT NULL`), across all tenants — the query
    `services/retention_service.py`'s Celery beat task runs on each tick,
    same "no single-tenant filter, the beat task isn't acting on behalf of
    any one tenant" shape as `list_due_pipelines`
    (`audit/db/pipelines.py`)."""
    stmt = select(users.c.id, users.c.retention_days).where(users.c.retention_days.isnot(None))
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [RetentionPolicy(tenant_id=row.id, retention_days=row.retention_days) for row in rows]


def set_retention_days(tenant_id: str, retention_days: Optional[int]) -> None:
    """Set (or clear, passing `None`) the tenant's automatic run-artifact
    retention window, in days. Self-service — callable by the tenant
    themselves via `PATCH /tenant/retention`, `editor`-gated (same trust
    model as `set_monthly_budget`). Validation (`retention_days >= 1`) is
    enforced at the API layer (`Field(ge=1)`), not here — this function
    trusts its caller, same as `set_monthly_budget`."""
    stmt = update(users).where(users.c.id == tenant_id).values(retention_days=retention_days)
    with get_engine().begin() as conn:
        conn.execute(stmt)
