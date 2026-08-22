"""Tenant monthly budget cap (Sprint 29, ADR-019).

Split out of the former monolithic `audit/db.py` (Sprint 33) — see
`audit/db/__init__.py` for the full split rationale.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select, update

from ai_etl.audit.connection import get_engine
from ai_etl.audit.models import analysis_runs, users


def get_monthly_budget(tenant_id: str) -> float | None:
    """Return the tenant's configured `monthly_budget_usd`, or `None` if the
    tenant has no cap set (default for every tenant, ADR-019) or does not
    exist yet (treated the same as "no cap" — `check_budget_cap` should never
    block a run over a tenant row that simply hasn't been `ensure_user()`-ed
    yet; that would be a different bug, not a budget one)."""
    stmt = select(users.c.monthly_budget_usd).where(users.c.id == tenant_id)
    with get_engine().connect() as conn:
        row = conn.execute(stmt).first()
    return row[0] if row is not None else None


def set_monthly_budget(tenant_id: str, monthly_budget_usd: float | None) -> None:
    """Set (or clear, passing `None`) the tenant's monthly budget cap.

    Self-service — callable by the tenant themselves via `PATCH /budget`,
    same trust model as every other tenant-owned setting in this project
    (e.g. `saved_pipelines`). There is no separate admin/billing role in this
    codebase yet (ADR-019 flags this as a known limitation for a real
    enterprise deployment)."""
    stmt = (
        update(users).where(users.c.id == tenant_id).values(monthly_budget_usd=monthly_budget_usd)
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)


def get_monthly_spend_usd(tenant_id: str) -> float:
    """Sum of `analysis_runs.cost_usd` for `tenant_id` in the current
    calendar month (UTC), the canonical, already-persisted per-run cost
    Sprint 3 (ADR-008) computes — see ADR-019 for why budget enforcement
    reads this directly instead of maintaining a second, Redis-resident
    running total that could drift from it.

    `COALESCE(..., 0)` — a tenant with zero runs this month has spent $0.00,
    not `NULL` (which would make every comparison against a cap fail
    ambiguously)."""
    month_start = datetime.now(tz=timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    stmt = select(func.coalesce(func.sum(analysis_runs.c.cost_usd), 0.0)).where(
        analysis_runs.c.tenant_id == tenant_id,
        analysis_runs.c.timestamp >= month_start,
    )
    with get_engine().connect() as conn:
        result = conn.execute(stmt).scalar()
    return float(result) if result is not None else 0.0
