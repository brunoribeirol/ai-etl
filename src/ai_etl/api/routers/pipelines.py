"""`/pipelines` endpoints: CRUD for saved (recurring) pipelines (Sprint 13,
ADR-016). Distinct from `/runs` (avulso, one-off execution), which is
unchanged. Same auth pattern as `routers/runs.py`: `get_current_tenant_id`.

Actual firing happens in `services/scheduler.py`'s Celery beat task, not
here: this router only manages the `saved_pipelines` row (create/list/get/
patch); it never itself enqueues a run.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from ai_etl.api.deps import get_current_tenant_id, require_role
from ai_etl.audit.db import (
    DEFAULT_PIPELINE_HISTORY_LIMIT,
    create_saved_pipeline,
    delete_saved_pipeline,
    get_pipeline_health,
    get_saved_pipeline,
    get_saved_pipeline_llm_config,
    get_saved_pipeline_notification_config,
    list_pipeline_run_history,
    list_saved_pipelines,
    set_saved_pipeline_llm_config,
    set_saved_pipeline_notification_config,
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
from ai_etl.services.notifications import ALLOWED_NOTIFICATION_CHANNELS

router = APIRouter(prefix="/pipelines", tags=["pipelines"])

# Sprint 16 (ADR-023): mirrors `agents/pipeline/quality.py::_CUSTOM_RULE_OPERATORS`'s keys
# exactly; kept as a literal here (not imported) so the API's request-validation
# error message names them without importing an `agents/` module into `api/`.
QualityRuleOperator = Literal["not_null", "gte", "lte", "gt", "lt", "eq", "ne"]


class QualityRule(BaseModel):
    """One operator-defined data quality rule (Sprint 16, ADR-023): a whitelisted
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
    # Sprint 14 (ADR-018): "% change on a tracked KPI worth alerting on"
    # for this pipeline's own recurring drift check. Default matches
    # audit/models.py's server_default, so a client that omits it gets the
    # same behavior as a row written before this field existed.
    drift_threshold_pct: float = Field(default=20.0, gt=0)
    # Sprint 16 (ADR-023): operator-defined quality rules, run on every subsequent
    # execution of this saved pipeline alongside the fixed checks. Defaults to no
    # custom rules, same behavior as every pipeline created before this sprint.
    quality_rules: list[QualityRule] = Field(default_factory=list)
    # Sprint 27 (ADR-028): opt-in write-approval gate. `False` by default: zero
    # behavior change unless explicitly turned on. See `agents/pipeline/loader.py`.
    require_approval: bool = False
    approval_threshold_rows: Optional[int] = Field(default=None, ge=0)


class UpdatePipelineRequest(BaseModel):
    """All fields optional: PATCH semantics, only provided fields change.

    `quality_rules`, if provided, REPLACES the pipeline's whole rule set (not a
    per-rule merge): the same "provide the field, it wins whole" PATCH semantics
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


# Concurrent database lookups when saved pipelines are listed or returned (2026-10-16).
_LOOKUP_WORKERS = 6


def _lookups(pipeline: dict[str, Any]) -> list[Callable[[], Any]]:
    """The three per-pipeline reads merged onto a saved pipeline: health (Sprint 15, ADR-020),
    LLM override (Sprint 30, ADR-031) and notification target (Sprint 37, ADR-034). Each is its
    own tenant-scoped transaction; run one after another they cost several database round
    trips per pipeline, and `GET /pipelines` took 10 s for two pipelines on production
    (2026-10-16: API in Amsterdam, database in São Paulo), so they run concurrently."""
    pipeline_id, tenant_id = pipeline["id"], pipeline["tenant_id"]
    return [
        lambda: get_pipeline_health(pipeline_id, tenant_id),
        lambda: get_saved_pipeline_llm_config(pipeline_id, tenant_id),
        lambda: get_saved_pipeline_notification_config(pipeline_id, tenant_id),
    ]


def _compose(
    pipeline: dict[str, Any],
    health: dict[str, Any],
    llm_config: dict[str, Any] | None,
    notification_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge the lookups onto the saved-pipeline dict. The notification target's value is
    never included (only whether one is configured and active), see
    `get_saved_pipeline_notification_config`'s docstring for why."""
    llm = llm_config or {"llm_provider": None, "llm_model": None}
    notification = notification_config or {
        "notification_channel": None,
        "notification_configured": False,
        "notification_active": True,
    }
    return {
        **pipeline,
        "success_rate": health["success_rate"],
        "avg_latency_seconds": health["avg_latency_seconds"],
        "health_sample_size": health["sample_size"],
        "llm_provider": llm["llm_provider"],
        "llm_model": llm["llm_model"],
        "notification_channel": notification["notification_channel"],
        "notification_configured": notification["notification_configured"],
        "notification_active": notification["notification_active"],
    }


def _with_health_all(pipelines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich every pipeline, all lookups in one pool; the order of `pipelines` is kept."""
    if not pipelines:
        return []
    with ThreadPoolExecutor(max_workers=_LOOKUP_WORKERS) as pool:
        futures = [[pool.submit(lookup) for lookup in _lookups(p)] for p in pipelines]
        return [
            _compose(p, *(future.result() for future in group))
            for p, group in zip(pipelines, futures, strict=True)
        ]


def _with_health(pipeline: dict[str, Any]) -> dict[str, Any]:
    return _with_health_all([pipeline])[0]


@router.get("")
def list_pipelines(
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> list[dict[str, Any]]:
    return _with_health_all(list_saved_pipelines(tenant_id))


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
    """Sprint 17 (ADR-017): time series of the `limit` most recent
    executions of one saved pipeline (oldest first), for the "Histórico
    comparável" view: KPI trend charts and the two-run diff. 404s (rather
    than returning an empty list) when the pipeline itself doesn't exist or
    isn't owned by this tenant: same "unknown vs. empty" distinction
    `get_pipeline` already makes, so the frontend can tell "no pipeline"
    from "pipeline with no runs yet" apart.

    `limit` (Sprint 17 code review, PR #64): a tight-cron pipeline can
    accumulate thousands of runs: same unbounded-query risk `GET /runs`'s
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


@router.delete("/{pipeline_id}", status_code=204)
def delete_pipeline(
    pipeline_id: str,
    tenant_id: Annotated[str, Depends(require_role("editor"))],
) -> None:
    """2026-09-04 gap-closing feature: until now, `is_active=False` (pause) was
     the only way to stop a saved pipeline: there was no way to actually remove
     a mistaken or test entry, so it stayed listed on `/pipelines` and `/summary`
     forever. Permanently deletes the `saved_pipelines` row; safe by schema design
    : `runs.saved_pipeline_id`/`analysis_runs.saved_pipeline_id` are both
     `ON DELETE SET NULL` (`audit/models.py`), so existing run history is
     detached, never deleted, by this call. `require_role("editor")`, same as
     `create_pipeline`/`patch_pipeline`: this is a regular tenant-scoped
     mutation, not a platform-admin action (no `log_admin_action`, matching the
     existing create/update endpoints in this router, neither of which logs
     either).
    """
    if not delete_saved_pipeline(pipeline_id, tenant_id):
        raise HTTPException(status_code=404, detail="Saved pipeline not found.")


# Sprint 30 (ADR-031): per-pipeline LLM provider/model selection, split into its
# own request model/endpoints (rather than folded into `UpdatePipelineRequest`/
# `patch_pipeline`) since "clear the override" needs `llm_provider=None,
# llm_model=None` to mean something different from "field omitted, leave
# unchanged": the PATCH-with-`None`-means-omitted convention every other field on
# `UpdatePipelineRequest` already uses would make clearing an override
# inexpressible.
class SetPipelineLlmConfigRequest(BaseModel):
    """`llm_provider`/`llm_model` both `None` clears the override (falls back to
    this deployment's global `AI_ETL_LLM_PROVIDER`/`AI_ETL_LLM_MODEL`,
    `core/llm.py`). Providing only one of the two is rejected: see
    `_model_validator` below: since a provider without a model (or vice versa) is
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
    """Sprint 30 (ADR-031): current LLM provider/model override for one saved
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
    """Sprint 30 (ADR-031): set or clear a saved pipeline's LLM provider/model
    override. A non-null pair must be in `core.llm.ALLOWED_MODELS_BY_PROVIDER`
    (same allowlist-validation posture as `_validate_source_type` above for
    `source_type`, ADR-016 Decision 3): an out-of-allowlist choice is rejected
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
    """Sprint 30 (ADR-031): the allowlist `PUT /pipelines/{id}/llm-config`
    validates against, sorted for stable UI rendering. Lives under `/pipelines`
    (not a new top-level router) since its only consumer is the per-pipeline LLM
    config picker this endpoint exists to back."""
    return {provider: sorted(models) for provider, models in ALLOWED_MODELS_BY_PROVIDER.items()}


# Sprint 37 (ADR-034): per-pipeline notification destination (email recipients /
# webhook URL), same "own request model/endpoints, not folded into
# `UpdatePipelineRequest`" rationale as the LLM-config block above: clearing the
# override needs `notification_channel=None, notification_target=None` to mean
# something different from "field omitted, leave unchanged".
class SetPipelineNotificationConfigRequest(BaseModel):
    """`notification_channel`/`notification_target` both `None` clears the
    override (falls back to this deployment's global
    `RESEND_API_KEY`/`SLACK_WEBHOOK_URL`/`TEAMS_WEBHOOK_URL`/
    `GOOGLE_CHAT_WEBHOOK_URL` env vars, `services/notifications.py`).
    Providing only one of the two is rejected: see `_both_or_neither`
    below: same reasoning as `SetPipelineLlmConfigRequest`.

    `notification_target` is plaintext on the wire (this endpoint requires
    `editor`, same as every other pipeline-mutating endpoint here): it is
    encrypted before being persisted (`services.secrets_service.encrypt_value`,
    called from `audit.db.set_saved_pipeline_notification_config`) and is
    never echoed back in this or any other response.
    """

    notification_channel: Optional[str] = None
    notification_target: Optional[str] = Field(default=None, min_length=1)
    notification_active: bool = True

    @model_validator(mode="after")
    def _both_or_neither(self) -> "SetPipelineNotificationConfigRequest":
        if (self.notification_channel is None) != (self.notification_target is None):
            raise ValueError(
                "notification_channel and notification_target must be set together, "
                "or both omitted/null to clear the override."
            )
        return self


@router.get("/{pipeline_id}/notification-config")
def get_pipeline_notification_config(
    pipeline_id: str,
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> dict[str, Any]:
    """Sprint 37 (ADR-034): current notification destination override for one
    saved pipeline. `notification_channel=None`/`notification_configured=False`
    means "no override, using this deployment's global channel(s)". The
    destination value itself (email recipients / webhook URL) is never
    returned by any endpoint, encrypted or not: see
    `audit.db.get_saved_pipeline_notification_config`'s docstring for why.
    """
    config = get_saved_pipeline_notification_config(pipeline_id, tenant_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Saved pipeline not found.")
    return config


@router.put("/{pipeline_id}/notification-config")
def set_pipeline_notification_config(
    pipeline_id: str,
    body: SetPipelineNotificationConfigRequest,
    tenant_id: Annotated[str, Depends(require_role("editor"))],
) -> dict[str, Any]:
    """Sprint 37 (ADR-034): set or clear a saved pipeline's notification
    destination override. A non-null `notification_channel` must be one of
    `services.notifications.ALLOWED_NOTIFICATION_CHANNELS` (same
    allowlist-validation posture as `_validate_source_type` above for
    `source_type`, ADR-016 Decision 3): an out-of-allowlist channel is
    rejected with 400 before it's ever persisted. `notification_target` is
    encrypted before it reaches the database and is never returned.
    """
    if (
        body.notification_channel is not None
        and body.notification_channel not in ALLOWED_NOTIFICATION_CHANNELS
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported notification channel '{body.notification_channel}'. "
                f"Allowed channels: {', '.join(ALLOWED_NOTIFICATION_CHANNELS)}."
            ),
        )

    updated = set_saved_pipeline_notification_config(
        pipeline_id,
        tenant_id,
        notification_channel=body.notification_channel,
        notification_target=body.notification_target,
        notification_active=body.notification_active,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Saved pipeline not found.")
    return updated


@router.get("/notifications/allowed-channels")
def get_allowed_notification_channels(
    tenant_id: Annotated[str, Depends(get_current_tenant_id)],
) -> list[str]:
    """Sprint 37 (ADR-034): the allowlist `PUT /pipelines/{id}/notification-config`
    validates against. Lives under `/pipelines` (not a new top-level router), same
    rationale as `GET /pipelines/llm/allowed-models`."""
    return list(ALLOWED_NOTIFICATION_CHANNELS)
