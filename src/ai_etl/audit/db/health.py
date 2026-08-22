"""Saved-pipeline health snapshot, run history, and drift-comparison lookups.

Split out of the former monolithic `audit/db.py` (Sprint 33) — see
`audit/db/__init__.py` for the full split rationale.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select, update

from ai_etl.audit.connection import get_engine
from ai_etl.audit.models import analysis_runs, runs, saved_pipelines, stage_latencies


def record_pipeline_health(pipeline_id: str, status: str, error: Optional[str] = None) -> None:
    """Sprint 15 (ADR-020) — update the health-snapshot cache on
    `saved_pipelines` after a scheduled fire's *final* attempt (any Level-B
    retries `execution_queue.py::run_full_analysis_task` performs are
    already exhausted by the time this is called — this records one fire's
    terminal outcome, not each individual attempt).

    `status` is `"completed"` or `"failed"` (mirrors `runs.status`).
    `consecutive_failures` increments atomically via a SQL expression (not
    read-then-write) to stay correct even if two fires of the same pipeline
    somehow overlap; resets to 0 on `"completed"`. `last_error` is cleared
    (`NULL`) on success, set to `error` otherwise.

    Best-effort by convention of every other post-completion side effect in
    this codebase (see `services/alerting.py`) — callers are expected to
    wrap this in a try/except so a health-tracking hiccup never fails the
    run itself; this function does not swallow errors on its own, since
    unlike a delivery provider, a DB write failing here is worth surfacing
    to the caller's own error handling.
    """
    now = datetime.now(tz=timezone.utc)
    if status == "completed":
        values: dict[str, Any] = {
            "consecutive_failures": 0,
            "last_status": status,
            "last_error": None,
            "updated_at": now,
        }
    else:
        values = {
            "consecutive_failures": saved_pipelines.c.consecutive_failures + 1,
            "last_status": status,
            "last_error": error,
            "updated_at": now,
        }
    stmt = update(saved_pipelines).where(saved_pipelines.c.id == pipeline_id).values(**values)
    with get_engine().begin() as conn:
        conn.execute(stmt)


DEFAULT_PIPELINE_HEALTH_WINDOW = 20


def get_pipeline_health(
    pipeline_id: str, tenant_id: str, window: int = DEFAULT_PIPELINE_HEALTH_WINDOW
) -> dict[str, Any]:
    """Sprint 15 (ADR-020) — success rate and average latency for a saved
    pipeline's most recent `window` fires, computed from `runs`/
    `stage_latencies` (both already persisted per execution, ADR-007/
    ADR-017) — pure aggregation, no new instrumentation.

    `consecutive_failures`/`last_status`/`last_error` are *not* recomputed
    here — they live on the `saved_pipelines` row itself (the cache
    `record_pipeline_health` maintains); read them from `get_saved_pipeline`
    instead, same way `api/routers/pipelines.py` already does for every
    other field.

    Returns `{"success_rate": float | None, "avg_latency_seconds":
    float | None, "sample_size": int}`. Both rate/latency are `None` when
    `sample_size` is 0 (the pipeline has never fired) — never a fabricated
    0.0, which would misleadingly read as "0% success" instead of "no data
    yet".
    """
    recent_stmt = (
        select(runs.c.run_id, runs.c.status)
        .where(runs.c.saved_pipeline_id == pipeline_id, runs.c.tenant_id == tenant_id)
        .order_by(runs.c.timestamp.desc())
        .limit(window)
    )
    with get_engine().connect() as conn:
        recent_rows = conn.execute(recent_stmt).fetchall()

    sample_size = len(recent_rows)
    if sample_size == 0:
        return {"success_rate": None, "avg_latency_seconds": None, "sample_size": 0}

    completed = sum(1 for row in recent_rows if row.status == "completed")
    success_rate = completed / sample_size

    run_ids = [row.run_id for row in recent_rows]
    latency_stmt = (
        select(
            stage_latencies.c.run_id,
            func.sum(stage_latencies.c.duration_seconds).label("total_seconds"),
        )
        .where(stage_latencies.c.run_id.in_(run_ids), stage_latencies.c.run_type == "silver")
        .group_by(stage_latencies.c.run_id)
    )
    with get_engine().connect() as conn:
        latency_rows = conn.execute(latency_stmt).fetchall()

    avg_latency_seconds = (
        sum(row.total_seconds for row in latency_rows) / len(latency_rows) if latency_rows else None
    )
    return {
        "success_rate": success_rate,
        "avg_latency_seconds": avg_latency_seconds,
        "sample_size": sample_size,
    }


# Sprint 17 code review (PR #64): a tight-cron saved pipeline (ADR-016 allows
# any valid cron, including every few minutes) accumulates thousands of runs
# over weeks/months with no cap — same class of unbounded-query risk
# `load_history`'s own `limit=20` default already guards against for the
# avulso run list. 200 (vs. `load_history`'s 20) is a deliberate, larger
# default: a KPI *trend* chart needs more points to actually show a trend,
# but still bounded, not "every run ever."
DEFAULT_PIPELINE_HISTORY_LIMIT = 200


def list_pipeline_run_history(
    pipeline_id: str, tenant_id: str, limit: int = DEFAULT_PIPELINE_HISTORY_LIMIT
) -> list[dict[str, Any]]:
    """Sprint 17 (ADR-017) — the `limit` most recent executions of one saved
    pipeline, oldest first, with the Gold/Science KPIs the time-series view
    charts.

    Scoped by `tenant_id` in addition to `saved_pipeline_id` — same
    defense-in-depth pattern as `get_saved_pipeline`/`_run_belongs_to_tenant`:
    a pipeline id alone should never be enough to read another tenant's run
    history, even though in practice a saved pipeline's own `tenant_id` and
    the runs it produced always agree (this function doesn't rely on that
    invariant holding, only checks it explicitly).

    `cost_usd`/`model_name`/`total_tokens`/`gold_subtasks`/`science_subtasks`
    come from a `LEFT OUTER JOIN` against `analysis_runs`, same as
    `load_history` — a scheduled fire with no `business_question` (Silver-only)
    has no matching row and reads back as `None`/`NaN`, not zero.

    `limit` bounds the query itself (not a post-hoc slice of every row ever
    fetched) — the *most recent* `limit` executions, since "the last 200"
    is what a trend view needs, not an arbitrary 200 from the beginning of
    the pipeline's history. Implemented as an inner query ordered `DESC`
    with the `LIMIT`, then re-ordered `ASC` in the outer query so the
    caller (and the chart) still gets oldest-first — the shape every
    existing caller/test expects.
    """
    base = (
        select(
            runs.c.run_id,
            runs.c.status,
            runs.c.rows_loaded,
            runs.c.timestamp,
            runs.c.error,
            analysis_runs.c.cost_usd,
            analysis_runs.c.model_name,
            analysis_runs.c.total_tokens,
            analysis_runs.c.gold_subtasks,
            analysis_runs.c.science_subtasks,
        )
        .select_from(runs.outerjoin(analysis_runs, runs.c.run_id == analysis_runs.c.run_id))
        .where(runs.c.saved_pipeline_id == pipeline_id, runs.c.tenant_id == tenant_id)
        .order_by(runs.c.timestamp.desc())
        .limit(limit)
        .subquery()
    )
    stmt = select(base).order_by(base.c.timestamp.asc())
    with get_engine().connect() as conn:
        rows_ = conn.execute(stmt).fetchall()
    return [
        {
            "run_id": row.run_id,
            "status": row.status,
            "rows_loaded": row.rows_loaded,
            "timestamp": row.timestamp,
            "error": row.error,
            "cost_usd": row.cost_usd,
            "model_name": row.model_name,
            "total_tokens": row.total_tokens,
            "gold_subtasks": row.gold_subtasks,
            "science_subtasks": row.science_subtasks,
        }
        for row in rows_
    ]


def get_previous_completed_run(
    saved_pipeline_id: str, exclude_run_id: str
) -> Optional[dict[str, Any]]:
    """Most recent `status = "completed"` run for `saved_pipeline_id`,
    excluding `exclude_run_id` (the run currently being evaluated) — the
    "second most recent fire" ADR-018 Decision 1 compares against. `None`
    when there is no prior completed run (the pipeline's first fire, or every
    prior fire failed) — the caller (`services/alerting.py`) treats that as
    "nothing to compare," not an error.

    A deliberately smaller, more targeted sibling of `list_pipeline_run_history`
    above (Sprint 17, ADR-017): that function returns up to
    `DEFAULT_PIPELINE_HISTORY_LIMIT` oldest-first rows for a time-series chart;
    this one returns at most one row — the single most recent *completed* run
    other than the one just evaluated — which is all Sprint 14's drift check
    needs. Kept separate rather than built on top of
    `list_pipeline_run_history` (e.g. `limit=2`, filter, take the older one)
    since that function doesn't filter by `status`, and a failed intervening
    fire would then silently become "the previous run" for comparison instead
    of being skipped.

    Left-outer-joins `analysis_runs` for `cost_usd`/`total_tokens`, same
    pattern as `load_history` — a Silver-only scheduled run (no
    `business_question`, so `save_analysis` never ran) reads those back as
    `None`, not `0`, matching `load_history`'s "no analysis, no cost" signal.
    """
    stmt = (
        select(
            runs.c.run_id,
            runs.c.rows_loaded,
            runs.c.timestamp,
            analysis_runs.c.cost_usd,
            analysis_runs.c.total_tokens,
        )
        .select_from(runs.outerjoin(analysis_runs, runs.c.run_id == analysis_runs.c.run_id))
        .where(
            runs.c.saved_pipeline_id == saved_pipeline_id,
            runs.c.run_id != exclude_run_id,
            runs.c.status == "completed",
        )
        .order_by(runs.c.timestamp.desc())
        .limit(1)
    )
    with get_engine().connect() as conn:
        row = conn.execute(stmt).first()
    if row is None:
        return None
    return {
        "run_id": row.run_id,
        "rows_loaded": row.rows_loaded,
        "cost_usd": row.cost_usd,
        "total_tokens": row.total_tokens,
        "timestamp": row.timestamp,
    }
