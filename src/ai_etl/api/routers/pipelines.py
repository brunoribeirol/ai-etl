"""`/pipelines` endpoints — CRUD for saved (recurring) pipelines (Sprint 13,
ADR-016). Distinct from `/runs` (avulso, one-off execution), which is
unchanged. Same auth pattern as `routers/runs.py`: `get_current_tenant_id`.

Actual firing happens in `services/scheduler.py`'s Celery beat task, not
here — this router only manages the `saved_pipelines` row (create/list/get/
patch); it never itself enqueues a run.
"""

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ai_etl.api.deps import get_current_tenant_id
from ai_etl.audit.db import (
    create_saved_pipeline,
    get_saved_pipeline,
    list_pipeline_run_history,
    list_saved_pipelines,
    update_saved_pipeline,
)
from ai_etl.core.scheduling import (
    SCHEDULABLE_SOURCE_TYPES,
    InvalidCronScheduleError,
    validate_cron_schedule,
)

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


def _validate_source_type(source_type: str) -> None:
    if source_type not in SCHEDULABLE_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"source_type {source_type!r} cannot be scheduled. Only live sources "
                f"(re-resolvable without a re-upload) support recurring execution: "
                f"{sorted(SCHEDULABLE_SOURCE_TYPES)}. See ADR-016."
            ),
        )


class CreatePipelineRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_type: str
    spec: str = Field(min_length=1)
    cron_schedule: str
    business_question: str = ""


class UpdatePipelineRequest(BaseModel):
    """All fields optional — PATCH semantics, only provided fields change."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    source_type: Optional[str] = None
    spec: Optional[str] = Field(default=None, min_length=1)
    cron_schedule: Optional[str] = None
    business_question: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("")
def list_pipelines(
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> list[dict[str, Any]]:
    return list_saved_pipelines(tenant_id)


@router.get("/{pipeline_id}")
def get_pipeline(
    pipeline_id: str,
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> dict[str, Any]:
    pipeline = get_saved_pipeline(pipeline_id, tenant_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="Saved pipeline not found.")
    return pipeline


@router.post("")
def create_pipeline(
    body: CreatePipelineRequest,
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> dict[str, Any]:
    _validate_source_type(body.source_type)
    try:
        validate_cron_schedule(body.cron_schedule)
    except InvalidCronScheduleError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return create_saved_pipeline(
        tenant_id=tenant_id,
        name=body.name,
        source_type=body.source_type,
        spec=body.spec,
        cron_schedule=body.cron_schedule,
        business_question=body.business_question,
    )


@router.get("/{pipeline_id}/history")
def get_pipeline_history(
    pipeline_id: str,
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> list[dict[str, Any]]:
    """Sprint 17 (ADR-017) — time series of every execution of one saved
    pipeline (oldest first), for the "Histórico comparável" view: KPI trend
    charts and the two-run diff. 404s (rather than returning an empty list)
    when the pipeline itself doesn't exist or isn't owned by this tenant —
    same "unknown vs. empty" distinction `get_pipeline` already makes, so the
    frontend can tell "no pipeline" from "pipeline with no runs yet" apart.
    """
    if get_saved_pipeline(pipeline_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Saved pipeline not found.")
    return list_pipeline_run_history(pipeline_id, tenant_id)


@router.patch("/{pipeline_id}")
def patch_pipeline(
    pipeline_id: str,
    body: UpdatePipelineRequest,
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> dict[str, Any]:
    if body.source_type is not None:
        _validate_source_type(body.source_type)
    if body.cron_schedule is not None:
        try:
            validate_cron_schedule(body.cron_schedule)
        except InvalidCronScheduleError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    updated = update_saved_pipeline(
        pipeline_id,
        tenant_id,
        name=body.name,
        source_type=body.source_type,
        spec=body.spec,
        cron_schedule=body.cron_schedule,
        business_question=body.business_question,
        is_active=body.is_active,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Saved pipeline not found.")
    return updated
