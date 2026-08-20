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
import logging
import os
import time
from pathlib import Path
from typing import Any

import redis
from celery.result import AsyncResult
from typing_extensions import TypedDict

from ai_etl.audit.db import get_monthly_budget, get_monthly_spend_usd, get_saved_pipeline
from ai_etl.core.analysis_types import AnalysisRunResult
from ai_etl.core.celery_app import celery_app
from ai_etl.core.state import PipelineState
from ai_etl.services.alerting import check_drift_and_notify
from ai_etl.services.pipeline_service import run_full_analysis

logger = logging.getLogger(__name__)

# Defaults are deliberately generous for a TCC-scale deployment (few tenants,
# manual testing) rather than tuned for real production load — revisit once
# Sprint 6's load/stability testing has real numbers to tune against.
RATE_LIMIT_MAX_RUNS = int(os.getenv("AI_ETL_RATE_LIMIT_MAX_RUNS", "10"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("AI_ETL_RATE_LIMIT_WINDOW_SECONDS", "3600"))

# Sprint 29 (ADR-019) — fraction of a tenant's monthly_budget_usd at which
# check_budget_cap starts warning, before the cap is actually reached.
BUDGET_WARNING_THRESHOLD_RATIO = float(os.getenv("AI_ETL_BUDGET_WARNING_THRESHOLD_RATIO", "0.8"))

# Sprint 29 (ADR-019 addendum) — safety-net TTL on the per-tenant in-flight
# budget lock (see check_budget_cap's docstring). Only matters if a worker
# crashes/is killed after acquiring the lock and before releasing it in
# run_full_analysis_task's `finally` — bounds how long a capped tenant could
# otherwise be stuck unable to enqueue anything. Generous relative to a real
# run's worst-case duration (sandbox timeouts scale up to a few minutes at
# large row counts, ADR-013) without being effectively unbounded.
BUDGET_INFLIGHT_LOCK_TTL_SECONDS = int(os.getenv("AI_ETL_BUDGET_INFLIGHT_LOCK_TTL_SECONDS", "900"))


class RateLimitExceededError(Exception):
    """Raised by `enqueue_analysis` when a tenant is over the cap for the
    current window. Carries no extra fields — the message alone is enough for
    `app.py` to render an `st.error`."""


class BudgetExceededError(Exception):
    """Raised by `enqueue_analysis` when a tenant has a `monthly_budget_usd`
    cap configured and has already spent at or above it this calendar month
    (ADR-019). Never raised for a tenant with no cap configured (`None`)."""


class BudgetStatus(TypedDict):
    """Return shape of `check_budget_cap` — also what `GET /budget` exposes."""

    cap_usd: float | None
    spent_usd: float
    ratio: float | None
    near_limit: bool
    exceeded: bool


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def _budget_inflight_key(tenant_id: str) -> str:
    return f"budget-inflight:{tenant_id}"


def _try_acquire_budget_inflight_lock(tenant_id: str) -> bool:
    """`SET ... NX EX` — Redis's own atomic "set only if absent" primitive,
    the same building block `check_and_increment_rate_limit` already relies
    on for its own atomicity (there, `INCR`; here, `SET NX`). Returns `True`
    if this call won the lock, `False` if another unreconciled execution for
    this tenant already holds it. See `check_budget_cap`'s docstring for why
    this exists."""
    client = _redis_client()
    return bool(
        client.set(
            _budget_inflight_key(tenant_id), "1", nx=True, ex=BUDGET_INFLIGHT_LOCK_TTL_SECONDS
        )
    )


def _release_budget_inflight_lock(tenant_id: str) -> None:
    """Best-effort release — a plain `DELETE` is a no-op if the lock was
    never acquired (uncapped tenant) or already expired, so this is always
    safe to call unconditionally."""
    client = _redis_client()
    client.delete(_budget_inflight_key(tenant_id))


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


def get_budget_status(tenant_id: str) -> BudgetStatus:
    """Read-only: current cap, spend-so-far this calendar month, and whether
    the tenant is near or over it. Never raises — `check_budget_cap` below is
    the enforcement wrapper around this for call sites that must block;
    `GET /budget` uses this directly so status can always be read, even for
    a tenant already over their cap."""
    cap = get_monthly_budget(tenant_id)
    if cap is None:
        return BudgetStatus(
            cap_usd=None, spent_usd=0.0, ratio=None, near_limit=False, exceeded=False
        )

    spent = get_monthly_spend_usd(tenant_id)
    ratio = spent / cap if cap > 0 else float("inf")
    exceeded = spent >= cap
    near_limit = not exceeded and ratio >= BUDGET_WARNING_THRESHOLD_RATIO
    return BudgetStatus(
        cap_usd=cap, spent_usd=spent, ratio=ratio, near_limit=near_limit, exceeded=exceeded
    )


def check_budget_cap(tenant_id: str) -> BudgetStatus:
    """Pre-enqueue budget gate (ADR-019, Sprint 29) — mirrors
    `check_and_increment_rate_limit`'s shape (a cheap, synchronous check
    ahead of `.delay()`, its own exception the API layer maps to an HTTP
    error) but the "count" being checked is real accumulated USD spend for
    the current calendar month, not a request counter.

    **Approach (a), not (b)** (see ADR-019's "Decisão real" section for the
    full trade-off): this checks spend *already accumulated* from completed
    runs (`get_budget_status`, itself `SUM(analysis_runs.cost_usd)` for the
    tenant this month via `audit.db.get_monthly_spend_usd`) against the cap,
    and rejects the *next* execution once the tenant is at or over it. It
    does not estimate this run's own cost ahead of time, so a single run can
    still itself push spend past the cap — flagged as an accepted
    limitation, not fixed here.

    Deliberately queries Postgres directly rather than mirroring spend into
    a second Redis-resident running total (unlike the rate limiter's own
    Redis `INCR`): `analysis_runs.cost_usd` is already the canonical,
    per-run persisted cost this project computes once (Sprint 3) — a
    parallel Redis counter would only add a second value that can drift from
    it (e.g. a task that dies after being enqueued but before `save_analysis`
    runs), for no correctness benefit at this project's scale.

    **Concurrency (ADR-019 addendum, post-PR-#63 code review fix)**: reading
    `spent` from Postgres and comparing it to the cap is not, by itself,
    enough to bound the overshoot to "at most one run" the way ADR-019's
    Decision 3 originally promised. Two `enqueue_analysis` calls arriving
    close together both read the same pre-execution `spent` (neither run has
    completed yet, so neither's cost has landed in `analysis_runs`), both see
    `spent < cap`, and both would pass — N concurrent runs, not one. Fixed by
    reusing the rate limiter's own atomicity primitive (Redis `SET NX`,
    exactly `INCR`'s "one winner" guarantee, applied to a lock instead of a
    counter) rather than inventing a third concurrency pattern in this
    module: once a cap is configured, at most one of this tenant's
    executions may be "in flight and unreconciled" (enqueued but not yet
    priced) at a time — `_try_acquire_budget_inflight_lock` below. A second
    concurrent call for the same capped tenant is rejected here with
    `BudgetExceededError`, not silently queued, until the first execution's
    real cost is persisted and the lock is released (`run_full_analysis_task`'s
    `finally`) or the safety-net TTL expires. This trades some concurrency
    for capped tenants (at most one execution in flight while unreconciled)
    for the overshoot guarantee ADR-019 actually promises; uncapped tenants
    (the default) are entirely unaffected — the lock is only acquired when
    `monthly_budget_usd` is set.

    A Postgres compare-and-swap (`audit.db.claim_due_pipeline`'s pattern,
    Sprint 13) was considered and rejected here: that pattern fits "claim one
    specific row before acting on it," which doesn't map onto "gate access to
    an aggregate `SUM(...)` across many rows" the way a lock does — reusing
    the rate limiter's existing Redis-lock family is the smaller, more
    consistent change for this specific problem.

    Raises `BudgetExceededError` if a cap is configured and either the
    tenant is already at/over it, or another of the tenant's executions is
    still in flight and unreconciled. Otherwise returns the `BudgetStatus`,
    logging a warning (not an audit-log `log_action()` entry — no
    `PipelineState`/`run_id` exists yet at enqueue-time, before a run has
    even started) if spend is near the cap but not yet over it. Caller
    (`enqueue_analysis`) is responsible for releasing the lock this acquires
    if enqueueing then fails before the task ever starts.
    """
    status = get_budget_status(tenant_id)
    if status["near_limit"]:
        logger.warning(
            "Tenant %s is near its monthly budget cap: $%.4f of $%.2f spent (%.0f%%).",
            tenant_id,
            status["spent_usd"],
            status["cap_usd"],
            (status["ratio"] or 0.0) * 100,
        )

    if status["exceeded"]:
        cap = status["cap_usd"]
        raise BudgetExceededError(
            f"Limite de orçamento mensal de US$ {cap:.2f} atingido para esta conta "
            f"(gasto atual: US$ {status['spent_usd']:.4f}). Novas execuções ficarão "
            "bloqueadas até o próximo período ou até o teto ser ajustado."
        )

    if status["cap_usd"] is not None and not _try_acquire_budget_inflight_lock(tenant_id):
        raise BudgetExceededError(
            "Outra execução desta conta ainda está sendo precificada. Aguarde ela terminar "
            "antes de iniciar uma nova, para respeitar o teto de orçamento com segurança."
        )

    return status


@celery_app.task(name="ai_etl.run_full_analysis", bind=True)  # type: ignore[untyped-decorator]
def run_full_analysis_task(
    self: Any,
    spec: str,
    business_question: str,
    run_dir: str,
    tenant_id: str,
    file_path: str | None = None,
    file_bytes_b64: str | None = None,
    saved_pipeline_id: str | None = None,
) -> dict[str, Any]:
    """Celery task wrapping `pipeline_service.run_full_analysis`.

    Sprint 7: live per-node progress, previously out of scope ("no
    `progress_callback` crosses the task boundary" — true, it still doesn't,
    as a Python callable), now uses Celery's own `update_state(meta=...)`
    instead — no new infra (still just the existing Redis result backend).
    `_report_progress` below is a plain closure over `self`, called from
    *inside* the worker process, so it never needs to be serialized itself;
    only the plain `(stage, message)` strings it sends via `update_state` do.
    `get_task_status` surfaces the latest one while state is `"PROGRESS"`.
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

    `saved_pipeline_id` (Sprint 17, ADR-017): forwarded straight through to
    `run_full_analysis`/`save_run`/`save_analysis` so this execution can be
    grouped with the rest of its saved pipeline's run history. `None` for
    every avulso (one-off) `POST /runs` call — only `services/scheduler.py`
    passes a real value. Sprint 14 (ADR-018) also uses it here, after the
    run completes, to run drift detection against the pipeline's previous
    fire and, if triggered, deliver a digest — an avulso run (`None`) never
    runs drift detection at all.
    """
    if file_path and file_bytes_b64:
        dest = Path(file_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(file_bytes_b64))

    def _report_progress(stage: str, message: str) -> None:
        # Best-effort: a Redis hiccup here should never fail the actual
        # analysis over a progress update no one may even be polling for.
        try:
            self.update_state(state="PROGRESS", meta={"stage": stage, "message": message})
        except Exception:  # nosec B110
            pass

    try:
        result = run_full_analysis(
            spec,
            business_question,
            run_dir,
            progress_callback=_report_progress,
            tenant_id=tenant_id,
            saved_pipeline_id=saved_pipeline_id,
        )
    finally:
        # Sprint 29 (ADR-019 addendum): release the per-tenant "in-flight"
        # budget lock `check_budget_cap` may have acquired, regardless of
        # success/failure — the tenant's real cost for this run (if any) is
        # already durably persisted by `run_full_analysis` (`save_analysis`)
        # by the time this runs, so the next `check_budget_cap` call will see
        # it via `get_monthly_spend_usd`. A no-op if no cap was configured
        # (lock never acquired) — see `_release_budget_inflight_lock`. This
        # release must happen before the Sprint 14 drift check below, not
        # just before the return, so a drift-check hiccup can never leave
        # the tenant's budget lock held past this run's own completion.
        _release_budget_inflight_lock(tenant_id)

    state = result["state"]

    if saved_pipeline_id is not None and state.get("status") == "completed":
        _run_drift_check_best_effort(
            saved_pipeline_id, tenant_id, run_dir, business_question, state, result
        )

    return {
        "run_id": state.get("run_id"),
        "status": state.get("status"),
        "error": state.get("error"),
        "tokens": dict(result.get("tokens", {})),
    }


def _run_drift_check_best_effort(
    saved_pipeline_id: str,
    tenant_id: str,
    run_dir: str,
    business_question: str,
    state: PipelineState,
    result: AnalysisRunResult,
) -> None:
    """Fetch the saved pipeline, run drift detection, deliver a digest if
    triggered — all best-effort (Sprint 14, ADR-018 Decision 6). Any
    failure here (a DB hiccup, an unreachable delivery provider) is caught
    and swallowed: the run itself already completed and was persisted above,
    an alerting side-effect must never retroactively fail it.
    """
    try:
        pipeline = get_saved_pipeline(saved_pipeline_id, tenant_id)
        if pipeline is None or not pipeline.get("is_active"):
            return
        load_result = state.get("load_result") or {}
        check_drift_and_notify(
            pipeline=pipeline,
            run_id=state.get("run_id", ""),
            rows_loaded=load_result.get("rows_loaded"),
            tokens=result.get("tokens", {}),
            science_results=result.get("science", []),
            advisor_result=result.get("advisor", {}),
            business_question=business_question,
            log_dir=run_dir,
        )
    except Exception:  # nosec B110 — best-effort alerting, never fails the run
        pass


def enqueue_analysis(
    spec: str,
    business_question: str,
    run_dir: str,
    tenant_id: str,
    file_path: str | None = None,
    file_bytes: bytes | None = None,
    saved_pipeline_id: str | None = None,
) -> str:
    """Enforce the tenant's budget cap and rate limit, then enqueue the run.

    Raises `BudgetExceededError` (ADR-019, Sprint 29) before touching Celery
    or the rate limiter at all if the tenant has a `monthly_budget_usd` cap
    configured and has already spent at or above it this calendar month, or
    if another of the tenant's executions is still in flight and
    unreconciled (see `check_budget_cap`'s docstring for the concurrency
    guard). **Checked first, before the rate limit** (post-PR-#63 code
    review fix) — a request rejected for being over budget must not still
    consume a rate-limit slot; the tenant would otherwise be locked out of
    legitimate calls for the rest of the rate-limit window even after fixing
    their budget, for a run that never actually executed. Raises
    `RateLimitExceededError` the same way if the tenant is over the rate-limit
    cap. Either way, a rejected run never occupies a queue slot.

    `file_path`/`file_bytes`: pass both when `spec` references an uploaded
    file (`app.py`'s upload flow) so the worker — a separate process/
    container with its own filesystem — can re-materialize it before running;
    see `run_full_analysis_task`'s docstring. `file_bytes` is base64-encoded
    here, not by the caller, so callers just pass the raw bytes they already
    have (e.g. `UploadedFile.getvalue()`).

    `saved_pipeline_id` (Sprint 17, ADR-017): pass the `saved_pipelines.id`
    when this call is a scheduled fire (`services/scheduler.py`), so the
    resulting `runs`/`analysis_runs` rows are linked back to it. `None` (the
    default) for every avulso call, e.g. `POST /runs`. Sprint 14 (ADR-018)
    also relies on this to gate its drift check — see
    `run_full_analysis_task`'s docstring.

    Returns the Celery task id `app.py` stores in `st.session_state` and
    polls via `get_task_status`.
    """
    status = check_budget_cap(tenant_id)
    budget_lock_held = status["cap_usd"] is not None
    try:
        check_and_increment_rate_limit(tenant_id)
        file_bytes_b64 = base64.b64encode(file_bytes).decode("ascii") if file_bytes else None
        task = run_full_analysis_task.delay(
            spec,
            business_question,
            run_dir,
            tenant_id,
            file_path,
            file_bytes_b64,
            saved_pipeline_id,
        )
    except Exception:
        # The run never actually started (rate-limited, or the broker call
        # itself failed) — release the in-flight budget lock `check_budget_cap`
        # just acquired so it isn't held for BUDGET_INFLIGHT_LOCK_TTL_SECONDS
        # over a run that will never happen. Once `.delay()` succeeds, the
        # lock's release is `run_full_analysis_task`'s responsibility instead
        # (it's now held for the run's real, in-progress duration).
        if budget_lock_held:
            _release_budget_inflight_lock(tenant_id)
        raise
    return str(task.id)


def get_task_status(task_id: str) -> dict[str, Any]:
    """Poll a previously-enqueued task's status.

    `state` is one of Celery's own values (`PENDING`, `STARTED`, `SUCCESS`,
    `FAILURE`, ...) plus the custom `"PROGRESS"` state `run_full_analysis_task`
    reports via `update_state` (Sprint 7) — `app.py` used to map these to UI
    copy rather than this module inventing a parallel vocabulary; the
    frontend now does the same. `result` is the task's JSON-safe summary dict
    on success; `error` is the stringified exception on failure; `meta` is
    the latest `{"stage", "message"}` progress update while `state ==
    "PROGRESS"`, `None` otherwise (`result.info` holds the raw exception
    object on failure, not something JSON-safe to hand back as-is).
    """
    result = AsyncResult(task_id, app=celery_app)
    return {
        "state": result.state,
        "ready": result.ready(),
        "result": result.result if result.successful() else None,
        "error": str(result.result) if result.failed() else None,
        "meta": result.info if result.state == "PROGRESS" else None,
    }
