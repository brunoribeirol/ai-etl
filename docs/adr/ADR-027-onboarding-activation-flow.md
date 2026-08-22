# ADR-027 — Self-serve onboarding/activation flow (Sprint 26)

## Status

Accepted

## Context

Roadmap (`artefact/product-roadmap-post-tcc.md`, Sprint 26): there is no
guided "connect a source → describe what you want → see a result" flow for
a brand-new tenant. The product's real value (recurring pipelines, drift
digests, the executive `/resumo` view — Sprints 13/14/18) only shows up
after a tenant has already run something at least once; today the only
entry point is `/` (`ExecutarForm`), a bare upload form with no guidance,
no example data, and no visible sense of "what's left before this account
is actually set up."

Investigated first, per this project's own standard, before designing
anything:

1. **`frontend/src/components/executar-form.tsx`** (Sprint 6/7) is the
   real one-off execution path — file upload or manual spec, business
   question, submit, poll `GET /runs/{task_id}/status`, link to
   `/historico/{run_id}` on completion. **`frontend/src/app/resumo/`**
   (Sprint 18, ADR-024) is the executive summary surface, but it only lists
   **saved** pipelines (`GET /pipelines`) — an avulso run from `/` never
   appears there. Both are real, working value; the onboarding flow's job
   is to walk a new tenant *into* them, not to reimplement either.
2. **`frontend/src/app/pipelines/` + `pipelines-manager.tsx`** (Sprint 13,
   ADR-016) already owns saved-pipeline creation. ADR-016 Decision 3
   restricts scheduling to "live" sources (`postgres`/`sqlite`/`mysql`/
   `mongodb`/`rest`) — never `csv`/uploaded files. That means a first-run
   example dataset (necessarily a file upload) **cannot** become a
   scheduled pipeline directly; the honest onboarding path is: guided
   first run (upload, incl. example data) → see the result → point the
   tenant at `/pipelines` for *live*-source recurrence and at `/resumo`
   for the executive view, once they have something scheduled. Onboarding
   does not touch `pipelines-manager.tsx`'s form logic at all.
3. **Activation signal — can it be derived without new schema?** Yes.
   `audit/models.py`'s `runs` table already has `tenant_id` and `status`
   (Sprint 3+), and `saved_pipelines` already has `tenant_id` (Sprint 13).
   "Has this tenant completed a first pipeline" is exactly
   `COUNT(runs) WHERE tenant_id = :t AND status = 'completed'` — an
   aggregate over data that already exists, the same pattern Sprint 15
   already uses for `success_rate`/`avg_latency_seconds` and Sprint 29
   for `get_monthly_spend_usd`. **No new column, no new table, no
   migration.**
4. **Example datasets** — `case_study/data/generate_{sales,orders}.py`
   exist, but their *output* (`case_study/data/*.csv`) is gitignored
   (`.gitignore` lines 29–33) and not shipped anywhere in production; it
   only ever exists on a machine that has run the generator locally.
   Reusing it as a "click to try" example in the deployed frontend is not
   possible without committing a dataset. Rather than committing a copy of
   the (larger, generator-produced) case-study fixture, a small,
   hand-authored, deterministic sample CSV is added under
   `frontend/public/examples/` — served as a static Next.js asset, not
   covered by any existing `.gitignore` rule, small enough (~20 rows) to
   commit outright. It mirrors the case-study schema
   (`order_id`/`customer_id`/`dt`/`product`/`amt`/`quantity`/`status`/
   `region`) so the guided flow exercises the same kind of "messy but
   real" cleanup (a null, a duplicate row) the case study itself uses,
   without duplicating the case study's own gitignored fixtures.

## Decision

**No new database schema.** Activation status is computed on read from
`runs`/`saved_pipelines`, exposed by a new, deliberately small endpoint,
`GET /onboarding/status`, added the same way `GET /budget` wraps
`get_budget_status` (Sprint 29) — a read-only aggregate, no new table, no
enforcement, safe to poll.

**Guided flow is additive UI, not a new execution path.** A new route
(`/comecar`) hosts:

- A source step: "use an example dataset" (fetches
  `frontend/public/examples/sample-sales.csv` client-side and hands it to
  the existing upload path as a `File`) or "upload your own" (same
  `<input type="file">` affordance `ExecutarForm` already has).
- A spec/question step and a run/result step that **reuse `ExecutarForm`
  itself** — `ExecutarForm` gained two optional, backward-compatible props
  (`initialFile`, `initialBusinessQuestion`, both default to the current
  empty state) instead of the onboarding page reimplementing upload/poll/
  result logic. `/` (`page.tsx`) renders `<ExecutarForm />` with no props,
  unchanged.
- A visible activation checklist (`GET /onboarding/status`): "first
  pipeline run" and "a scheduled pipeline created," each linking onward to
  `/` /`/pipelines` respectively, plus a closing link to `/resumo` once a
  saved pipeline exists — the explicit "conduct the user to real value"
  requirement from the roadmap, not a dead-end upload screen.

**Why not gate/redirect existing routes on activation status**: doing so
would change the behavior of `/`, `/pipelines`, and `/resumo` for every
existing tenant (all of whom are already "activated" in practice, since
the product predates this flow). `/comecar` is purely additive — a new nav
link, first in the header — matching the low-risk, additive posture this
project has used for every prior UI-only sprint (Sprint 18/ADR-024's own
precedent: "puramente composição de UI sobre fluxos já existentes").

**Why an ADR at all, given the above is composition over existing flows**:
following the same precedent Sprint 18 (ADR-024) set — this sprint makes
one real, if small, architectural call worth recording (deriving
activation from existing tables instead of adding a column, and where the
guided flow's first-run example dataset can and cannot lead), so it is
documented rather than silently skipped.

## Consequences

- **No migration** in this sprint. If a future sprint needs richer
  activation tracking (e.g. a persisted "activated_at" timestamp, an
  activation funnel with more granular steps), that is new work with its
  own migration — not assumed here.
- The checklist is currently only rendered on `/comecar`, not repeated in
  the header on every page — a deliberate scope cut (same "não precisa ser
  bonita, precisa funcionar" posture Sprint 13 set), flagged as a known
  limitation rather than silently decided.
- `GET /onboarding/status` recomputes both counts on every call (no
  caching) — acceptable at current scale (same posture as `GET /budget`);
  revisit if `/comecar` traffic grows enough to matter.
- The example dataset is static and hand-authored, not the case-study
  generator's output — it will not stay byte-for-byte in sync with
  `case_study/data/generate_sales.py` if that generator's schema changes;
  acceptable since its only purpose is a small, realistic "try it" sample,
  not case-study parity.

## Related

- ADR-016 (scheduled-pipeline data model — live-source-only scheduling,
  respected here by not offering "schedule this" from the example-dataset
  path).
- ADR-024 (executive summary UI — the flow's downstream destination once a
  saved pipeline exists).
- ADR-019/ADR-020 (existing precedent for deriving read-only aggregates
  from `runs`/`analysis_runs`/`saved_pipelines` without new schema).
