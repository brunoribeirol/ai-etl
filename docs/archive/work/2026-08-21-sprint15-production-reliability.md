# Sprint 15 — Production reliability (work plan)

**Date:** 2026-08-21
**Branch (to create):** `feat/sprint15-production-reliability`
**Companion ADR (to create):** `docs/adr/ADR-020-scheduled-pipeline-reliability.md`
**Roadmap source:** `artefact/product-roadmap-post-tcc.md` — Sprint 15

## 1. Objective

Sprints 13 (scheduling) and 14 (drift/digest) are merged and applied to production, but a
scheduled pipeline can fail — transiently or persistently — with no retry and no operator-facing
signal. `services/scheduler.py`'s own docstring already states the failure-handling contract as
"catch and skip, retried next tick" for *enqueue-time* failures (rate limit, claim loss) only.
Nothing retries or alerts on a failure that happens *during* execution, and nothing tracks a
saved pipeline's health over time.

**Definition of done (roadmap's own words):** a simulated failure (source unavailable, LLM
error) is automatically retried, and if it persists, generates an alert to the operator — never
fails silently.

## 2. Non-goals

- Not building the Sprint 14 drift digest again — this is about pipeline *health* (did the run
  complete successfully, on schedule, in reasonable time), a different concern from Sprint 14's
  *content* comparison (did a KPI move). `services/notifications.py`'s four send functions are
  reused; `services/digest.py`'s business-content formatting is not.
- Not adding a per-tenant "run health" UI page — the roadmap groups a UI surface separately
  (Sprint 18, UI executiva). This sprint's UI footprint (if any) is a minimal addition to the
  existing pipelines list, not a new page.
- Not touching `agents/` — no LangGraph node signature changes. All work is in `services/`,
  `audit/`, and `api/routers/pipelines.py`.
- Not solving the general "any run can fail" problem — this sprint is scoped to *scheduled*
  (saved-pipeline) executions specifically, per the roadmap item and because avulso runs already
  have a human watching the screen when they fail.

## 3. Investigation findings (real, not assumed)

Read before planning, per project convention:

1. **`run_full_analysis_task` (`services/execution_queue.py`) has no Celery retry policy
   today** — no `autoretry_for`, no `max_retries`, no `self.retry(...)` anywhere in the module.
   A raised exception simply marks the task `FAILURE` once.
2. **Most realistic scheduled-pipeline failures never raise an exception the Celery layer could
   retry on.** Confirmed in `agents/extractor.py`: a source-connection failure ("fonte
   indisponível", the roadmap's own example) is caught internally and written to
   `state["error"]`/`status="failed"` — `run_silver_pipeline` returns normally, `save_run`
   persists a `runs` row with `status="failed"`, and `run_full_analysis_task` returns its usual
   JSON-safe summary with `"status": "failed"`. Celery sees this as a **successful task
   execution** (no exception), so a plain `autoretry_for=(Exception,)` would not catch this case
   at all — it only helps genuinely unhandled infra failures (Redis/Postgres connectivity blips,
   an uncaught bug). **This means retry has to be implemented at two separate levels, not one:**
   - Level A — Celery-level `autoretry_for` + backoff, for unhandled exceptions escaping
     `run_full_analysis_task` itself (infra transience).
   - Level B — application-level retry inside/around the task, for a *logical* `status="failed"`
     result with no exception (the more common case per the roadmap's own example).
3. **`saved_pipelines` has no failure-tracking columns.** `last_task_id`/`last_run_at` record
   only that a fire happened, not whether it succeeded. There is no `consecutive_failures` (or
   equivalent) to decide "this has failed enough times to alert the operator," and no
   `last_status`/`last_error` for a health view to read without joining `runs`.
4. **Health/latency data already exists and does not need new instrumentation**: `runs.status`/
   `runs.error` (per execution) and `stage_latencies` (per-stage duration, Sprint 2/ADR-007) are
   already persisted for every run, including scheduled ones (`saved_pipeline_id` FK, Sprint 17).
   A pipeline's success-rate/latency-over-time can be computed by querying `runs` filtered on
   `saved_pipeline_id` — the same join `list_pipeline_run_history` (Sprint 17,
   `audit/db.py:867`) already does for the KPI history view. No new latency instrumentation is
   in scope; this sprint's "métricas" work is aggregation on top of what already exists.
5. **`services/notifications.py`'s four send functions (email/Slack/Teams/Google Chat) are
   generic** — they take pre-formatted subject/text/blocks, not digest-specific data structures.
   They are directly reusable for an operator failure alert; only a new content-builder is
   needed (not new delivery code), same reuse pattern Sprint 14 itself follows internally.
6. **`claim_due_pipeline`/`release_pipeline_claim` (Sprint 13, ADR-016 addendum) already retry
   *enqueue-time* failures** (rate limit, lost claim race) by releasing the claim so the next
   tick picks it up — this sprint must not duplicate or conflict with that existing mechanism;
   it only covers a claim that succeeded and an execution that then failed.

## 4. Affected files (initial set — confirm/expand once implementing)

- `src/ai_etl/services/execution_queue.py` — `run_full_analysis_task` (Celery `autoretry_for` +
  bounded application-level retry on logical failure).
- `src/ai_etl/services/scheduler.py` — may need to thread a retry-attempt count through to the
  task, or leave retry entirely inside the task (decision below).
- `src/ai_etl/audit/models.py` — new nullable columns on `saved_pipelines`
  (`consecutive_failures`, `last_status`, `last_error` — exact names TBD in ADR).
- New migration `00XX_scheduled_pipeline_health.py` (next free number — **check `alembic/versions/`
  immediately before creating**, since 0007-0009 were all claimed in parallel by three sprints
  last session; nothing else in this sprint's plan is known to run in parallel with it right now,
  but confirm no other branch has claimed the next number before opening the PR).
- `src/ai_etl/audit/db.py` — a health-update function (record success/failure + reset/increment
  `consecutive_failures`) and a health-read function (success rate, avg latency, last N run
  statuses) for a given `saved_pipeline_id`.
- New `src/ai_etl/services/health_alerts.py` (or similar name, to keep separate from
  `services/alerting.py`'s drift-specific content) — builds the operator-facing failure alert
  content, calls the existing `notifications.py` senders.
- `src/ai_etl/api/routers/pipelines.py` — expose health fields on `GET /pipelines`/`GET
  /pipelines/{id}` (additive fields on the existing response, no new endpoint needed unless the
  aggregate query is too heavy to run inline — confirm during implementation).
- `frontend/src/components/pipelines-manager.tsx` — minimal additive display of health (e.g., a
  badge: "3 falhas seguidas" / last status), not a new page.
- `docs/adr/ADR-020-scheduled-pipeline-reliability.md` — required before merge, per
  `sr-standard.md` §8 (any SaaS roadmap item needs an ADR).
- Tests: `tests/unit/test_execution_queue.py`, `tests/unit/test_scheduler.py` (or new
  `test_pipeline_health.py`), `tests/unit/test_health_alerts.py`.

## 5. Key open decision — to resolve in the ADR before writing code

**Where does the bounded application-level retry (Level B above) live, and how many attempts?**

Two real options, both consistent with the existing architecture:

- **(a) Retry inside `run_full_analysis_task` itself**, via `self.retry(...)` when the returned
  `state["status"] == "failed"` and a per-invocation attempt counter (Celery's own `self.request.retries`)
  is below a max — same task, same Celery retry machinery Level A would use anyway, one unified
  mechanism instead of two. Countdown/backoff between attempts avoids hammering an unavailable
  source immediately.
- **(b) Retry as a scheduler-level concern** — `check_scheduled_pipelines_task` (or a new
  follow-up tick) re-enqueues a pipeline whose most recent fire resulted in `status="failed"`,
  bounded by `consecutive_failures`. Keeps `run_full_analysis_task` itself simple (no
  retry-awareness), but duplicates some of what Celery's own retry primitives already give for
  free, and mixes "is this pipeline due on its cron schedule" with "does this pipeline need an
  out-of-band retry" in the same function.

**Leaning (a)** — it reuses Celery's existing retry primitives for both levels (unifying A and B
instead of building two different retry code paths), and keeps `check_scheduled_pipelines_task`'s
job exactly what its docstring already says it is (fire due pipelines), not also deciding retry
policy. Final call belongs in the ADR, with the trade-off written out — not decided silently here.

**Alert threshold**: alert after `consecutive_failures` reaches a small configurable threshold
(e.g., `AI_ETL_HEALTH_ALERT_FAILURE_THRESHOLD`, default candidate: 3 — matching the roadmap's
"if it persists" language, and avoiding an alert on one transient blip). Reset
`consecutive_failures` to 0 on any completed (even if logically empty-result) run. Exact default
is an implementation-time judgment call, not a hard requirement — document the choice in the ADR.

## 6. Acceptance criteria (from the roadmap's own "definição de pronto")

1. A simulated transient failure (mock a source-connection error) is retried automatically
   without operator intervention.
2. A simulated persistent failure (fails every retry) results in exactly one alert delivered via
   the existing `notifications.py` channels (best-effort, never raises into the run itself — same
   discipline as `alerting.py`'s drift check).
3. `consecutive_failures` resets to 0 on the next successful fire of the same pipeline.
4. Health fields (success rate, last status, recent latency) are queryable per saved pipeline and
   correct against real inserted `runs` rows.
5. `make check` green (lint, format, mypy --strict, tests, bandit/pip-audit) — no new `bandit`
   findings, no new `# type: ignore` without a comment.
6. New migration verified locally against a real throwaway Postgres (upgrade head, `\d
   saved_pipelines` matches, downgrade -1 clean, re-upgrade clean) — same discipline as every
   prior sprint's migration. **Not applied to production Supabase from this sprint alone** —
   that stays a separate, explicit checkpoint with the owner, same posture as migrations
   0006-0009.

## 7. Validation commands

```bash
make check                 # lint + format-check + type-check + test + security
pytest tests/unit/test_execution_queue.py tests/unit/test_scheduler.py -v
alembic upgrade head        # against local throwaway Postgres
alembic downgrade -1 && alembic upgrade head   # round-trip check
```

## 8. Workstream split

Single coherent workstream (task-level parallelism within this one sprint, per the earlier
discussion in this session — not sprint-level parallelism, since this sprint alone owns
`execution_queue.py`/`scheduler.py`/`saved_pipelines`' migration slot right now):

1. ADR-020 (decide the retry-location question in §5) — do this first, everything else depends
   on the answer.
2. Migration + `audit/models.py`/`audit/db.py` health columns and read/write functions.
3. `execution_queue.py` retry logic (Level A + B, per the ADR's decision) + `health_alerts.py`.
4. `api/routers/pipelines.py` additive health fields + minimal frontend badge.
5. Tests + `make check` + this doc's acceptance criteria, in that order.

One integration owner (this session) — no isolated worktrees for this sprint, since the work is
sequential/dependent, not independent.

## 9. Risks

- If the ADR decision in §5 picks (a) and a scheduled pipeline's source is *persistently* down
  (not transient), retries with backoff still cost Celery worker time and (if the failure occurs
  after an LLM call) real token cost per attempt — bound the max attempts conservatively (e.g. 2-3)
  precisely because of this, not just for latency reasons.
- Migration numbering: confirm the next free Alembic revision immediately before creating the
  file — three sprints collided on `0007` last session working in parallel; this sprint is not
  currently running in parallel with anything else, but re-check `alembic/versions/` right before
  opening the PR in case that has changed.
