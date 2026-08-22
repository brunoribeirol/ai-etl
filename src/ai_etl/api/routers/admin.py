"""`/admin` endpoints — platform-admin, cross-tenant access (Sprint 31, ADR-032).

Every route here is `require_admin()`-gated (platform `admin` role only,
resolved via `AI_ETL_PLATFORM_ADMINS` — see `api/deps.py`) and every route
writes exactly one `log_admin_action()` entry (`audit/admin_log.py`) before
returning, success or not — this router is the DoD's "acesso admin
cross-tenant fica registrado de forma auditável e consultável" made
concrete. No route here ever accepts write operations against another
tenant's data this sprint (read-only cross-tenant access) — see ADR-032
Decision 2 for why that scope was chosen deliberately, not by omission.
"""

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends

from ai_etl.api.deps import AuthContext, require_admin
from ai_etl.api.serialization import nan_to_none_records
from ai_etl.audit.admin_log import AdminActionRecord, list_admin_actions, log_admin_action
from ai_etl.audit.db import load_history
from ai_etl.services.execution_queue import BudgetStatus, get_budget_status

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/tenants/{tenant_id}/runs")
def admin_list_tenant_runs(
    tenant_id: str,
    auth: Annotated[AuthContext, Depends(require_admin)],
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Cross-tenant equivalent of `GET /runs` — the run history of any
    tenant, not just the caller's own. `require_admin()` (not `require_role`)
    means only a platform admin can reach this; a tenant's own `editor` role
    cannot escalate here."""
    history = load_history(limit=limit, tenant_id=tenant_id)
    records = cast("list[dict[str, Any]]", history.to_dict(orient="records"))
    log_admin_action(
        auth["user_id"],
        "list_tenant_runs",
        target_tenant_id=tenant_id,
        detail=f"limit={limit}",
    )
    return nan_to_none_records(records)


@router.get("/tenants/{tenant_id}/budget")
def admin_get_tenant_budget(
    tenant_id: str,
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> BudgetStatus:
    """Cross-tenant equivalent of `GET /budget` — support/billing triage
    without the tenant needing to share a screenshot."""
    status = get_budget_status(tenant_id)
    log_admin_action(auth["user_id"], "view_tenant_budget", target_tenant_id=tenant_id)
    return status


@router.get("/audit-log")
def admin_get_audit_log(
    auth: Annotated[AuthContext, Depends(require_admin)],
    limit: int = 100,
    actor_user_id: str | None = None,
    target_tenant_id: str | None = None,
) -> list[AdminActionRecord]:
    """The admin audit trail itself — 'consultável' (DoD). Reading the audit
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
        detail=f"limit={limit}, actor_user_id={actor_user_id}",
    )
    return records
