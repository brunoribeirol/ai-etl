"""Asynchronous execution queue (Sprint 3, ADR-008).

Takes `pipeline_service.run_full_analysis` off Streamlit's blocking click
handler by running it inside a Celery task, and enforces a per-tenant rate
limit backed by the same Redis instance Celery already uses as its broker.

**Design decision — rate limiting diverges from ADR-008's initial framing.**
ADR-008 suggested reusing "Celery's native rate limiting." Celery's own
`rate_limit` (set via `@task(rate_limit=...)` or `CELERY_TASK_ANNOTATIONS`)
throttles a *task type* globally across all workers — e.g. "no more than N
`run_full_analysis_task` executions per minute, total" — it has no concept of
a per-call argument like `tenant_id`. What Sprint 3 actually needs is "no more
than N runs per *tenant* per window," which Celery's rate_limit cannot express
on its own. Rather than force-fit it (e.g. one dynamically-registered task per
tenant, which Celery does not support cleanly), this module adds a small,
explicit fixed-window counter directly on Redis (`INCR` + `EXPIRE`) ahead of
enqueueing. This still satisfies ADR-008's spirit (reuse the same Redis
instance, no second piece of infra) while actually being per-tenant. Flagged
for Tech Lead review — this is the one place implementation diverged from the
ADR's sketch.

**Design decision — Celery task returns a summary, not the full result.**
`run_full_analysis`'s return value carries `pd.DataFrame`s and Plotly
`Figure` objects, which are not JSON-serializable (and this project
deliberately keeps Celery's serializer as `json`, not `pickle` — see
`core/celery_app.py`'s docstring on why). Rather than relax that, the task
returns a small JSON-safe summary (`run_id`, `status`, `error`, `tokens`).
The full result is already durably persisted as a side effect of
`run_full_analysis` itself (`save_run`/`save_analysis`, unchanged) — exactly
the same JSON-on-disk-plus-DB-row path `app.py`'s History tab already reads
from for past runs. `app.py` polls this module's `get_task_status`, and once
`SUCCESS`, re-loads the full result the same way the History tab does, rather
than this module inventing a second, redundant transport for the same data.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

import redis
from celery.result import AsyncResult

from ai_etl.core.celery_app import celery_app
from ai_etl.services.pipeline_service import run_full_analysis

# Defaults are deliberately generous for a TCC-scale deployment (few tenants,
# manual testing) rather than tuned for real production load — revisit once
# Sprint 6's load/stability testing has real numbers to tune against.
RATE_LIMIT_MAX_RUNS = int(os.getenv("AI_ETL_RATE_LIMIT_MAX_RUNS", "10"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("AI_ETL_RATE_LIMIT_WINDOW_SECONDS", "3600"))


class RateLimitExceededError(Exception):
    """Raised by `enqueue_analysis` when a tenant is over the cap for the
    current window. Carries no extra fields — the message alone is enough for
    `app.py` to render an `st.error`."""


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def check_and_increment_rate_limit(tenant_id: str) -> None:
    """Fixed-window per-tenant counter on Redis.

    Key is `ratelimit:{tenant_id}:{window_index}`, where `window_index` is
    `int(time.time()) // RATE_LIMIT_WINDOW_SECONDS` — every tenant's counter
    resets at the same wall-clock boundaries (not a sliding window keyed off
    each tenant's first call). Simpler than a sliding-window log, and
    "resets on a shared clock boundary rather than per-tenant" is an accepted
    trade-off at this project's scale — a tenant could in principle burst up
    to `2x` the cap across a window boundary. Good enough to stop runaway
    /adversarial usage; not meant to be billing-grade precise.

    Raises `RateLimitExceededError` without incrementing further once the tenant
    is already over the cap (the raising call itself still counts, matching
    "the Nth call is now over the limit" semantics rather than "the N+1th
    call is silently free").
    """
    client = _redis_client()
    window_index = int(time.time()) // RATE_LIMIT_WINDOW_SECONDS
    key = f"ratelimit:{tenant_id}:{window_index}"

    count = client.incr(key)
    if count == 1:
        # Only the call that creates the key sets its TTL — re-setting it on
        # every INCR would keep pushing the window out indefinitely under
        # sustained traffic, defeating the fixed-window design.
        client.expire(key, RATE_LIMIT_WINDOW_SECONDS)

    if count > RATE_LIMIT_MAX_RUNS:
        raise RateLimitExceededError(
            f"Limite de {RATE_LIMIT_MAX_RUNS} execuções por "
            f"{RATE_LIMIT_WINDOW_SECONDS // 60} minutos atingido para esta conta. "
            "Tente novamente mais tarde."
        )


@celery_app.task(name="ai_etl.run_full_analysis", bind=True)  # type: ignore[untyped-decorator]
def run_full_analysis_task(
    self: Any,
    spec: str,
    business_question: str,
    run_dir: str,
    tenant_id: str,
    file_path: str | None = None,
    file_bytes_b64: str | None = None,
) -> dict[str, Any]:
    """Celery task wrapping `pipeline_service.run_full_analysis`.

    No `progress_callback` crosses the task boundary — it's a plain Python
    callable, not something Celery's JSON serializer can carry, and live
    per-node progress streaming from inside a worker back to a polling
    Streamlit session is out of scope for this sprint (`app.py` shows a
    coarse "queued / running / done" state instead — see `get_task_status`).
    Returns a JSON-safe summary; see this module's docstring for why the full
    result isn't returned here.

    **`file_path`/`file_bytes_b64` — interim fix, not the final design.**
    `spec` embeds an uploaded file's path as plain text (see `app.py`'s
    `_auto_generate_spec`), but the web process and this worker are separate
    Railway services with separate filesystems — a path that exists on the
    web container's disk does not exist here. Rather than assume shared
    storage, the web process base64-encodes the uploaded file's bytes and
    passes them through the task itself; this worker re-materializes the
    exact file at `file_path` on its own disk, in the same directory shape
    (`runs/uploads/...`) both containers already agree on (same relative
    `WORKDIR`), before ever calling `run_full_analysis`. Both are `None` when
    `spec` came from the manual-spec textarea (no upload to move). This is a
    deliberate, scoped Sprint 3 fix — Sprint 4 (ADR-009, tenant-scoped S3
    storage) replaces it with a real shared store; base64-in-task-payload
    does not scale past small demo-sized files and isn't meant to.
    """
    if file_path and file_bytes_b64:
        dest = Path(file_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(file_bytes_b64))

    result = run_full_analysis(spec, business_question, run_dir, tenant_id=tenant_id)
    state = result["state"]
    return {
        "run_id": state.get("run_id"),
        "status": state.get("status"),
        "error": state.get("error"),
        "tokens": dict(result.get("tokens", {})),
    }


def enqueue_analysis(
    spec: str,
    business_question: str,
    run_dir: str,
    tenant_id: str,
    file_path: str | None = None,
    file_bytes: bytes | None = None,
) -> str:
    """Enforce the tenant's rate limit, then enqueue the run.

    Raises `RateLimitExceededError` before touching Celery at all if the tenant is
    already over the cap — a rejected run should never occupy a queue slot.

    `file_path`/`file_bytes`: pass both when `spec` references an uploaded
    file (`app.py`'s upload flow) so the worker — a separate process/
    container with its own filesystem — can re-materialize it before running;
    see `run_full_analysis_task`'s docstring. `file_bytes` is base64-encoded
    here, not by the caller, so callers just pass the raw bytes they already
    have (e.g. `UploadedFile.getvalue()`).

    Returns the Celery task id `app.py` stores in `st.session_state` and
    polls via `get_task_status`.
    """
    check_and_increment_rate_limit(tenant_id)
    file_bytes_b64 = base64.b64encode(file_bytes).decode("ascii") if file_bytes else None
    task = run_full_analysis_task.delay(
        spec, business_question, run_dir, tenant_id, file_path, file_bytes_b64
    )
    return str(task.id)


def get_task_status(task_id: str) -> dict[str, Any]:
    """Poll a previously-enqueued task's status.

    `state` is one of Celery's own values (`PENDING`, `STARTED`, `SUCCESS`,
    `FAILURE`, ...) — `app.py` maps these to UI copy rather than this module
    inventing a parallel vocabulary. `result` is the task's JSON-safe summary
    dict on success; `error` is the stringified exception on failure.
    """
    result = AsyncResult(task_id, app=celery_app)
    return {
        "state": result.state,
        "ready": result.ready(),
        "result": result.result if result.successful() else None,
        "error": str(result.result) if result.failed() else None,
    }
