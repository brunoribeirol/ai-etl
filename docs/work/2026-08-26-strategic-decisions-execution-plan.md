# 2026-08-26 — Execution plan: post-audit strategic decisions

**Status:** in progress
**Owner:** Bruno Ribeiro (decisions) + Claude (execution)

## Objective

Close the 3 remaining fronts left open after the 2026-08-25 audit-fix session
(`docs/CURRENT_STATE.md`, 2026-08-25 entry):

1. TCC text discrepancy — **done**, see vault
   `writing/drafts/draft-product-vision.md` (2026-08-26 edit): Agentic BI
   reframed from speculative/future to implemented/production, matching
   `docs/CURRENT_STATE.md`.
2. UI for 4 backend-ready, zero-frontend features: tenant secrets, budget
   self-service, per-pipeline notification config, LGPD export/retention.
3. Sandbox: user decided to migrate `core/sandbox.py`'s restricted `exec()`
   to a real Docker-isolated sandbox (see ADR-003, ADR-007, ADR-032 Decision
   4 for the accepted-risk history this supersedes).

Clerk dev mode and pricing: **explicitly deferred by the user**, no work
this round (Clerk — no domain purchase yet; pricing — after MVP validation).

## Non-goals

- Not touching Clerk config or auth flow.
- Not defining a pricing model.
- Not re-opening any of the 23 already-merged audit-fix PRs.

## Affected contracts

- `core/sandbox.py`'s `execute_in_sandbox()` signature must not change for
  callers (Transformer/Analyst/Science) — only the isolation mechanism
  underneath.
- New frontend components only; no existing route/schema changes required
  for the 4 UI features (`GET/POST/DELETE /secrets`, `GET/PATCH /budget`,
  `PATCH /pipelines/{id}` notification fields, `GET /tenant/export`,
  `GET /tenant/retention` all already exist and are stable).

## Sequencing

**Wave 1 (3 parallel worktrees, launched together):**
- W1a — Secrets management UI (tenant self-service, editor-only)
- W1b — Budget self-service UI (tenant self-service cap + status)
- W1c — Sandbox Docker migration: ADR-038 (formalizes the decision,
  supersedes ADR-032 Decision 4) + implementation

**Wave 2 (after Wave 1 lands, 2 parallel worktrees):**
- W2a — Per-pipeline notification config UI (wired into
  `pipelines-manager.tsx`'s edit form, same pattern as `ModelPicker`)
- W2b — LGPD export/retention UI (tenant-facing, likely a settings page)

Each PR follows the established flow: branch → `ruff`/`mypy`/`bandit`/pytest
(+ `npm run lint`/`next build` for frontend) locally → PR → CI green →
squash-merge. Real Postgres via `docker-compose up -d app-postgres-test`
where integration tests touch it.

## Acceptance criteria

- W1a/W1b/W2a/W2b: `make check` clean, new frontend page reachable from nav,
  editor-only routes enforce role client-side (cosmetic) and rely on the
  existing server-side check (real) — same pattern as admin panel (#144).
- W1c: ADR-038 written and accepted; `execute_in_sandbox()` runs generated
  code inside a container with no host filesystem/network access beyond
  what's explicitly allowlisted; existing sandbox test suite (unit +
  Transformer/Analyst/Science integration) passes unmodified in contract,
  updated only where the isolation mechanism itself is asserted on.

## Risks

- Docker-in-Railway: needs confirming the current Railway service can run
  nested containers, or whether this requires Railway's own container
  primitives / a sidecar. Flagged for W1c to investigate before committing
  to an implementation shape — may require its own ADR follow-up before
  code, not a same-day migration.
- Wave 1 parallel worktrees touch different files (frontend components vs.
  `core/sandbox.py` + new Docker infra) — no expected merge conflicts, but
  W1c is materially larger/riskier than W1a/W1b and may not close same-day.
