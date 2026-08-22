"""`/pipelines` endpoints — CRUD for saved (recurring) pipelines (Sprint 13,
ADR-016). Distinct from `/runs` (avulso, one-off execution), which is
unchanged. Same auth pattern as `routers/runs.py`: `get_current_tenant_id`.

Actual firing happens in `services/scheduler.py`'s Celery beat task, not
here — this router only manages the `saved_pipelines` row (create/list/get/
patch); it never itself enqueues a run.
"""

from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from ai_etl.api.deps import get_current_tenant_id, require_role
from ai_etl.audit.db import (
    DEFAULT_PIPELINE_HISTORY_LIMIT,
    create_saved_pipeline,
    get_pipeline_health,
    get_saved_pipeline,
    get_saved_pipeline_llm_config,
    list_pipeline_run_history,
    list_saved_pipelines,
    set_saved_pipeline_llm_config,
    update_saved_pipeline,
)
from ai_etl.core.llm import (
    ALLOWED_MODELS_BY_PROVIDER,
    UnsupportedProviderOrModelError,
    validate_provider_and_model,
)
from ai_etl.core.scheduling import (
    SCHEDULABLE_SOURCE_TYPES,
    InvalidCronScheduleError,
    validate_cron_schedule,
)

router = APIRouter(prefix="/pipelines", tags=["pipelines"])

# Sprint 16 (ADR-023) — mirrors `agents/pipeline/quality.py::_CUSTOM_RULE_OPERATORS`'s keys
# exactly; kept as a literal here (not imported) so the API's request-validation
# error message names them without importing an `agents/` module into `api/`.
QualityRuleOperator = Literal["not_null", "gte", "lte", "gt", "lt", "eq", "ne"]


class QualityRule(BaseModel):
    """One operator-defined data quality rule (Sprint 16, ADR-023) — a whitelisted
    declarative check, never an arbitrary expression or code (see the ADR's Decision 1
    for why: a rule is persisted and re-run unattended on every future scheduled fire).
    """

    column: str = Field(min_length=1)
    operator: QualityRuleOperator
    value: Optional[Any] = None
    severity: Literal["ok", "warning", "error"] = "error"
    name: Optional[str] = None

    @model_validator(mode="after")
    def _value_required_unless_not_null(self) -> "QualityRule":
        if self.operator != "not_null" and self.value is None:
            raise ValueError(f"operator {self.operator!r} requires a non-null 'value'.")
        return self


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
    # Sprint 14 (ADR-018) — "% change on a tracked KPI worth alerting on"
    # for this pipeline's own recurring drift check. Default matches
    # audit/models.py's server_default, so a client that omits it gets the
    # same behavior as a row written before this field existed.
    drift_threshold_pct: float = Field(default=20.0, gt=0)
    # Sprint 16 (ADR-023) — operator-defined quality rules, run on every subsequent
    # execution of this saved pipeline alongside the fixed checks. Defaults to no
    # custom rules, same behavior as every pipeline created before this sprint.
    quality_rules: list[QualityRule] = Field(default_factory=list)
    # Sprint 27 (ADR-028) — opt-in write-approval gate. `False` by default: zero
    # behavior change unless explicitly turned on. See `agents/pipeline/loader.py`.
    require_approval: bool = False
    approval_threshold_rows: Optional[int] = Field(default=None, ge=0)


class UpdatePipelineRequest(BaseModel):
    """All fields optional — PATCH semantics, only provided fields change.

    `quality_rules`, if provided, REPLACES the pipeline's whole rule set (not a
    per-rule merge) — the same "provide the field, it wins whole" PATCH semantics
    every other field here already has; `None` (the default, field omitted) leaves
    the existing rules untouched.
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    source_type: Optional[str] = None
    spec: Optional[str] = Field(default=None, min_length=1)
    cron_schedule: Optional[str] = None
    business_question: Optional[str] = None
    is_active: Optional[bool] = None
    drift_threshold_pct: Optional[float] = Field(default=None, gt=0)
    quality_rules: Optional[list[QualityRule]] = None
    require_approval: Optional[bool] = None
    approval_threshold_rows: Optional[int] = Field(default=None, ge=0)


def _with_health(pipeline: dict[str, Any]) -> dict[str, Any]:
    """Sprint 15 (ADR-020 Decision 4) — merge `success_rate`/
    `avg_latency_seconds`/`health_sample_size` onto a saved-pipeline dict,
    additive fields alongside the `consecutive_failures`/`last_status`/
    `last_error` cache `_saved_pipeline_row_to_dict` already includes.
    `consecutive_failures`/`last_status`/`last_error` are cheap (already on
    the row); `success_rate`/`avg_latency_seconds` cost one extra aggregate
    query per pipeline (`audit.db.get_pipeline_health`) — accepted at this
    project's current scale, same posture ADR-020 documents.
    """
    health = get_pipeline_health(pipeline["id"], pipeline["tenant_id"])
    # Sprint 30 (ADR-031) — merge the LLM provider/model override the same way
    # `success_rate`/etc. above are merged: `_saved_pipeline_row_to_dict`
    # (`audit/db.py`) intentionally wasn't touched to add these two columns (see
    # `get_saved_pipeline_llm_config`'s docstring for why), so this router composes
    # them on read instead.
    llm_config = get_saved_pipeline_llm_config(pipeline["id"], pipeline["tenant_id"]) or {
        "llm_provider": None,
        "llm_model": None,
    }
    return {
        **pipeline,
        "success_rate": health["success_rate"],
        "avg_latency_seconds": health["avg_latency_seconds"],
        "health_sample_size": health["sample_size"],
        "llm_provider": llm_config["llm_provider"],
        "llm_model": llm_config["llm_model"],
    }


@router.get("")
def list_pipelines(
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> list[dict[str, Any]]:
    return [_with_health(p) for p in list_saved_pipelines(tenant_id)]


@router.get("/{pipeline_id}")
def get_pipeline(
    pipeline_id: str,
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> dict[str, Any]:
    pipeline = get_saved_pipeline(pipeline_id, tenant_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="Saved pipeline not found.")
    return _with_health(pipeline)


@router.post("")
def create_pipeline(
    body: CreatePipelineRequest,
    tenant_id: Annotated[str, Depends(require_role("editor"))],
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
        drift_threshold_pct=body.drift_threshold_pct,
        quality_rules=[r.model_dump() for r in body.quality_rules],
        require_approval=body.require_approval,
        approval_threshold_rows=body.approval_threshold_rows,
    )


@router.get("/{pipeline_id}/history")
def get_pipeline_history(
    pipeline_id: str,
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
    limit: int = Query(default=DEFAULT_PIPELINE_HISTORY_LIMIT, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Sprint 17 (ADR-017) — time series of the `limit` most recent
    executions of one saved pipeline (oldest first), for the "Histórico
    comparável" view: KPI trend charts and the two-run diff. 404s (rather
    than returning an empty list) when the pipeline itself doesn't exist or
    isn't owned by this tenant — same "unknown vs. empty" distinction
    `get_pipeline` already makes, so the frontend can tell "no pipeline"
    from "pipeline with no runs yet" apart.

    `limit` (Sprint 17 code review, PR #64): a tight-cron pipeline can
    accumulate thousands of runs — same unbounded-query risk `GET /runs`'s
    own `limit` already guards against. Defaults to
    `audit.db.DEFAULT_PIPELINE_HISTORY_LIMIT` (200); capped at 1000 so a
    caller can widen the window without being able to request an unbounded
    query.
    """
    if get_saved_pipeline(pipeline_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Saved pipeline not found.")
    return list_pipeline_run_history(pipeline_id, tenant_id, limit=limit)


@router.patch("/{pipeline_id}")
def patch_pipeline(
    pipeline_id: str,
    body: UpdatePipelineRequest,
    tenant_id: Annotated[str, Depends(require_role("editor"))],
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
        drift_threshold_pct=body.drift_threshold_pct,
        quality_rules=(
            [r.model_dump() for r in body.quality_rules] if body.quality_rules is not None else None
        ),
        require_approval=body.require_approval,
        approval_threshold_rows=body.approval_threshold_rows,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Saved pipeline not found.")
    return updated


# Sprint 30 (ADR-031) — per-pipeline LLM provider/model selection, split into its
# own request model/endpoints (rather than folded into `UpdatePipelineRequest`/
# `patch_pipeline`) since "clear the override" needs `llm_provider=None,
# llm_model=None` to mean something different from "field omitted, leave
# unchanged" — the PATCH-with-`None`-means-omitted convention every other field on
# `UpdatePipelineRequest` already uses would make clearing an override
# inexpressible.
class SetPipelineLlmConfigRequest(BaseModel):
    """`llm_provider`/`llm_model` both `None` clears the override (falls back to
    this deployment's global `AI_ETL_LLM_PROVIDER`/`AI_ETL_LLM_MODEL`,
    `core/llm.py`). Providing only one of the two is rejected — see
    `_model_validator` below — since a provider without a model (or vice versa) is
    not a state `core.llm.ALLOWED_MODELS_BY_PROVIDER` can validate.
    """

    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None

    @model_validator(mode="after")
    def _both_or_neither(self) -> "SetPipelineLlmConfigRequest":
        if (self.llm_provider is None) != (self.llm_model is None):
            raise ValueError(
                "llm_provider and llm_model must be set together, or both omitted/null "
                "to clear the override."
            )
        return self


@router.get("/{pipeline_id}/llm-config")
def get_pipeline_llm_config(
    pipeline_id: str,
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> dict[str, Any]:
    """Sprint 30 (ADR-031) — current LLM provider/model override for one saved
    pipeline. Both fields `None` means "no override, using this deployment's
    global default" (`GET /config`'s `model_name`)."""
    config = get_saved_pipeline_llm_config(pipeline_id, tenant_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Saved pipeline not found.")
    return config


@router.put("/{pipeline_id}/llm-config")
def set_pipeline_llm_config(
    pipeline_id: str,
    body: SetPipelineLlmConfigRequest,
    tenant_id: Annotated[str, Depends(require_role("editor"))],
) -> dict[str, Any]:
    """Sprint 30 (ADR-031) — set or clear a saved pipeline's LLM provider/model
    override. A non-null pair must be in `core.llm.ALLOWED_MODELS_BY_PROVIDER`
    (same allowlist-validation posture as `_validate_source_type` above for
    `source_type`, ADR-016 Decision 3) — an out-of-allowlist choice is rejected
    with 400 before it's ever persisted.
    """
    if body.llm_provider is not None and body.llm_model is not None:
        try:
            validate_provider_and_model(body.llm_provider, body.llm_model)
        except UnsupportedProviderOrModelError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    updated = set_saved_pipeline_llm_config(
        pipeline_id, tenant_id, llm_provider=body.llm_provider, llm_model=body.llm_model
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Saved pipeline not found.")
    return updated


@router.get("/llm/allowed-models")
def get_allowed_llm_models(
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> dict[str, list[str]]:
    """Sprint 30 (ADR-031) — the allowlist `PUT /pipelines/{id}/llm-config`
    validates against, sorted for stable UI rendering. Lives under `/pipelines`
    (not a new top-level router) since its only consumer is the per-pipeline LLM
    config picker this endpoint exists to back."""
    return {provider: sorted(models) for provider, models in ALLOWED_MODELS_BY_PROVIDER.items()}
