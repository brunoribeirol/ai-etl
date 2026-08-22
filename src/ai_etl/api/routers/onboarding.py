"""`/onboarding` endpoints — self-serve activation checklist (Sprint 26,
ADR-027).

Read-only, same trust model as `/budget`: a tenant can only ever read its
own activation status. No new schema — every field is an aggregate over
`runs`/`saved_pipelines`, computed on demand by
`audit.db.get_onboarding_status`.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ai_etl.api.deps import get_current_tenant_id
from ai_etl.audit.db import get_onboarding_status

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/status")
def onboarding_status(
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> dict[str, Any]:
    """The `/comecar` guided-flow page's activation checklist: has this
    tenant completed a first pipeline run, and created a scheduled
    (saved) pipeline yet."""
    return get_onboarding_status(tenant_id)
