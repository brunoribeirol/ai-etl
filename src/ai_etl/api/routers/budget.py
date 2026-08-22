"""`/budget` endpoints — per-tenant monthly budget cap (Sprint 29, ADR-019).

Self-service, same trust model as `/pipelines`: a tenant can only read or
set their own cap. `PATCH` requires the `editor` role (Sprint 19, ADR-021)
— partially closes the "no separate admin/billing role" limitation ADR-019
flagged; a dedicated billing role distinct from `editor` remains future
work, this sprint only stops a `viewer` from changing the cap.

`GET /budget` is also how a frontend would surface the "near the cap" alert
`services/execution_queue.py::check_budget_cap` computes on every enqueue —
this endpoint recomputes the same `BudgetStatus` on demand (via the
non-raising `get_budget_status`), independent of whether a run is currently
being enqueued, so a dashboard can poll it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ai_etl.api.deps import get_current_tenant_id, require_role
from ai_etl.audit.db import set_monthly_budget
from ai_etl.services.execution_queue import BudgetStatus, get_budget_status

router = APIRouter(prefix="/budget", tags=["budget"])


class SetBudgetRequest(BaseModel):
    # None explicitly clears the cap (back to "unlimited") — Optional with no
    # default so a caller must pass the key, not omit it, to avoid an
    # ambiguous "did they mean null or did they forget the field" PATCH body.
    monthly_budget_usd: float | None = Field(ge=0)


@router.get("")
def get_budget(tenant_id: Annotated[str, Depends(get_current_tenant_id)]) -> BudgetStatus:
    """Current cap, spend-so-far this calendar month, and whether the tenant
    is near or over the cap. Read-only — never blocks; only `POST /runs`
    (via `enqueue_analysis`) actually enforces the cap."""
    return get_budget_status(tenant_id)


@router.patch("")
def patch_budget(
    body: SetBudgetRequest,
    tenant_id: Annotated[str, Depends(require_role("editor"))],
) -> BudgetStatus:
    set_monthly_budget(tenant_id, body.monthly_budget_usd)
    return get_budget_status(tenant_id)
