# ADR-017: Tenant Budget Cap — Pre-Enqueue Enforcement

**Status:** Proposed (code complete, migration verified locally, not applied to production)
**Date:** 2026-08-20
**Sprint:** 29 (post-TCC product roadmap)

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

`users.monthly_budget_usd` (`Float`, nullable), migration `0007`. `NULL`
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
  this to matter.
- **Known limitation:** `analysis_runs.cost_usd` is only written when a
  business question triggers the Agentic BI layer (`save_analysis` /
  `_write_analysis_row`) — a Silver-only run (extract/transform/load, no
  question asked) still makes real LLM calls in the Orchestrator/Transformer
  but has no cost row at all today (a pre-existing Sprint 3 gap, not
  introduced by this sprint). Budget enforcement therefore only sees and
  caps the Agentic-BI-layer cost, not Silver-only LLM cost. Flagged for a
  future sprint, not fixed here — out of this sprint's stated scope.
- Migration `0007` verified locally (Homebrew Postgres, not Docker, same
  pattern as Sprint 13's migration `0006`): `alembic upgrade head` (0001→0007)
  applied cleanly, `\d users` matched the new column exactly, `alembic
  downgrade -1` cleanly dropped it, re-`upgrade head` reapplied cleanly.
  **Not applied to the live Supabase database** — pending explicit owner
  confirmation, same checkpoint discipline as Sprint 13's `0006`.

## Related

- ADR-008 — async execution, per-tenant rate limiting (the pattern this ADR
  reuses/diverges from).
- `core/pricing.py::compute_cost_usd` — the per-run cost this ADR's
  enforcement reads (unchanged by this sprint).
- Vault: `artefact/product-roadmap-post-tcc.md`, Sprint 29.
