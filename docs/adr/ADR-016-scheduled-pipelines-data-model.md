# ADR-016: Scheduled pipelines — data model and "same source" strategy

**Status:** Proposed (checkpoint — not merged, see PR)
**Date:** 2026-08-19
**Context:** Sprint 13 (product roadmap post-TCC, `product-roadmap-post-tcc.md`)

## Context

Every run today (`runs` table, `POST /runs`) is one isolated event: a `spec`
(natural-language pipeline description) plus an optional `business_question`,
executed once by Celery, producing one `run_id`. There is no concept of "the
same pipeline, run again later."

Recurring execution is the feature that turns AI-ETL from "a tool you run by
hand" into something that justifies subscription billing: a customer
configures a pipeline once and expects it to keep running unattended. This
requires a new persisted entity — a *saved pipeline* (spec + schedule +
tenant + on/off state) — reexecuted by Celery beat on a cron schedule,
independent from the existing avulso (one-off) `POST /runs` flow, which is
unchanged.

## Decision 1 — new `saved_pipelines` table, `POST /runs` untouched

Add `saved_pipelines` (SQLAlchemy Core, same style as `runs`/`analysis_runs`
in `audit/models.py`): `id`, `tenant_id` (FK `users.id`), `name`, `spec`,
`business_question`, `cron_schedule`, `is_active`, `last_task_id`,
`last_run_at`, `next_run_at`, `created_at`, `updated_at`. `next_run_at` is
precomputed (via `croniter`) and indexed alongside `is_active` so the beat
task's due-query is a cheap indexed range scan, not a per-row cron evaluation
against every saved pipeline on every tick.

A `saved_pipeline` produces `runs` rows exactly the way `POST /runs` does —
`run_full_analysis_task` is reused unchanged (Decision 2) — so a scheduled
run is audited identically to an avulso one; nothing downstream (`audit/db.py`,
Histórico, cost tracking, stage latencies) needs to know a run was scheduled
rather than manually triggered. `saved_pipelines.last_task_id` is the only new
link, purely for the UI to show "last run" without a join.

## Decision 2 — reuse the existing Celery/Redis queue, add one beat entry

No new infrastructure. `core/celery_app.py` gets `beat_schedule` with a single
periodic entry (`check-scheduled-pipelines`, interval configurable via
`AI_ETL_SCHEDULER_INTERVAL_SECONDS`, default 60s) that calls a new task,
`services/scheduler.py::check_scheduled_pipelines_task`. That task queries
`saved_pipelines` for `is_active AND next_run_at <= now()`, and for each due
row calls the *existing* `execution_queue.enqueue_analysis()` — the same
function `POST /runs` calls — then advances `next_run_at` via
`croniter.get_next()` and records `last_task_id`/`last_run_at`.

This means: one new Celery beat process (`celery -A ai_etl.core.celery_app
beat`) needs to run in production alongside the existing worker — no new
broker, no new result backend, no new Railway service *type* (a `celery beat`
process is the same Docker image with a different Custom Start Command,
exactly like the existing worker service already is). A tenant's per-window
rate limit (`RATE_LIMIT_MAX_RUNS`/`RATE_LIMIT_WINDOW_SECONDS`, unchanged) still
applies to scheduled runs, since they go through the same `enqueue_analysis`
gate — a runaway or misconfigured cron schedule cannot bypass the cap. A
`RateLimitExceededError` for one tenant's due pipeline is caught and logged
per-pipeline inside the beat task, not raised — one tenant being over their
cap must never stop the same tick from firing every other tenant's due
pipelines.

`croniter` is added as a new dependency (pure-Python, no system dependency —
same bar `pymysql`/`pymongo` were added under in Sprint 11) rather than
hand-rolling cron parsing/next-fire-time math, which has enough real edge
cases (month-end, DST, leap years) to not be worth reimplementing.

## Decision 3 — how "the source stays the same" between scheduled runs

The roadmap names three options:

**(a) Fixed path** — the spec keeps referencing a file path
(`runs/uploads/...`) that must still exist on disk at every future scheduled
fire. Rejected: it silently couples a saved pipeline's correctness to a
file surviving indefinitely on whichever container happened to receive the
original upload — this project already moved off exactly that model in
Sprint 4 (ADR-009) for the *single-run* case, because "hope the file is still
there" doesn't survive multiple deploys/containers. Reintroducing it for
recurring execution, where the gap between runs can be days, is worse, not
better.

**(b) Mandatory re-upload every run** — defeats the entire point of
scheduling: "log in and re-attach a file before every scheduled fire" is not
unattended execution, it's a recurring manual chore with a calendar reminder.
Rejected outright for v1.

**(c) Scheduling only for "live" sources — chosen for v1.** A saved
pipeline's `spec` may only describe source types the Orchestrator can
re-resolve from connection info alone, with no dependency on a file that
exists once and never again: `postgres`, `sqlite`, `mysql`, `mongodb`, `rest`
(all already supported connectors — Sprint 11, ADR-012). `csv`/`document`
(PDF/DOCX) — anything that originates from a browser upload — is rejected at
`POST /pipelines` creation/update time with a 400, not silently accepted and
left to fail at the next scheduled fire.

This is enforced by requiring `POST /pipelines`/`PATCH /pipelines/{id}` to
declare `source_type` explicitly (the frontend renders it as a dropdown, not
a free-text field), validated server-side against the allowlist
(`{"postgres", "sqlite", "mysql", "mongodb", "rest"}`) before the row is ever
written. Deliberately **not** implemented by round-tripping the spec through
the Orchestrator's LLM at save time: that would add a real LLM call (cost,
latency, and a hard dependency on `OPENAI_API_KEY`/whichever provider being
configured) to a plain CRUD request, and an LLM-inferred source type is
exactly the kind of check that should be deterministic, not probabilistic,
for something enforcing a hard allowlist. `source_type` is stored only for
this validation gate — the Orchestrator still re-derives the *actual*
structured plan from `spec` at execution time, unchanged from how an avulso
run already works; `source_type` is not threaded any further into the
pipeline. **Chosen because it needs zero new infrastructure, no LLM
dependency in the CRUD path, and is the cleanest way to keep the blast
radius of Sprint 13 to the scheduling data model itself** rather than also
inventing a durable-uploaded-file-store policy in the same PR.

(a) and (b) are recorded here as explicitly out of scope for this round, not
forgotten — a future sprint could revisit (a) once/if a durable per-tenant
upload store (something like a "registered file source" pointing at S3,
ADR-009's storage backend, refreshed independently of any single run) exists;
that is a materially bigger feature than this sprint's scope.

## Consequences

- `POST /runs` (avulso) is completely unchanged — still accepts any source
  type, including uploads. Scheduling is strictly additive.
- Sprints 14/15/17 (named in the roadmap as depending on this data model)
  inherit `saved_pipelines` as-is; any change to its shape after this point
  should get its own ADR rather than silently drifting the schema other
  sprints already built against.
- A saved pipeline's `spec` is validated once, at creation/update time, not
  re-validated on every scheduled fire — if the underlying live source
  becomes unreachable between runs (e.g. a customer's Postgres credentials
  rotate), that surfaces as a normal `runs.status = "failed"` row, exactly
  the same failure mode an avulso run against a broken source already has.
  No new error-handling path was invented for this.
- Billing (Stripe, roadmap Sprint v1.0) is still not implemented — this ADR
  only makes recurring *execution* possible; charging per saved pipeline or
  per scheduled run is a separate, later decision.
