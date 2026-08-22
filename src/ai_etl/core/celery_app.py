"""Celery application factory (ADR-008).

Single call site for the Celery app instance — `services/execution_queue.py`
(and any future task module) import `celery_app` from here rather than
constructing their own, so task registration and broker/backend config stay
in one place. Mirrors `audit/connection.py`'s "one function, one env var,
fail loud if unset" shape for the same reason: a silently-defaulted broker
URL would make a worker connect to the wrong Redis without any error.

Broker and result backend both point at the same Redis instance (`REDIS_URL`)
— there's no need for a separate result store at this project's scale, and
splitting them would be one more moving part with no offsetting benefit here.

Unlike `connection.py`'s `get_engine()` (raises if `APP_DATABASE_URL` is
unset), a missing `REDIS_URL` falls back to the local `docker-compose` default
instead of raising. `Celery(...)` doesn't connect eagerly — the constructor
just stores config — so failing loudly here would only punish importing this
module (e.g. from `services/execution_queue.py`, itself imported by `app.py`)
in any environment without Redis configured, including plain unit-test
collection. A real connection failure still surfaces loudly, just later, at
the point a task is actually enqueued or a worker actually starts.
"""

import os

from celery import Celery
from celery.schedules import schedule

from ai_etl.core.logging_config import configure_logging
from ai_etl.core.observability import init_sentry

# Sprint 34 (ADR-033) — module-scope, same "runs once at import time" shape
# as `api/main.py`'s bootstrap: `celery_app.py` is the single import site
# every worker/beat process (`celery -A ai_etl.core.celery_app worker`)
# loads before executing any task, so this covers both without a separate
# entrypoint. `CeleryIntegration` (inside `init_sentry`) instruments task
# execution automatically from here on — no per-task code change needed.
configure_logging()
init_sentry(component="worker")


def _redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


# Sprint 13 (ADR-016) — how often the beat process checks for due saved
# pipelines. A plain interval (`schedule(seconds=...)`), not a crontab entry
# itself: each *saved pipeline* has its own cron schedule, evaluated inside
# `check_scheduled_pipelines_task`; this is just the polling cadence of that
# check. Configurable so tests/local verification can use a short interval
# (see the sprint's "3 consecutive fires" definition of done) without a
# production redeploy needing a code change.
SCHEDULER_INTERVAL_SECONDS = int(os.getenv("AI_ETL_SCHEDULER_INTERVAL_SECONDS", "60"))

celery_app = Celery("ai_etl", broker=_redis_url(), backend=_redis_url())
celery_app.conf.update(
    # `run_full_analysis_task` lives in `services/execution_queue.py`, not
    # here — a plain `celery -A ai_etl.core.celery_app worker` never imports
    # that module on its own, so the task would register only on the web
    # process (which imports `execution_queue` transitively via `app.py`)
    # and the worker would reject every enqueued run as "unregistered task".
    # `include` makes the worker import it at startup too, independent of
    # whatever CLI flags the actual `celery worker` invocation happens to use.
    # `services.scheduler` (Sprint 13) is included the same way, for the
    # same reason — the beat process only *schedules* the task by name; a
    # worker still needs to have imported it to execute it.
    include=["ai_etl.services.execution_queue", "ai_etl.services.scheduler"],
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Task return values must stay JSON-safe (see execution_queue.py's task —
    # it returns a small summary dict, never DataFrames/Figures). Enforcing
    # the json serializer (over pickle) means a task that ever tries to
    # return something unpicklable-unsafe fails loudly at serialization time
    # instead of introducing a deserialization-of-untrusted-data risk on the
    # worker side.
    result_expires=3600,
    task_track_started=True,
    # Sprint 13 (ADR-016) — requires a separate `celery -A
    # ai_etl.core.celery_app beat` process running in production (same image
    # as the existing worker, different Custom Start Command) to actually
    # tick; the web/API process and the plain worker process don't run beat
    # themselves. See ADR-016 Decision 2.
    beat_schedule={
        "check-scheduled-pipelines": {
            "task": "ai_etl.check_scheduled_pipelines",
            "schedule": schedule(run_every=SCHEDULER_INTERVAL_SECONDS),
        },
    },
)
