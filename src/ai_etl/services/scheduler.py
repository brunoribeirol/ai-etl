"""Celery beat task that fires due saved pipelines (Sprint 13, ADR-016).

`core/celery_app.py`'s `beat_schedule` calls `check_scheduled_pipelines_task`
on a fixed interval (`AI_ETL_SCHEDULER_INTERVAL_SECONDS`, default 60s). Each
tick: query `audit.db.list_due_pipelines()`, and for every due pipeline call
the *same* `execution_queue.enqueue_analysis()` `POST /runs` already uses —
no separate execution path, so a scheduled run is audited exactly like an
avulso one (ADR-016 Decision 1/2).

Requires a `celery beat` process running in production alongside the
existing worker (same Docker image, different Custom Start Command — see
ADR-016) for scheduled pipelines to actually fire; the web/API process and
the plain `celery worker` process do not run beat themselves.
"""

from __future__ import annotations

from typing import Any

from ai_etl.audit.db import list_due_pipelines, mark_pipeline_fired
from ai_etl.core.celery_app import celery_app
from ai_etl.core.scheduling import compute_next_run_at
from ai_etl.services.execution_queue import RateLimitExceededError, enqueue_analysis

RUNS_DIR = "./runs"


@celery_app.task(name="ai_etl.check_scheduled_pipelines")  # type: ignore[untyped-decorator]
def check_scheduled_pipelines_task() -> dict[str, Any]:
    """Fire every due, active saved pipeline. Returns a small summary dict
    (fired/skipped counts) — useful for beat log inspection, not consumed by
    any caller.

    One pipeline's failure (rate limit, or any other `enqueue_analysis`
    error) is caught and skipped, not raised — a single misbehaving tenant
    must never stop the same tick from firing every other tenant's due
    pipelines. A skipped pipeline's `next_run_at` is deliberately left
    unchanged, so it is retried on the next tick rather than silently
    missing this fire.
    """
    fired: list[str] = []
    skipped: list[str] = []

    for pipeline in list_due_pipelines():
        pipeline_id = pipeline["id"]
        try:
            task_id = enqueue_analysis(
                pipeline["spec"],
                pipeline["business_question"],
                run_dir=RUNS_DIR,
                tenant_id=pipeline["tenant_id"],
            )
        except RateLimitExceededError:
            skipped.append(pipeline_id)
            continue
        except Exception:  # nosec B110 — one bad pipeline must not break the tick
            skipped.append(pipeline_id)
            continue

        next_run_at = compute_next_run_at(pipeline["cron_schedule"])
        # `enqueue_analysis` returns a Celery task id, not a run_id — the
        # actual run_id is only known once `run_full_analysis_task` starts
        # (it's generated inside `run_silver_pipeline`). Recording the task
        # id here still gives the UI something to poll immediately; a future
        # tick's `list_saved_pipelines` read (or the run's own Histórico
        # entry) is the source of truth for the eventual real run_id.
        mark_pipeline_fired(pipeline_id, task_id, next_run_at)
        fired.append(pipeline_id)

    return {"fired": fired, "skipped": skipped}
