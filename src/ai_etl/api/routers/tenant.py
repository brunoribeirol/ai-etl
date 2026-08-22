"""`/tenant` endpoints — self-service tenant data erasure (Sprint 24, ADR-025).

`editor`-only, same trust boundary as `PATCH /budget` and
`POST/DELETE /secrets`: a tenant can only ever delete its own data, resolved
from the verified JWT (`require_role`), never an arbitrary `tenant_id` —
there is no admin role able to target another tenant (ADR-022's still-open
limitation, deliberately not resolved here, see ADR-025 Decision 1).
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ai_etl.api.deps import require_role
from ai_etl.services.tenant_deletion_service import (
    TenantDeletionSummary,
    TenantNotFoundError,
    delete_tenant_data,
)

router = APIRouter(prefix="/tenant", tags=["tenant"])


class TenantDeletionRequest(BaseModel):
    # Must be typed exactly, mirroring the "type the resource name to
    # confirm" pattern for irreversible actions (ADR-025 Decision 4).
    confirm: Literal["DELETE"]


@router.delete("")
def delete_tenant(
    body: TenantDeletionRequest,
    tenant_id: Annotated[str, Depends(require_role("editor"))],
) -> TenantDeletionSummary:
    try:
        return delete_tenant_data(tenant_id, requested_by=tenant_id)
    except TenantNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Tenant not found.") from exc
