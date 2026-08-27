"""Tenant monthly budget cap (Sprint 29, ADR-019).

Split out of the former monolithic `audit/db.py` (Sprint 33) — see
`audit/db/__init__.py` for the full split rationale.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select, update

from ai_etl.audit.connection import get_engine, tenant_scope
from ai_etl.audit.models import analysis_runs, users


def get_monthly_budget(tenant_id: str) -> float | None:
    """Return the tenant's configured `monthly_budget_usd`, or `None` if the
    tenant has no cap set (default for every tenant, ADR-019) or does not
    exist yet (treated the same as "no cap" — `check_budget_cap` should never
    block a run over a tenant row that simply hasn't been `ensure_user()`-ed
    yet; that would be a different bug, not a budget one)."""
    stmt = select(users.c.monthly_budget_usd).where(users.c.id == tenant_id)
    with tenant_scope(tenant_id) as conn:
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
    with tenant_scope(tenant_id) as conn:
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
    with tenant_scope(tenant_id) as conn:
        result = conn.execute(stmt).scalar()
    return float(result) if result is not None else 0.0


# --- Sprint 35 (FinOps: pre-run cost estimation) ---------------------------
# Appended, not interleaved with the Sprint 29 functions above, per this
# sprint's isolation rules (this file is shared with other in-flight
# sprints — additions only, never a rewrite of an existing function).


def get_avg_run_cost_usd(tenant_id: str, limit: int = 20) -> float | None:
    """Average `analysis_runs.cost_usd` over this tenant's most recent
    `limit` *priced* runs (Sprint 35) — `None` if the tenant has zero priced
    runs yet (a brand-new tenant, or a run history made entirely of unpriced
    models; see `core.pricing.compute_cost_usd`'s docstring for why
    `cost_usd` is nullable).

    Distinct from `get_monthly_spend_usd` above: that one sums a fixed
    calendar-month window for budget *enforcement*; this one averages the
    tenant's most recent runs regardless of month, as a per-run cost signal
    for `services/cost_estimation.py::estimate_run_cost`."""
    subq = (
        select(analysis_runs.c.cost_usd)
        .where(analysis_runs.c.tenant_id == tenant_id, analysis_runs.c.cost_usd.is_not(None))
        .order_by(analysis_runs.c.timestamp.desc())
        .limit(limit)
        .subquery()
    )
    stmt = select(func.avg(subq.c.cost_usd))
    with tenant_scope(tenant_id) as conn:
        result = conn.execute(stmt).scalar()
    return float(result) if result is not None else None


def get_global_avg_run_cost_usd(limit: int = 200) -> float | None:
    """Same as `get_avg_run_cost_usd` above, but across every tenant's most
    recent `limit` priced runs combined — the fallback
    `services/cost_estimation.py::estimate_run_cost` uses for a tenant with
    zero run history of its own (a brand-new tenant has no per-tenant signal
    to estimate from otherwise). `None` only for a deployment with zero
    priced runs across every tenant (e.g. right after a fresh install)."""
    subq = (
        select(analysis_runs.c.cost_usd)
        .where(analysis_runs.c.cost_usd.is_not(None))
        .order_by(analysis_runs.c.timestamp.desc())
        .limit(limit)
        .subquery()
    )
    stmt = select(func.avg(subq.c.cost_usd))
    with get_engine().connect() as conn:
        result = conn.execute(stmt).scalar()
    return float(result) if result is not None else None
