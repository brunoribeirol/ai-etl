# ADR-047: Admin Mediated Write-Access for Support Scenarios

**Status:** Accepted, minimal scope.
**Date:** 2026-09-06
**Plan:** `docs/work/2026-09-06-mvp-monetization-readiness-plan.md`, WS3

## Context

ADR-032 (Sprint 31) built `/admin`'s read-only cross-tenant access
(`require_admin()`, `admin_action_log`) and explicitly flagged a write path
as future work, not an omission (Decision 2). The 2026-08-21 SOC2
self-assessment's own top finding restated the same gap in auditor terms:
*"how does staff access customer data for support, and is it logged? Today
the honest answer is direct DB access by Bruno, not mediated by the
application, not separately logged."*

The owner asked, after closing the mega-audit remediation plan, to close
the cheap remaining gaps toward a monetization-ready MVP. This is the one
item in that follow-up plan that is a real code change touching auth, so it
went through an explicit scoping decision before implementation — see the
plan's WS3 section for the two rejected/deferred alternatives (a general
"support session" concept; deferring further as accepted risk).

## Decision 1 — Minimal scope: `reason` on every route, one write action

Two changes, reusing 100% of ADR-032's existing infrastructure:

1. **Every `/admin` route now requires a `reason`** — a short, free-text
   justification for the action, logged verbatim in a new
   `admin_action_log.reason` column (migration `0022`). Read routes take it
   as a required query parameter; the new write route takes it in the
   request body. This alone is most of what a SOC2 auditor is checking for
   under CC5: not that access never happens, but that it is deliberate and
   accountable.
2. **One concrete write action**: `POST /admin/tenants/{tenant_id}/pipelines/{pipeline_id}/retry`
   — an admin-triggered retry of a tenant's saved pipeline, the exact
   support scenario ("a tenant's run is stuck, support needs to re-fire it")
   that today happens as unmediated, unlogged direct DB access.

**Rejected alternative: a general-purpose "support session" concept**
(time-boxed, every query during the session logged). More auditor-
convincing in the abstract, but meaningfully more design and code surface
for a single-operator, pre-revenue product with no support team yet to use
it. Revisit if/when a support role distinct from the founder exists.

**Rejected alternative: defer further, same as `rolbypassrls`.** Rejected
because this gap, unlike `rolbypassrls`, has no compensating control at all
today (RLS is a real backstop for the DB-role gap; nothing backstops
unmediated direct DB access) — and the fix is genuinely cheap given
`enqueue_analysis()` already exists.

## Decision 2 — Reuse `enqueue_analysis()` verbatim, not a bypass path

The new retry route calls `services/execution_queue.enqueue_analysis()` —
the exact function `services/scheduler.py`'s own scheduled fires call, and
`POST /runs` for an avulso run. An admin-triggered retry therefore goes
through the tenant's normal budget cap and rate limit exactly like any
other run: `BudgetExceededError`/`RateLimitExceededError` map to the same
402/429 responses `POST /runs` already uses. **Deliberately not a bypass**
— an admin cannot use this route to run a pipeline the tenant's own budget
would have blocked; that would trade one gap (unmediated access) for a
worse one (an unmediated way to also inflate a tenant's spend).

## Decision 3 — `reason` nullable at the DB, required at the API

Every row written before migration `0022` has no `reason` — backfilling a
fabricated value for those would be worse than an honest `NULL`. New rows
always have one, enforced at the FastAPI layer (`Query(min_length=1)` /
`Field(min_length=1)`), same "optional at DB, enforced at API" shape
`saved_pipelines.approval_threshold_rows` (ADR-028) already uses.

## Consequences

- `docs/compliance/soc2-readiness-assessment.md`'s CC5 admin-access gap
  moves from "not addressed" to "in progress": read access is now
  reason-gated and logged, one real support write action is mediated. Full
  closure would mean every direct-DB-access support scenario has an
  equivalent mediated route — not attempted this round, tracked in
  `docs/compliance/risk-register.md` row 3.
- No new admin role or permission tier — the existing `admin`/
  `AI_ETL_PLATFORM_ADMINS` allowlist is unchanged; this ADR only changes
  what an admin must supply and what an admin can do, not who is one.
- `admin_action_log.reason` is free text, same never-log-a-secret
  discipline as `detail` — not independently validated against dumping a
  credential, same trust boundary as every other admin-authored log field.
