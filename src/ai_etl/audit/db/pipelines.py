"""Saved-pipeline CRUD, scheduling claims, write-approval, and LLM-config overrides.

Split out of the former monolithic `audit/db.py` (Sprint 33) — see
`audit/db/__init__.py` for the full split rationale.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import insert, select, update

from ai_etl.audit.connection import get_engine
from ai_etl.audit.models import runs, saved_pipelines
from ai_etl.core.scheduling import compute_next_run_at


def _saved_pipeline_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "name": row.name,
        "source_type": row.source_type,
        "spec": row.spec,
        "business_question": row.business_question,
        "cron_schedule": row.cron_schedule,
        "is_active": row.is_active,
        "next_run_at": row.next_run_at,
        "last_task_id": row.last_task_id,
        "last_run_at": row.last_run_at,
        # Sprint 14 (ADR-018) — per-pipeline "% change worth alerting on".
        "drift_threshold_pct": row.drift_threshold_pct,
        # Sprint 15 (ADR-020) — health-snapshot cache, see models.py.
        "consecutive_failures": row.consecutive_failures,
        "last_status": row.last_status,
        "last_error": row.last_error,
        # Sprint 16 (ADR-023) — operator-defined quality rules, see models.py.
        "quality_rules": row.quality_rules or [],
        # Sprint 27 (ADR-028) — write-approval gate, see models.py.
        "require_approval": row.require_approval,
        "approval_threshold_rows": row.approval_threshold_rows,
        "last_approved_at": row.last_approved_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def create_saved_pipeline(
    tenant_id: str,
    name: str,
    source_type: str,
    spec: str,
    cron_schedule: str,
    business_question: str = "",
    drift_threshold_pct: float = 20.0,
    quality_rules: Optional[list[dict[str, Any]]] = None,
    require_approval: bool = False,
    approval_threshold_rows: Optional[int] = None,
) -> dict[str, Any]:
    """Persist a new saved pipeline (Sprint 13, ADR-016; `drift_threshold_pct`
    added Sprint 14, ADR-018; `quality_rules` added Sprint 16, ADR-023;
    `require_approval`/`approval_threshold_rows` added Sprint 27, ADR-028).

    Caller (the `/pipelines` router) is responsible for validating
    `cron_schedule` (`core.scheduling.validate_cron_schedule`),
    `source_type` (`core.scheduling.SCHEDULABLE_SOURCE_TYPES`, ADR-016
    Decision 3), and each `quality_rules` entry's `operator`/`value` shape
    (Pydantic, ADR-023) before calling this — this function trusts its
    inputs, matching `save_run`/`save_analysis`'s existing "no revalidation
    at the persistence layer" pattern.
    """
    now = datetime.now(tz=timezone.utc)
    pipeline_id = str(uuid.uuid4())
    next_run_at = compute_next_run_at(cron_schedule, now)
    rules = quality_rules or []
    stmt = insert(saved_pipelines).values(
        id=pipeline_id,
        tenant_id=tenant_id,
        name=name,
        source_type=source_type,
        spec=spec,
        business_question=business_question,
        cron_schedule=cron_schedule,
        is_active=True,
        next_run_at=next_run_at,
        last_task_id=None,
        last_run_at=None,
        drift_threshold_pct=drift_threshold_pct,
        consecutive_failures=0,
        last_status=None,
        last_error=None,
        quality_rules=rules,
        require_approval=require_approval,
        approval_threshold_rows=approval_threshold_rows,
        last_approved_at=None,
        created_at=now,
        updated_at=now,
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)
    return {
        "id": pipeline_id,
        "tenant_id": tenant_id,
        "name": name,
        "source_type": source_type,
        "spec": spec,
        "business_question": business_question,
        "cron_schedule": cron_schedule,
        "is_active": True,
        "next_run_at": next_run_at,
        "last_task_id": None,
        "last_run_at": None,
        "drift_threshold_pct": drift_threshold_pct,
        "consecutive_failures": 0,
        "last_status": None,
        "last_error": None,
        "quality_rules": rules,
        "require_approval": require_approval,
        "approval_threshold_rows": approval_threshold_rows,
        "last_approved_at": None,
        "created_at": now,
        "updated_at": now,
    }


def list_saved_pipelines(tenant_id: str) -> list[dict[str, Any]]:
    """Return every saved pipeline belonging to `tenant_id`, most recently updated first."""
    stmt = (
        select(saved_pipelines)
        .where(saved_pipelines.c.tenant_id == tenant_id)
        .order_by(saved_pipelines.c.updated_at.desc())
    )
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [_saved_pipeline_row_to_dict(row) for row in rows]


def get_saved_pipeline(pipeline_id: str, tenant_id: str) -> Optional[dict[str, Any]]:
    """Return one saved pipeline, scoped to `tenant_id` — `None` covers both
    "unknown id" and "belongs to another tenant" (same soft-fail shape as
    `load_full_result`'s ownership check)."""
    stmt = select(saved_pipelines).where(
        saved_pipelines.c.id == pipeline_id, saved_pipelines.c.tenant_id == tenant_id
    )
    with get_engine().connect() as conn:
        row = conn.execute(stmt).first()
    return _saved_pipeline_row_to_dict(row) if row is not None else None


def update_saved_pipeline(
    pipeline_id: str,
    tenant_id: str,
    name: Optional[str] = None,
    source_type: Optional[str] = None,
    spec: Optional[str] = None,
    cron_schedule: Optional[str] = None,
    business_question: Optional[str] = None,
    is_active: Optional[bool] = None,
    drift_threshold_pct: Optional[float] = None,
    quality_rules: Optional[list[dict[str, Any]]] = None,
    require_approval: Optional[bool] = None,
    approval_threshold_rows: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Partial update (PATCH semantics) — only fields passed as non-`None` change.

    `approval_threshold_rows` follows the same "explicit value wins, `None`
    means omitted" PATCH convention as every other optional field here — a
    caller cannot use this function alone to *clear* an existing threshold
    back to `NULL` ("always require approval"); the router only ever forwards
    a Pydantic-validated value, never a sentinel-vs-omitted distinction for
    this particular field (Sprint 27, ADR-028; same accepted limitation
    `drift_threshold_pct` already has).

    Recomputes `next_run_at` whenever `cron_schedule` changes, or whenever a
    paused pipeline (`is_active=False -> True`) is resumed — a schedule that
    was paused for a week should fire from "now", not immediately catch up
    on every tick it missed while paused.

    Returns the updated row, or `None` if `pipeline_id` doesn't exist or
    doesn't belong to `tenant_id` (ownership check happens first, before any
    write — same pattern as `_run_belongs_to_tenant`).
    """
    existing = get_saved_pipeline(pipeline_id, tenant_id)
    if existing is None:
        return None

    now = datetime.now(tz=timezone.utc)
    values: dict[str, Any] = {"updated_at": now}
    if name is not None:
        values["name"] = name
    if source_type is not None:
        values["source_type"] = source_type
    if spec is not None:
        values["spec"] = spec
    if business_question is not None:
        values["business_question"] = business_question
    if drift_threshold_pct is not None:
        values["drift_threshold_pct"] = drift_threshold_pct
    if quality_rules is not None:
        # `is not None` (not truthiness) — an explicit `quality_rules=[]` is a valid
        # "clear all my rules" update, distinct from "field omitted, leave as-is".
        values["quality_rules"] = quality_rules
    if require_approval is not None:
        values["require_approval"] = require_approval
    if approval_threshold_rows is not None:
        values["approval_threshold_rows"] = approval_threshold_rows

    resolved_cron = cron_schedule if cron_schedule is not None else existing["cron_schedule"]
    became_active = is_active is True and existing["is_active"] is False
    if cron_schedule is not None:
        values["cron_schedule"] = cron_schedule
    if is_active is not None:
        values["is_active"] = is_active
    if cron_schedule is not None or became_active:
        values["next_run_at"] = compute_next_run_at(resolved_cron, now)

    stmt = (
        update(saved_pipelines)
        .where(saved_pipelines.c.id == pipeline_id, saved_pipelines.c.tenant_id == tenant_id)
        .values(**values)
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)
    return get_saved_pipeline(pipeline_id, tenant_id)


def list_due_pipelines(now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Return every active saved pipeline (across all tenants) whose
    `next_run_at` has passed — the query `services/scheduler.py`'s Celery
    beat task runs on each tick. No `tenant_id` filter: the beat task itself
    isn't acting on behalf of any one tenant."""
    cutoff = now or datetime.now(tz=timezone.utc)
    stmt = select(saved_pipelines).where(
        saved_pipelines.c.is_active.is_(True), saved_pipelines.c.next_run_at <= cutoff
    )
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [_saved_pipeline_row_to_dict(row) for row in rows]


def claim_due_pipeline(
    pipeline_id: str, expected_next_run_at: datetime, new_next_run_at: datetime
) -> bool:
    """Atomically claim one due fire of a saved pipeline (Sprint 13 code
    review fix — see ADR-016 addendum on beat-tick concurrency).

    A compare-and-swap on `next_run_at`: the `UPDATE` only affects a row if
    `next_run_at` still equals `expected_next_run_at` (the value
    `list_due_pipelines` read it as). If a Celery beat tick overruns the
    next tick's start (many due pipelines, a slow Redis rate-limit check,
    worker backlog — `next_run_at` was previously only advanced *after*
    `enqueue_analysis` returned, so an overlapping tick would still see the
    same pipeline as due and fire it twice), only one tick's `UPDATE` can
    win this race: Postgres serializes concurrent `UPDATE`s to the same row,
    so the loser's `WHERE` clause simply matches 0 rows once the winner's
    transaction commits. No separate lock table or Redis dependency needed.

    Returns `True` if this call won the claim (must proceed to
    `enqueue_analysis`), `False` if another tick already claimed this fire
    (must skip — not an error, just lost the race).
    """
    stmt = (
        update(saved_pipelines)
        .where(
            saved_pipelines.c.id == pipeline_id,
            saved_pipelines.c.next_run_at == expected_next_run_at,
        )
        .values(next_run_at=new_next_run_at, updated_at=datetime.now(tz=timezone.utc))
    )
    with get_engine().begin() as conn:
        result = conn.execute(stmt)
        return result.rowcount == 1


def release_pipeline_claim(
    pipeline_id: str, claimed_next_run_at: datetime, original_next_run_at: datetime
) -> None:
    """Undo a `claim_due_pipeline` win when `enqueue_analysis` then fails
    (rate limit or any other error) — reverts `next_run_at` back to the
    original due time so the pipeline is retried on the very next tick
    instead of silently waiting a full cron period.

    Guarded the same way as the claim itself (`next_run_at` must still equal
    what this call's claim just set) — in the normal single-claimant flow
    this is always true; the guard is defensive, not load-bearing."""
    stmt = (
        update(saved_pipelines)
        .where(
            saved_pipelines.c.id == pipeline_id,
            saved_pipelines.c.next_run_at == claimed_next_run_at,
        )
        .values(next_run_at=original_next_run_at, updated_at=datetime.now(tz=timezone.utc))
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)


def record_pipeline_run(pipeline_id: str, task_id: str) -> None:
    """Record that a claimed saved pipeline actually fired: remember
    `last_task_id`/`last_run_at`. `next_run_at` is *not* touched here — by
    the time this is called, `claim_due_pipeline` has already advanced it
    atomically (see that function's docstring for why the ordering matters).

    `task_id` is the Celery task id `enqueue_analysis` returns, not yet the
    eventual `runs.run_id` (generated later, inside the task itself) — same
    distinction `GET /runs/{task_id}/status` already relies on."""
    now = datetime.now(tz=timezone.utc)
    stmt = (
        update(saved_pipelines)
        .where(saved_pipelines.c.id == pipeline_id)
        .values(last_task_id=task_id, last_run_at=now, updated_at=now)
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)


def mark_pipeline_approved(pipeline_id: str, tenant_id: str) -> None:
    """Sprint 27 (ADR-028) — record that a saved pipeline just had an
    operator-approved write. Called once, by
    `services/pipeline_service.py::resume_pending_load`, on the first
    approval that actually completes a write. Scoped by `tenant_id` (same
    ownership-check pattern as `update_saved_pipeline`) — a no-op if the
    pipeline doesn't exist or isn't owned by this tenant, rather than
    raising, matching this module's soft-fail style for post-hoc bookkeeping.
    """
    now = datetime.now(tz=timezone.utc)
    stmt = (
        update(saved_pipelines)
        .where(saved_pipelines.c.id == pipeline_id, saved_pipelines.c.tenant_id == tenant_id)
        .values(last_approved_at=now, updated_at=now)
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)


def get_run_status_and_pipeline(run_id: str, tenant_id: str) -> Optional[dict[str, Any]]:
    """Sprint 27 (ADR-028) — the small slice of a `runs` row
    `resume_pending_load`/`reject_pending_load` need before touching any
    storage artifact: current `status` and which `saved_pipeline_id` (if
    any) produced it. Tenant-scoped, `None` for "unknown or not owned" —
    same soft-fail shape as `get_saved_pipeline`.
    """
    stmt = select(runs.c.status, runs.c.saved_pipeline_id).where(
        runs.c.run_id == run_id, runs.c.tenant_id == tenant_id
    )
    with get_engine().connect() as conn:
        row = conn.execute(stmt).first()
    if row is None:
        return None
    return {"status": row.status, "saved_pipeline_id": row.saved_pipeline_id}


def list_pending_approvals(tenant_id: str) -> list[dict[str, Any]]:
    """Sprint 27 (ADR-028) — every run of this tenant currently
    `awaiting_approval`, most recently created first. Backs
    `GET /runs/pending-approval` — the queue an operator works through.
    `LEFT OUTER JOIN`ed against `saved_pipelines` for the pipeline's `name`
    (an avulso run is never gated, per the ADR, so this list is expected to
    be scheduled-fire-only in practice, but the join tolerates a `NULL`
    `saved_pipeline_id` the same way `list_pipeline_run_history` tolerates a
    missing `analysis_runs` row).
    """
    stmt = (
        select(
            runs.c.run_id,
            runs.c.spec,
            runs.c.timestamp,
            runs.c.saved_pipeline_id,
            saved_pipelines.c.name.label("pipeline_name"),
        )
        .select_from(
            runs.outerjoin(saved_pipelines, runs.c.saved_pipeline_id == saved_pipelines.c.id)
        )
        .where(runs.c.tenant_id == tenant_id, runs.c.status == "awaiting_approval")
        .order_by(runs.c.timestamp.desc())
    )
    with get_engine().connect() as conn:
        rows_ = conn.execute(stmt).fetchall()
    return [
        {
            "run_id": row.run_id,
            "spec": row.spec,
            "timestamp": row.timestamp,
            "saved_pipeline_id": row.saved_pipeline_id,
            "pipeline_name": row.pipeline_name,
        }
        for row in rows_
    ]


# Sprint 30 (ADR-031) — per-`saved_pipeline` LLM provider/model override
# (migration 0016). Deliberately two small standalone functions rather than new
# parameters bolted onto `create_saved_pipeline`/`update_saved_pipeline`/
# `get_saved_pipeline`/`_saved_pipeline_row_to_dict` above: this sprint runs in a
# worktree parallel to Sprints 31/32, both of which may also touch this file, and
# the isolation rule for this batch is "append-only, never edit an existing
# function" to keep the 3 PRs' diffs merge-safe. `api/routers/pipelines.py`
# composes these with `get_saved_pipeline`'s existing dict the same way
# `_with_health` already composes `get_pipeline_health` onto it.


def get_saved_pipeline_llm_config(pipeline_id: str, tenant_id: str) -> Optional[dict[str, Any]]:
    """Return `{"llm_provider": str | None, "llm_model": str | None}` for one saved
    pipeline, scoped to `tenant_id` — `None` (the whole return value) covers both
    "unknown id" and "belongs to another tenant", same soft-fail shape as
    `get_saved_pipeline`. Both dict values `None` means "no override configured,
    this pipeline uses the deployment's global `AI_ETL_LLM_PROVIDER`/
    `AI_ETL_LLM_MODEL`" (`core/llm.py`).
    """
    stmt = select(saved_pipelines.c.llm_provider, saved_pipelines.c.llm_model).where(
        saved_pipelines.c.id == pipeline_id, saved_pipelines.c.tenant_id == tenant_id
    )
    with get_engine().connect() as conn:
        row = conn.execute(stmt).first()
    if row is None:
        return None
    return {"llm_provider": row.llm_provider, "llm_model": row.llm_model}


def set_saved_pipeline_llm_config(
    pipeline_id: str,
    tenant_id: str,
    llm_provider: Optional[str],
    llm_model: Optional[str],
) -> Optional[dict[str, Any]]:
    """Set (or clear) one saved pipeline's LLM provider/model override.

    `llm_provider`/`llm_model` are always written together — pass both as `None` to
    clear the override back to "use this deployment's global default" (matching
    `models.py`'s "always set or cleared together" column comment); the caller
    (`api/routers/pipelines.py`) is responsible for validating a non-`None` pair
    against `core.llm.ALLOWED_MODELS_BY_PROVIDER`
    (`core.llm.validate_provider_and_model`) before calling this — this function
    trusts its inputs, matching every other `audit/db` writer's "no revalidation
    at the persistence layer" pattern (see `create_saved_pipeline`'s docstring).

    Returns the updated `{"llm_provider", "llm_model"}` dict, or `None` if
    `pipeline_id` doesn't exist or doesn't belong to `tenant_id` (ownership check
    happens first, before any write — same pattern as `update_saved_pipeline`).
    """
    existing = get_saved_pipeline(pipeline_id, tenant_id)
    if existing is None:
        return None

    stmt = (
        update(saved_pipelines)
        .where(saved_pipelines.c.id == pipeline_id, saved_pipelines.c.tenant_id == tenant_id)
        .values(
            llm_provider=llm_provider,
            llm_model=llm_model,
            updated_at=datetime.now(tz=timezone.utc),
        )
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)
    return {"llm_provider": llm_provider, "llm_model": llm_model}
