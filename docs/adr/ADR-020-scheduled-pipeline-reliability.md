# ADR-020: Scheduled Pipeline Reliability — Retry and Failure Alerting

**Status:** Proposed (design decision — implementation follows in this same sprint)
**Date:** 2026-08-21
**Sprint:** 15 (post-TCC product roadmap)

## Context

Sprint 13 (ADR-016, scheduling) and Sprint 14 (ADR-018, drift/digest) are merged and applied
to production. Neither addresses what happens when a scheduled fire *fails* — `services/
scheduler.py`'s own docstring already states its failure contract precisely: an
`enqueue_analysis` failure (rate limit, lost claim race) is "caught and skipped ... retried
next tick." That contract stops at enqueue time. Nothing retries or alerts on a failure that
happens *during* execution, and `saved_pipelines` tracks no failure history at all
(`last_task_id`/`last_run_at` record only that a fire happened, not whether it succeeded).

Roadmap definition of done (`artefact/product-roadmap-post-tcc.md`, Sprint 15): a simulated
failure (source unavailable, LLM error) is retried automatically, and if it persists, alerts
the operator — never fails silently.

**Investigated first, per this project's own standard, before deciding anything** (full
write-up in `docs/work/2026-08-21-sprint15-production-reliability.md`):

1. `run_full_analysis_task` (`services/execution_queue.py`) has no Celery retry policy today —
   no `autoretry_for`, no `max_retries`, no `self.retry(...)` anywhere in the module.
2. **The realistic failure the roadmap names — "fonte indisponível" — does not raise an
   exception Celery could retry on.** Confirmed in `agents/extractor.py`: a source-connection
   failure is caught internally and written to `state["error"]`/`status="failed"`;
   `run_full_analysis_task` returns its normal JSON-safe summary with `"status": "failed"`,
   no exception. Celery records this as a **successful** task execution. A plain
   `autoretry_for=(Exception,)` would therefore miss the roadmap's own example entirely — it
   only helps genuinely unhandled infra failures (a Redis/Postgres connectivity blip, an
   uncaught bug), a narrower and less common case in practice.
3. `runs.status`/`runs.error` (per execution) and `stage_latencies` (per-stage duration,
   ADR-007) are already persisted for every run, including scheduled ones (`saved_pipeline_id`
   FK, ADR-017) — health/latency metrics need aggregation, not new instrumentation.
4. `services/notifications.py`'s four send functions (email/Slack/Teams/Google Chat) are
   generic — reusable for an operator alert without new delivery code, the same reuse pattern
   `services/alerting.py` (ADR-018) already follows for the drift digest.
5. `claim_due_pipeline`/`release_pipeline_claim` (ADR-016 addendum) already retry *enqueue-time*
   failures by releasing the claim so the next tick picks it up. This ADR must not duplicate
   that — it covers only a failure *during* execution, after enqueueing already succeeded.

## Decision 1 — Retry lives inside `run_full_analysis_task`, via Celery's own `self.retry`, unifying both failure classes

Two failure classes need retry, and this ADR puts both through the same mechanism:

- **Level A — unhandled exception** escaping `run_full_analysis_task` (infra transience: a
  DB/Redis blip, an uncaught bug). Handled by Celery's own `autoretry_for`/`retry_backoff`.
- **Level B — logical failure**, `state["status"] == "failed"` returned with no exception (the
  roadmap's own example: source unavailable). The task itself checks this after
  `run_full_analysis` returns and, when the fire came from a saved pipeline
  (`saved_pipeline_id is not None`) and `self.request.retries` is below a configured max,
  calls `self.retry(countdown=...)` manually to force another attempt through the exact same
  Celery retry machinery Level A already uses.

**Alternative considered and rejected: retry as a `scheduler.py`/`check_scheduled_pipelines_task`
concern** (re-enqueue a pipeline whose last fire failed, gated by a `consecutive_failures`
counter checked on the next tick). Rejected because:
- It duplicates retry/backoff bookkeeping (attempt counting, delay-between-attempts) that
  Celery's own `self.request.retries` and `countdown` already give for free — two different
  retry code paths (one per failure class) instead of one.
- It mixes two different questions inside one function: "is this pipeline due on its cron
  schedule" (what `check_scheduled_pipelines_task`'s own docstring already says its job is)
  and "does this specific fire need an out-of-band retry" — a second concern that function was
  never designed to carry.
- A tick-driven retry is bounded by `AI_ETL_SCHEDULER_INTERVAL_SECONDS` (default 60s) at best —
  coarser and less controllable than a task-level `countdown`.

**Why not retry at the task level only for Level A, and leave Level B for the scheduler to
notice?** Because that is exactly today's status quo minus nothing — the roadmap's own worked
example (source unavailable) is Level B, so a fix that only covers Level A would not meet the
sprint's stated definition of done.

**Avulso (one-off, non-scheduled) runs are excluded from Level B retry** — `run_full_analysis_task`
only applies the manual `self.retry()` on logical failure when `saved_pipeline_id is not None`.
A human submitting `POST /runs` and watching the result is already the "retry" in that flow
(they see the failure and can resubmit); auto-retrying behind their back would silently re-run
LLM calls (real cost) for a request they may not want repeated. Level A (`autoretry_for`)
still applies to every run regardless of origin — an unhandled infra exception is never a
deliberate user action to second-guess.

**Bounded attempts, not unbounded**: max **2 retries** (3 total attempts) — deliberately
conservative, not just for latency. Each retry of an LLM-calling pipeline costs real tokens
(Sprint 3/8's own measured numbers: ~$0.0006/run at this project's current scale) —
retrying a persistently-broken source 5+ times would multiply that cost for a fire that was
never going to succeed. `countdown` backs off between attempts (candidate: 60s, 180s — doubling,
capped) so a transient blip (a source flaking for a few seconds) has a real chance to clear
without hammering it immediately.

## Decision 2 — Failure tracking: three new columns on `saved_pipelines`, migration `0010`

- `consecutive_failures` (`Integer`, `nullable=False`, `server_default="0"`) — increments on
  every fire whose *final* attempt (after retries are exhausted) still has `status != "completed"`;
  resets to `0` on any fire that completes successfully. Backfills every existing row to `0`.
- `last_status` (`String(20)`, nullable) — the outcome of the most recent fire's final attempt
  (`"completed"` / `"failed"`), read directly by a health view without joining `runs`.
- `last_error` (`Text`, nullable) — the most recent failure's error message, for the same
  no-join reason; cleared (`NULL`) on a successful fire.

**Why on `saved_pipelines`, not a new table:** the same reasoning ADR-019 already used for
`users.monthly_budget_usd` applies here — this is live, mutable, single-current-value state
about one saved pipeline, not a history needing its own versioned rows. Full failure history
(what failed, when, with what error) already exists in `runs`/`runs.error`, queryable via the
existing `saved_pipeline_id` FK (ADR-017) — these three columns are a fast-read cache of "the
current health snapshot," not a second source of truth for failure history.

**Alternative considered:** compute `consecutive_failures` on read, by querying `runs` ordered
by timestamp and counting from the most recent backwards. Rejected: it turns a cheap read
(`GET /pipelines`, listing many saved pipelines) into an `N+1` aggregate query per pipeline,
for a value that changes at a predictable point (once per fire) and is naturally write-once
per fire — a stored counter updated at that one point is simpler and cheaper than a query-time
aggregate recomputed on every list request.

## Decision 3 — Alert threshold and delivery

Alert the operator when `consecutive_failures` reaches `AI_ETL_HEALTH_ALERT_FAILURE_THRESHOLD`
(env-configurable, default **3**) — i.e., after retries are already exhausted on the third
consecutive failed fire, not on the first transient blip (Level B retry already absorbs a
single flake; this threshold is about a fire that keeps failing across separate scheduled
occurrences, not separate retries of the same occurrence). Alert exactly once per crossing
(fires again only if the count drops to 0 via a success and then climbs back to the threshold
— never re-alerts on every failure past 3, to avoid alert fatigue for a pipeline that's simply
broken and already known about).

**Delivery reuses `services/notifications.py`'s existing four send functions** (email/Slack/
Teams/Google Chat) directly — no new delivery code. **Content is built by a new module,
`services/health_alerts.py`**, kept separate from `services/digest.py`: Sprint 14's digest is
about pipeline *content* (a KPI moved); this is about pipeline *health* (did it run at all).
Conflating the two would mean a customer-facing digest template growing operator-facing
"your pipeline is broken" language, or vice versa — different audiences, different tone,
same delivery mechanism underneath.

Same best-effort discipline as `services/alerting.py`: wrapped in try/except, a delivery
failure never fails the run itself (already-established pattern, not a new one this ADR
invents).

## Decision 4 — Health/metrics surface: additive fields, no new endpoint

Success rate and average latency for a saved pipeline are computed from `runs`
(`status`/`saved_pipeline_id`) and `stage_latencies` — data that already exists. Exposed as
additive fields on the existing `GET /pipelines`/`GET /pipelines/{id}` response (alongside
`last_status`/`last_error`/`consecutive_failures`), not a new endpoint — same posture as
ADR-017's `GET /pipelines/{id}/history`, which already established the pattern of adding a
read surface onto the existing `saved_pipelines` resource rather than inventing a parallel
one. A dedicated health *page* is explicitly out of scope (Sprint 18, UI executiva, per the
roadmap) — this sprint's frontend footprint is a minimal badge on the existing
`pipelines-manager.tsx` list, not a new route.

## Consequences

- A scheduled pipeline whose source is down gets up to 3 real attempts (with backoff) before
  being marked failed for that fire — bounded LLM cost exposure per fire (at most 3x a single
  attempt's cost), not unbounded.
- After 3 consecutive *fires* (not retries) fail, the operator gets exactly one alert via
  whatever channels Sprint 14 already has configured (Resend/Slack/Teams/Google Chat) —
  reuses existing configuration, no new setup required if Sprint 14's channels are already
  live.
- `consecutive_failures`/`last_status`/`last_error` are a derived cache, not a new source of
  truth — if they ever drift from `runs`' real history (a bug), the fix is recomputing them
  from `runs`, never trusting them over the underlying data.
- **Known limitation, explicitly out of scope:** this ADR does not distinguish *why* a fire
  failed for retry-decision purposes (e.g., retrying a `LLM error` the same way as a `source
  unavailable` error) — both are treated identically by Level B's retry logic. A future sprint
  could differentiate (e.g., don't retry a clearly non-transient validation error), but that
  needs a taxonomy of failure causes this sprint does not build.
- **Known limitation:** avulso (one-off) runs get Level A retry only, never Level B — a human
  watching `POST /runs` fail is expected to decide whether to resubmit, not to have it
  auto-retried behind them. If a future sprint wants opt-in auto-retry for avulso runs too,
  that is a new, explicit decision, not inherited silently from this one.
- Migration `0010` to be verified locally (throwaway Postgres: upgrade head, `\d
  saved_pipelines` matches, downgrade -1 clean, re-upgrade clean) before merge — **not applied
  to production Supabase from this sprint alone**, same checkpoint discipline as every prior
  migration (0006-0009) in this project.

## Related

- ADR-016 — `saved_pipelines` data model, `claim_due_pipeline` compare-and-swap (the
  enqueue-time retry this ADR does not duplicate).
- ADR-017 — `saved_pipeline_id` linkage on `runs`, the FK this ADR's health aggregation reads.
- ADR-018 — `services/alerting.py`/`services/digest.py`/`services/notifications.py`, the
  delivery pattern this ADR reuses for a different (health, not content) alert.
- ADR-019 — single-nullable-column-on-an-existing-table precedent, reused for Decision 2.
- Vault: `artefact/product-roadmap-post-tcc.md`, Sprint 15.
- `docs/work/2026-08-21-sprint15-production-reliability.md` — full investigation and
  workstream plan.
