"""`/tenant` endpoints — self-service tenant data erasure, export, and
retention (Sprint 24 ADR-025, Sprint 36 ADR-035).

`editor`-only for anything that mutates/deletes (`DELETE /tenant`,
`PATCH /tenant/retention`), same trust boundary as `PATCH /budget` and
`POST/DELETE /secrets`: a tenant can only ever act on its own data, resolved
from the verified JWT (`require_role`/`get_current_tenant_id`), never an
arbitrary `tenant_id` — there is no admin role able to target another
tenant (ADR-022's still-open limitation, deliberately not resolved here, see
ADR-025 Decision 1). `GET /tenant/export` and `GET /tenant/retention` are
read-only and available to `viewer` too, same access-right tier as
`GET /budget`/`GET /pipelines`.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ai_etl.api.deps import get_current_tenant_id, require_role
from ai_etl.audit.db.retention import RetentionPolicy, get_retention_days, set_retention_days
from ai_etl.services.tenant_deletion_service import (
    TenantDeletionSummary,
    TenantNotFoundError,
    delete_tenant_data,
)
from ai_etl.services.tenant_export_service import TenantExport, export_tenant_data

router = APIRouter(prefix="/tenant", tags=["tenant"])


class TenantDeletionRequest(BaseModel):
    # Must be typed exactly, mirroring the "type the resource name to
    # confirm" pattern for irreversible actions (ADR-025 Decision 4).
    confirm: Literal["DELETE"]


class SetRetentionRequest(BaseModel):
    # None explicitly clears the policy (back to "keep forever") — Optional
    # with no default so a caller must pass the key, not omit it, mirroring
    # `SetBudgetRequest` (api/routers/budget.py).
    retention_days: int | None = Field(ge=1)


@router.delete("")
def delete_tenant(
    body: TenantDeletionRequest,
    tenant_id: Annotated[str, Depends(require_role("editor"))],
) -> TenantDeletionSummary:
    try:
        return delete_tenant_data(tenant_id, requested_by=tenant_id)
    except TenantNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Tenant not found.") from exc


@router.get("/export")
def export_tenant(
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> TenantExport:
    """LGPD Art. 9/18 II, GDPR Art. 15/20 — a tenant's full self-service data
    export (ADR-035). Never cross-tenant: always the caller's own resolved
    `tenant_id`, same as `DELETE /tenant`."""
    try:
        return export_tenant_data(tenant_id)
    except TenantNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Tenant not found.") from exc


@router.get("/retention")
def get_retention(
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> RetentionPolicy:
    """Current automatic run-artifact retention window, if any (ADR-035).
    `retention_days=None` (the default for every tenant) means "keep
    forever" — zero behavior change unless a tenant opts in."""
    return RetentionPolicy(tenant_id=tenant_id, retention_days=get_retention_days(tenant_id))


@router.patch("/retention")
def patch_retention(
    body: SetRetentionRequest,
    tenant_id: Annotated[str, Depends(require_role("editor"))],
) -> RetentionPolicy:
    set_retention_days(tenant_id, body.retention_days)
    return RetentionPolicy(tenant_id=tenant_id, retention_days=body.retention_days)
