# ADR-019: Tenant Budget Cap — Pre-Enqueue Enforcement

**Status:** Proposed (code complete, migration verified locally, not applied to production)
**Date:** 2026-08-20
**Sprint:** 29 (post-TCC product roadmap)
**Note:** Originally drafted as ADR-017 / migration `0007`. Renumbered to
ADR-019 / migration `0009` after a three-way ADR-number and migration-number
collision with Sprint 17 (kept ADR-017) and Sprint 14 (ADR-018, its own
migration `0008`, `down_revision` on Sprint 17's `0007`) — all three sprints
were built in parallel. Merge order was set to 17 → 14 → 29; see the
Addendum's "Renumbering" subsection for the full verification of this move.

## Context

Sprint 3 (ADR-008) added per-run LLM cost tracking: `analysis_runs.cost_usd`,
computed by `core/pricing.py::compute_cost_usd()` from the real token usage a
run reported, persisted after the run completes, and visible in the History
tab/`GET /runs`. This is purely observational — a tenant can see what they
spent, but nothing stops them from spending more.

An enterprise customer frequently needs a hard spend cap enforced *before*
new LLM cost is incurred, not just visibility after the fact. This ADR adds
that: a configurable per-tenant monthly budget, enforced ahead of enqueueing
a new execution, plus an early warning before the cap is hit.

## Decision 1 — Period: calendar month (UTC)

A budget cap resets on a fixed calendar-month boundary (`day=1, 00:00:00 UTC`
of the current month), not a rolling 30-day window or a per-tenant
anniversary date.

**Why:** Calendar-month billing is what every LLM provider (OpenAI, our own
future Stripe billing) already uses, so a tenant's cap lines up with the
period they're used to reasoning about ("$50/month"). It also needs no new
state to track the period boundary — `SELECT SUM(cost_usd) ... WHERE
timestamp >= date_trunc('month', now())` is computable from data the system
already persists (`analysis_runs.timestamp`). A rolling window or a
per-tenant anniversary date would both need a second stored value (window
start) that could itself drift or need backfilling; not worth it at this
project's scale.

## Decision 2 — Data model: one nullable column on `users`

`users.monthly_budget_usd` (`Float`, nullable), migration `0009`. `NULL`
(the default for every existing and new tenant) means "no cap configured" —
opt-in, zero behavior change for every tenant until they (or, in a future
session with a real admin role, an operator) set one.

**Alternatives considered:**
- **A new `tenant_budgets` table** (versioned history of cap changes, one row
  per tenant per period) — rejected as over-engineering for this round: there
  is no requirement yet to know what a tenant's cap *was* last month, only
  what it is now. `analysis_runs.cost_usd` already gives a full spend
  history; only the cap itself needs a place to live, and a single mutable
  value fits a single column better than a table.
- **A cap on `saved_pipelines`** (per-recurring-pipeline budget) — rejected:
  the scope in the vault roadmap is "budget cap per **tenant**," and a
  per-pipeline cap would need to compose with a tenant-wide one anyway (what
  happens when a pipeline is under its own cap but the tenant is over
  theirs?) — a real feature, but a distinct one, out of scope here.

## Decision 3 — Enforcement mechanism, and the real decision: approach (a) vs (b)

**The core question:** `compute_cost_usd()` only knows a run's real cost
*after* it finishes (real token usage is only known then). Budget
enforcement, by definition, has to act *before* a run starts. Two ways to
close that gap:

- **(a) Check accumulated real cost, reject the next execution if already
  over.** Query `SUM(analysis_runs.cost_usd)` for the tenant this month
  (already-persisted, canonical numbers from every completed run) against
  the cap; if `spent >= cap`, reject the next `enqueue_analysis` call before
  it ever reaches Celery. Simple, uses data the system already has, no new
  estimation logic. **Does not** prevent the one run that itself pushes
  spend past the cap — if a tenant has $0.05 of headroom left and the next
  run would cost $0.30, that run still completes and the tenant ends the
  month at $0.25 over cap. The overshoot is bounded by "the cost of one
  run," not unbounded.
- **(b) Estimate a per-run cost ceiling from tenant history, block
  preemptively.** Before enqueueing, estimate what *this* run will likely
  cost (e.g. the tenant's own trailing average `cost_usd` per run, or a
  configured worst-case ceiling per model), and reject if `spent +
  estimate > cap`. Prevents every overshoot, at the cost of a real
  estimation model: what's the right statistic (mean? p95? worst observed?),
  how does a brand-new tenant with no history get a sane default, and what
  happens when a legitimately larger dataset makes one run cost far more
  than the tenant's historical average (a false rejection of a run that
  would have been fine, or a false acceptance of one that isn't)?

**Chosen: approach (a).** Rationale:
1. Sprint 3/8's own observed real-world numbers are tiny (`$0.000643` for a
   full production Scenario-1-style run, `gpt-4o-mini`) and Sprint 12's
   204k-row scale profiling never pushed a single run's LLM cost into
   dollars, let alone tens of dollars — the realistic single-run overshoot
   at this project's current usage pattern is cents, not a budget-breaking
   event. If/when real enterprise usage or `gpt-4o` adoption changes that
   picture, approach (b) becomes worth its added complexity; not yet.
2. Approach (a) needs zero new estimation logic and no new failure mode (a
   bad estimate blocking a run that would have been fine, or letting one
   through that blows the budget by more than the estimate predicted) — it
   reuses exactly the real, audited numbers Sprint 3 already computes.
3. It composes cleanly with the existing rate limiter's philosophy
   (`execution_queue.py`'s own docstring: "Good enough to stop runaway/
   adversarial usage; not meant to be billing-grade precise").

**Approach (b) is explicitly out of scope for this round** — flagged here so
a future sprint doesn't have to rediscover the trade-off.

**Mechanism — reusing (and deliberately diverging from) the rate limiter's
pattern:** `services/execution_queue.py::check_and_increment_rate_limit`
established the shape this sprint reuses: a cheap, synchronous gate function,
called from `enqueue_analysis` before `.delay()`, that raises a
purpose-built exception the API layer maps to an HTTP error. The new
`check_budget_cap` follows the same shape (added right after the rate-limit
check, same call site, same "reject before the run ever occupies a queue
slot" guarantee) — but **does not** reuse the rate limiter's storage choice.
The rate limiter keeps its own counter *in* Redis (`INCR`/`EXPIRE`, a fixed
window with no other source of truth for "how many calls this window"), and
that is fine for a call counter, which has no other canonical home. Spend,
unlike a call count, already has a canonical home: `analysis_runs.cost_usd`,
computed once and persisted by Sprint 3. Introducing a second, Redis-resident
running total for spend would only add a value that can drift from that
canonical number (e.g. a Celery task that dies after `.delay()` succeeds but
before `save_analysis` ever runs would have incremented a Redis counter with
no way to reconcile it against the real, un-incurred cost) — for no
correctness or performance benefit at this project's scale (`analysis_runs`
is already a small table, and it's already queried on every `GET /runs`).
`check_budget_cap` therefore queries Postgres directly
(`audit.db.get_monthly_spend_usd`, a plain `SUM(...) WHERE tenant_id = :t
AND timestamp >= :month_start`) rather than maintaining a parallel Redis
counter. This is the one place this sprint's implementation intentionally
diverges from "just copy the rate limiter" — flagged here the same way
`execution_queue.py`'s own module docstring flags its divergence from
ADR-008's original sketch.

## Decision 4 — Alert before the cap is hit

`check_budget_cap` (and its non-raising sibling `get_budget_status`)
computes `ratio = spent / cap` whenever a cap is configured. If
`ratio >= AI_ETL_BUDGET_WARNING_THRESHOLD_RATIO` (env-configurable, default
`0.8`, i.e. 80%) and the tenant is not yet over the cap, it's flagged
`near_limit: True`:
- `check_budget_cap` logs a `logging.warning(...)` at enqueue time (not
  `audit.logger.log_action()` — that call requires a `PipelineState` with a
  `run_id`, which does not exist yet at enqueue time, before a run has even
  started; this is an infrastructure-level log, the same category as
  `services/`'s existing best-effort error handling, not a pipeline-audit
  entry).
- `GET /budget` (new, `api/routers/budget.py`) exposes the same
  `{cap_usd, spent_usd, ratio, near_limit, exceeded}` shape live, so a
  frontend can build a "you're near your budget" banner by polling it — the
  actual banner UI is out of scope for this backend-focused sprint, same as
  Sprint 7's `GET /config` was a read-only contract with no immediate UI
  consumer beyond a caption.
- `PATCH /budget` lets a tenant set (or clear, with `null`) their own cap —
  self-service, the same trust model every other tenant-owned resource in
  this codebase already uses (`saved_pipelines`, via `get_current_tenant_id`).
  **Known limitation**: there is no separate admin/billing role in this
  codebase — a tenant configuring their own spend cap is today's only
  option. A real enterprise deployment would likely want this set by an
  account admin instead of (or in addition to) the tenant themselves; out of
  scope until a role system exists.

## Consequences

- A tenant with `monthly_budget_usd` set and already at/over it gets `402
  Payment Required` from `POST /runs` (distinct from the rate limiter's
  `429`, since this rejection doesn't self-resolve by waiting — it needs the
  cap raised or the period to roll over) and has their scheduled pipeline
  fires skipped-and-retried-next-tick by `services/scheduler.py`, exactly
  like a rate-limited one.
- A tenant with no cap configured (every tenant today) sees zero behavior
  change — `get_monthly_budget` returns `None`, `check_budget_cap` returns
  immediately without querying spend at all.
- One extra Postgres query (`SUM(cost_usd)`) per `enqueue_analysis` call for
  tenants with a cap configured — negligible at this project's scale (same
  table, same order of query cost as `load_history`'s existing join).
- **Known limitation (by design, see Decision 3):** a single execution can
  still push a tenant's spend past their cap; only the *next* execution is
  blocked. Revisit with approach (b) if real per-run costs grow enough for
  this to matter. (The Addendum below closes the *concurrent*-execution
  version of this gap — multiple simultaneous overshoots — but not the
  single-execution one, which remains an accepted trade-off.)
- **Known limitation:** `analysis_runs.cost_usd` is only written when a
  business question triggers the Agentic BI layer (`save_analysis` /
  `_write_analysis_row`) — a Silver-only run (extract/transform/load, no
  question asked) still makes real LLM calls in the Orchestrator/Transformer
  but has no cost row at all today (a pre-existing Sprint 3 gap, not
  introduced by this sprint). Budget enforcement therefore only sees and
  caps the Agentic-BI-layer cost, not Silver-only LLM cost. Flagged for a
  future sprint, not fixed here — out of this sprint's stated scope.
- Migration `0009` verified locally (Homebrew Postgres, not Docker, same
  pattern as Sprint 13's migration `0006`): `alembic upgrade head` (0001→0009)
  applied cleanly, `\d users` matched the new column exactly, `alembic
  downgrade -1` cleanly dropped it, re-`upgrade head` reapplied cleanly.
  Re-verified after the merge-order renumbering (see the Addendum below) with
  Sprint 17's and Sprint 14's real migrations staged ahead of it.
  **Not applied to the live Supabase database** — pending explicit owner
  confirmation, same checkpoint discipline as Sprint 13's `0006`.

## Addendum — concurrency fixes (post-PR-#63 code review, before merge)

`/code-review` on PR #63 caught two real bugs in the first cut of this
enforcement, both fixed before merge:

**1. Wrong check order — a budget rejection was still consuming a
rate-limit slot.** The first cut called `check_and_increment_rate_limit`
before `check_budget_cap`, so a request rejected for being over budget
(`402`) had *already* incremented the tenant's rate-limit counter for a run
that never executed. A tenant near the rate-limit cap but already over
budget would stay locked out of legitimate calls for the rest of the
rate-limit window even after raising their cap or waiting for the budget
period to roll over — punished twice for one rejected call. **Fixed** by
swapping the order in `enqueue_analysis`: `check_budget_cap` now runs
first, `check_and_increment_rate_limit` only runs (and only ever increments)
once the budget gate has already passed.

**2. Race condition — concurrent enqueues could all pass the budget check.**
`check_budget_cap` read `SUM(analysis_runs.cost_usd)` and compared it to the
cap with no locking. Two `enqueue_analysis` calls for the same tenant
arriving close together both read the same pre-execution `spent` (neither
run had completed yet, so neither's cost had landed in Postgres), both saw
`spent < cap`, and both would pass — N concurrent runs pushing spend past
the cap, not the "bounded to one run's overshoot" guarantee Decision 3
actually promises.

**Fix chosen: a Redis lock, reusing the rate limiter's own atomicity
primitive — not a Postgres compare-and-swap.** Two mechanisms already exist
elsewhere in this codebase for exactly this class of problem:
- `execution_queue.py`'s own rate limiter: Redis `INCR`, atomic by
  construction, no external lock needed.
- `audit.db.claim_due_pipeline` (Sprint 13, ADR-016): a Postgres
  compare-and-swap `UPDATE ... WHERE next_run_at = :expected`, atomic
  because it claims one specific, already-identified row before acting on
  it.

The budget check doesn't have a single row to compare-and-swap — it gates
access to an *aggregate* (`SUM(...)` across every `analysis_runs` row this
month), which is exactly what `claim_due_pipeline`'s row-level CAS pattern
doesn't fit. What does fit is the rate limiter's own primitive, applied to a
lock instead of a counter: Redis `SET key value NX EX <ttl>` is atomic
"claim only if nobody else holds it right now," the same one-winner
guarantee `INCR` gives the rate limiter, reused rather than reinvented.
`check_budget_cap` now acquires a per-tenant `budget-inflight:{tenant_id}`
lock (`_try_acquire_budget_inflight_lock`) immediately after confirming the
tenant isn't already over the cap, **only when a cap is configured** — an
uncapped tenant (the default) never touches this lock at all, so nothing
changes for the common case. A second concurrent call for the same capped
tenant fails to acquire the lock and is rejected with `BudgetExceededError`
before it ever reaches `.delay()`. The lock is released once the run's real
cost is durably persisted (`run_full_analysis_task`'s `finally`, after
`run_full_analysis` returns — success or failure, cost may or may not have
been written, but either way the run is no longer "in flight"), or
immediately if enqueueing itself then fails before the task ever starts
(`enqueue_analysis`'s own `except` — e.g. the rate limiter rejects the call
after the budget lock was already acquired). A `BUDGET_INFLIGHT_LOCK_TTL_SECONDS`
(default 900s) safety-net expiry guards against a worker crashing between
acquiring the lock and reaching the `finally`, so a capped tenant can never
be locked out indefinitely by a dead worker.

**Trade-off, stated plainly:** a capped tenant can now have at most one
execution in flight and unreconciled at a time — a second submission while
the first is still running is rejected, not queued. Uncapped tenants are
entirely unaffected. This is a real (if usually small, given typical run
durations) reduction in concurrency for capped tenants, accepted in
exchange for the overshoot bound Decision 3 actually claims; revisit if a
real customer's workload needs concurrent capped executions.

**Verified for real** against a throwaway local Redis (`brew install redis`,
no Docker daemon in this environment — same substitution pattern prior
sprints used for Postgres) plus the same throwaway Homebrew Postgres used
for migration `0009`: two `enqueue_analysis` calls issued back-to-back for a
tenant near its cap — first passes and is enqueued, second (before the
first's cost is written) is rejected with `BudgetExceededError`, confirming
only one passes; a tenant already over its cap making repeated calls never
increments the rate-limit counter at all (confirmed by then successfully
making calls up to the rate limit afterward). See `tests/unit/test_execution_queue.py`
for the equivalent fake-Redis unit coverage (`test_concurrent_enqueue_for_a_capped_tenant_only_one_passes`,
`test_enqueue_analysis_over_budget_does_not_consume_a_rate_limit_slot`,
`test_inflight_lock_is_released_after_the_task_finishes`,
`test_inflight_lock_is_released_when_enqueueing_itself_fails`,
`test_uncapped_tenant_never_touches_the_inflight_lock`).

## Addendum — renumbering (ADR-017 → ADR-019, migration `0007` → `0009`)

Sprints 17, 14, and 29 (this one) were all built in parallel, each starting
from `main` before any of the other two had merged. All three independently
claimed `ADR-017` and migration `0007` — the "next available" numbers per
`.claude/specs/sr-standard.md` at the time each sprint started. Sprint 17
(`saved_pipeline_id` linkage on `runs`/`analysis_runs`) kept `ADR-017`/`0007`
as the merge-order winner; Sprint 14 (drift alerts) became `ADR-018`,
rewriting its own migration to `0008` with `down_revision = "0007"` pointed
at Sprint 17's file. This sprint (29) is last in the agreed merge order
(**17 → 14 → 29**), so it renumbers to **`ADR-019`** / migration **`0009`**,
`down_revision = "0008"` (Sprint 14's final migration, which itself chains
onto Sprint 17's `0007`).

**Confirmed this is pure renumbering, not schema reconciliation** (unlike
Sprint 14, which had to rewrite its migration's actual content because both
it and Sprint 17 independently added the *same* `runs.saved_pipeline_id`
column): this migration only ever adds `users.monthly_budget_usd`. Sprint 17
only touches `runs`/`analysis_runs`; Sprint 14 only touches `saved_pipelines`
and adds an index on `runs` — neither touches `users` at all, so there is no
column-name or table-content overlap to resolve. Confirmed by re-reading
this migration's own `upgrade()`/`downgrade()` before renumbering: two lines,
`op.add_column("users", ...)` / `op.drop_column("users", ...)`, untouched by
the rename.

**Verified locally, full chain**: Sprint 17's `0007_run_pipeline_linkage.py`
and Sprint 14's `0008_drift_threshold_pct.py` were temporarily copied from
their respective branches (`feat/sprint17-comparable-run-history`,
`feat/sprint14-drift-alerts-digest`) into this branch's `alembic/versions/`
(not committed — same throwaway-staging trick Sprint 14's own migration
docstring describes using against Sprint 17), then, against a fresh
throwaway Homebrew Postgres: `alembic upgrade head` applied all nine
migrations in order (0001→0009) cleanly; `\d users` showed
`monthly_budget_usd` alongside Sprint 17/14's changes on `runs`/
`saved_pipelines`, confirming no conflict; `alembic downgrade -1` cleanly
dropped only this migration's column, leaving Sprint 17/14's schema intact;
re-`upgrade head` reapplied cleanly. The two staged files were removed
afterward — this branch commits only its own `0009_tenant_budget_cap.py`.

## Related

- ADR-008 — async execution, per-tenant rate limiting (the pattern this ADR
  reuses/diverges from).
- ADR-016 — `claim_due_pipeline`'s compare-and-swap pattern, considered and
  set aside for the Addendum's concurrency fix above.
- `core/pricing.py::compute_cost_usd` — the per-run cost this ADR's
  enforcement reads (unchanged by this sprint).
- Vault: `artefact/product-roadmap-post-tcc.md`, Sprint 29.
