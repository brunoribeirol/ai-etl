"""`/admin` endpoints: platform-admin, cross-tenant access (Sprint 31, ADR-032;
mediated write access, ADR-047).

Every route here is `require_admin()`-gated (platform `admin` role only,
resolved via `AI_ETL_PLATFORM_ADMINS`: see `api/deps.py`) and every route
writes exactly one `log_admin_action()` entry (`audit/admin_log.py`) before
returning, success or not: this router is the DoD's "acesso admin
cross-tenant fica registrado de forma auditável e consultável" made
concrete.

Every route also requires a `reason`: a short, free-text justification for
why this specific action is being taken (ADR-047). This is deliberately not
optional: it is the single cheapest change that turns "an admin can read/act
on tenant data" into "an admin must state why," which is most of what a
SOC2 auditor is actually checking for under CC5. Read routes take it as a
required query parameter; the one write route below takes it in the request
body.

`retry_tenant_pipeline` (ADR-047) is this router's first *write* route:
Sprint 31/ADR-032 Decision 2 deliberately scoped this router to read-only
and flagged a future write path as explicit future work, not an omission.
It reuses `services/execution_queue.enqueue_analysis()` verbatim: the same
function `services/scheduler.py`'s own scheduled fires call: so an
admin-triggered retry goes through the application's normal budget/rate-limit
gates exactly like any other run, never a bypass.
"""

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ai_etl.api.deps import AuthContext, require_admin
from ai_etl.api.serialization import nan_to_none_records
from ai_etl.audit.admin_log import AdminActionRecord, list_admin_actions, log_admin_action
from ai_etl.audit.db import TenantSummary, list_all_tenants, load_history
from ai_etl.audit.db.pipelines import get_saved_pipeline
from ai_etl.core.paths import RUNS_DIR
from ai_etl.services.execution_queue import (
    BudgetExceededError,
    BudgetStatus,
    RateLimitExceededError,
    enqueue_analysis,
    get_budget_status,
)

router = APIRouter(prefix="/admin", tags=["admin"])

_ReasonQuery = Annotated[
    str, Query(min_length=1, description="Why this action is being taken, for the audit log.")
]


@router.get("/tenants")
def admin_list_tenants(
    auth: Annotated[AuthContext, Depends(require_admin)],
    reason: _ReasonQuery,
) -> list[TenantSummary]:
    """The tenant directory itself: Wave 6 (2026-08-25 admin panel/approval-
    gate UI plan) gap-closing addition. The 3 routes below all require a
    `tenant_id` the caller already knows; there was previously no way to
    discover one at all, so the frontend admin panel had no real picker."""
    tenants = list_all_tenants()
    log_admin_action(auth["user_id"], "list_tenants", reason=reason, detail=f"count={len(tenants)}")
    return tenants


@router.get("/tenants/{tenant_id}/runs")
def admin_list_tenant_runs(
    tenant_id: str,
    auth: Annotated[AuthContext, Depends(require_admin)],
    reason: _ReasonQuery,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Cross-tenant equivalent of `GET /runs`: the run history of any
    tenant, not just the caller's own. `require_admin()` (not `require_role`)
    means only a platform admin can reach this; a tenant's own `editor` role
    cannot escalate here."""
    history = load_history(limit=limit, tenant_id=tenant_id)
    records = cast("list[dict[str, Any]]", history.to_dict(orient="records"))
    log_admin_action(
        auth["user_id"],
        "list_tenant_runs",
        target_tenant_id=tenant_id,
        reason=reason,
        detail=f"limit={limit}",
    )
    return nan_to_none_records(records)


@router.get("/tenants/{tenant_id}/budget")
def admin_get_tenant_budget(
    tenant_id: str,
    auth: Annotated[AuthContext, Depends(require_admin)],
    reason: _ReasonQuery,
) -> BudgetStatus:
    """Cross-tenant equivalent of `GET /budget`: support/billing triage
    without the tenant needing to share a screenshot."""
    status = get_budget_status(tenant_id)
    log_admin_action(
        auth["user_id"], "view_tenant_budget", target_tenant_id=tenant_id, reason=reason
    )
    return status


class RetryTenantPipelineRequest(BaseModel):
    """`reason` (ADR-047): required justification, e.g. "tenant emailed
    support, run stuck queued for 2h": logged verbatim in
    `admin_action_log.reason`, never truncated or summarized.
    """

    reason: str = Field(min_length=1)


@router.post("/tenants/{tenant_id}/pipelines/{pipeline_id}/retry")
def admin_retry_tenant_pipeline(
    tenant_id: str,
    pipeline_id: str,
    body: RetryTenantPipelineRequest,
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict[str, str]:
    """Admin-mediated retry of a tenant's saved pipeline (ADR-047): the
    support scenario the SOC2 self-assessment's own top finding described
    ("direct DB access by Bruno, not mediated by the application, not
    separately logged") now has an application-mediated, reason-required,
    audit-logged path instead. Reuses `enqueue_analysis()` verbatim, the
    same function `services/scheduler.py`'s scheduled fires call, so this
    goes through the tenant's normal budget cap and rate limit exactly like
    any other run: an admin retry is not a bypass of either.
    """
    pipeline = get_saved_pipeline(pipeline_id, tenant_id)
    if pipeline is None:
        log_admin_action(
            auth["user_id"],
            "retry_pipeline_not_found",
            target_tenant_id=tenant_id,
            reason=body.reason,
            detail=f"pipeline_id={pipeline_id}",
        )
        raise HTTPException(status_code=404, detail="Saved pipeline not found.")

    try:
        task_id = enqueue_analysis(
            pipeline["spec"],
            pipeline["business_question"],
            run_dir=str(RUNS_DIR),
            tenant_id=tenant_id,
            saved_pipeline_id=pipeline_id,
        )
    except (RateLimitExceededError, BudgetExceededError) as e:
        log_admin_action(
            auth["user_id"],
            "retry_pipeline_rejected",
            target_tenant_id=tenant_id,
            reason=body.reason,
            detail=f"pipeline_id={pipeline_id}, error={e}",
        )
        status_code = 429 if isinstance(e, RateLimitExceededError) else 402
        raise HTTPException(status_code=status_code, detail=str(e)) from e

    log_admin_action(
        auth["user_id"],
        "retry_pipeline",
        target_tenant_id=tenant_id,
        reason=body.reason,
        detail=f"pipeline_id={pipeline_id}, task_id={task_id}",
    )
    return {"task_id": task_id}


@router.get("/audit-log")
def admin_get_audit_log(
    auth: Annotated[AuthContext, Depends(require_admin)],
    reason: _ReasonQuery,
    limit: int = 100,
    actor_user_id: str | None = None,
    target_tenant_id: str | None = None,
) -> list[AdminActionRecord]:
    """The admin audit trail itself: 'consultável' (DoD). Reading the audit
    log is itself an admin action and is itself logged, same as every other
    route in this router (recursion bottoms out fine: this call's own log
    entry is written *after* the read below, so it never appears in its own
    result)."""
    records = list_admin_actions(
        limit=limit, actor_user_id=actor_user_id, target_tenant_id=target_tenant_id
    )
    log_admin_action(
        auth["user_id"],
        "view_admin_audit_log",
        target_tenant_id=target_tenant_id,
        reason=reason,
        detail=f"limit={limit}, actor_user_id={actor_user_id}",
    )
    return records
