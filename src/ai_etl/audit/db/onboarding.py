"""Self-serve activation checklist (Sprint 26, ADR-027).

Split out of the former monolithic `audit/db.py` (Sprint 33) — see
`audit/db/__init__.py` for the full split rationale.
"""

from typing import Any

from sqlalchemy import func, select

from ai_etl.audit.connection import tenant_scope
from ai_etl.audit.models import runs, saved_pipelines


def get_onboarding_status(tenant_id: str) -> dict[str, Any]:
    """Sprint 26 (ADR-027) — self-serve activation checklist, derived on read
    from `runs`/`saved_pipelines`. No new column/table: "has this tenant
    completed a first pipeline" is `COUNT(runs) WHERE tenant_id = :t AND
    status = 'completed'`, the same "derive from existing tables" pattern
    Sprint 15's `get_pipeline_health` and Sprint 29's `get_monthly_spend_usd`
    already use.

    `has_completed_run` covers any completed run (avulso or scheduled) —
    the guided flow's own example-dataset run is an avulso upload, same as
    any other `POST /runs`. `has_saved_pipeline` is a separate, later step
    (ADR-016 Decision 3: only live sources are schedulable, so the guided
    flow's own upload can never satisfy this one directly)."""
    run_count_stmt = select(func.count()).where(runs.c.tenant_id == tenant_id)
    completed_run_count_stmt = select(func.count()).where(
        runs.c.tenant_id == tenant_id, runs.c.status == "completed"
    )
    saved_pipeline_count_stmt = select(func.count()).where(saved_pipelines.c.tenant_id == tenant_id)
    with tenant_scope(tenant_id) as conn:
        run_count = conn.execute(run_count_stmt).scalar() or 0
        completed_run_count = conn.execute(completed_run_count_stmt).scalar() or 0
        saved_pipeline_count = conn.execute(saved_pipeline_count_stmt).scalar() or 0

    return {
        "run_count": int(run_count),
        "completed_run_count": int(completed_run_count),
        "has_completed_run": completed_run_count > 0,
        "saved_pipeline_count": int(saved_pipeline_count),
        "has_saved_pipeline": saved_pipeline_count > 0,
    }
