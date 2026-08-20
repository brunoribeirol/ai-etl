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

**Concurrency (Sprint 13 code review fix, ADR-016 addendum)**: a tick has no
guarantee it finishes before the next one starts — many due pipelines, a
slow Redis rate-limit check, or worker backlog can all make a tick overrun
`AI_ETL_SCHEDULER_INTERVAL_SECONDS`. `audit.db.claim_due_pipeline` guards
against this with a `next_run_at` compare-and-swap *before* calling
`enqueue_analysis`, not after — exactly one overlapping tick can win the
claim for a given due fire; the loser sees 0 rows affected and skips it
without firing a duplicate run. The claim's replacement `next_run_at` is
also computed from the pipeline's *previous* `next_run_at` (the originally
scheduled time), not `datetime.now()` — otherwise a cron running "every
minute" under sustained load would drift later on every tick, since each
fire would inherit the delay of the one before it.
"""

from __future__ import annotations

from typing import Any

from ai_etl.audit.db import (
    claim_due_pipeline,
    list_due_pipelines,
    record_pipeline_run,
    release_pipeline_claim,
)
from ai_etl.core.celery_app import celery_app
from ai_etl.core.paths import RUNS_DIR
from ai_etl.core.scheduling import InvalidCronScheduleError, compute_next_run_at
from ai_etl.services.execution_queue import RateLimitExceededError, enqueue_analysis


@celery_app.task(name="ai_etl.check_scheduled_pipelines")  # type: ignore[untyped-decorator]
def check_scheduled_pipelines_task() -> dict[str, Any]:
    """Fire every due, active saved pipeline. Returns a small summary dict
    (fired/skipped counts) — useful for beat log inspection, not consumed by
    any caller.

    Per due pipeline: compute the next fire time from its *current*
    `next_run_at` (not `datetime.now()` — see module docstring), then
    atomically claim it (`claim_due_pipeline`). Losing the claim (another
    tick got there first) or any `enqueue_analysis` failure (rate limit, or
    anything else) is caught and skipped, not raised — a single misbehaving
    or overlapping tick must never stop the same run from firing every other
    tenant's due pipelines. A failed `enqueue_analysis` call releases its
    claim (`release_pipeline_claim`) so the pipeline is retried on the very
    next tick rather than silently missing this fire.
    """
    fired: list[str] = []
    skipped: list[str] = []

    for pipeline in list_due_pipelines():
        pipeline_id = pipeline["id"]
        expected_next_run_at = pipeline["next_run_at"]

        try:
            new_next_run_at = compute_next_run_at(pipeline["cron_schedule"], expected_next_run_at)
        except InvalidCronScheduleError:
            skipped.append(pipeline_id)
            continue

        if not claim_due_pipeline(pipeline_id, expected_next_run_at, new_next_run_at):
            # Another (overlapping) tick already claimed this fire — not an
            # error, just lost the race. See module docstring.
            skipped.append(pipeline_id)
            continue

        try:
            task_id = enqueue_analysis(
                pipeline["spec"],
                pipeline["business_question"],
                run_dir=str(RUNS_DIR),
                tenant_id=pipeline["tenant_id"],
            )
        except RateLimitExceededError:
            release_pipeline_claim(pipeline_id, new_next_run_at, expected_next_run_at)
            skipped.append(pipeline_id)
            continue
        except Exception:  # nosec B110 — one bad pipeline must not break the tick
            release_pipeline_claim(pipeline_id, new_next_run_at, expected_next_run_at)
            skipped.append(pipeline_id)
            continue

        # `enqueue_analysis` returns a Celery task id, not a run_id — the
        # actual run_id is only known once `run_full_analysis_task` starts
        # (it's generated inside `run_silver_pipeline`). Recording the task
        # id here still gives the UI something to poll immediately; a future
        # tick's `list_saved_pipelines` read (or the run's own Histórico
        # entry) is the source of truth for the eventual real run_id.
        record_pipeline_run(pipeline_id, task_id)
        fired.append(pipeline_id)

    return {"fired": fired, "skipped": skipped}
