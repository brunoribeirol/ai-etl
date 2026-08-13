# ADR-006 — Clerk (local JWT/JWKS) for authentication, Supabase-as-Postgres for real account tenancy

**Status:** Accepted
**Date:** 2026-08-13
**Deciders:** Bruno Ribeiro

---

## Context

ADR-005 closed the active cross-session data leak in the Histórico tab with a stopgap: a random UUID per Streamlit `st.session_state`, filtered explicitly in `audit/db.py`. It was always scoped as temporary — no real identity survives a cleared cookie, a browser switch, or a device switch, and Sprint 1 (this ADR) is the planned public deploy on Railway, which cannot ship on top of session-only "tenancy." Two independent decisions are needed: how users authenticate, and what backs `tenant_id` once it stops being a session UUID.

### Authentication provider

1. **Auth0** — mature, broad enterprise feature set (SSO, org management, extensive rules engine). Rejected: pricing tiers and feature surface are aimed at multi-org enterprise auth; overkill for a single-tenant-per-user SaaS at this stage, and the free tier's MAU cap is tighter than Clerk's for a pre-revenue project.
2. **Firebase Auth** — free, well-documented, generous free tier. Rejected: couples identity to the broader Firebase/GCP ecosystem (Firestore, GCP IAM) that this project does not otherwise use; the project's data plane is Postgres (Supabase) and its deploy target is Railway, not GCP — adding Firebase would be a second vendor with no other footprint in the stack.
3. **Roll-your-own (NextAuth-style: bcrypt + sessions, or a hand-rolled JWT issuer)** — full control, no vendor dependency. Rejected: credential storage, password reset flows, session/token rotation, and MFA are exactly the kind of security-critical, easy-to-get-subtly-wrong surface that a solo-developer TCC project should not take on when a managed alternative exists — the cost of getting it wrong (leaked credentials, session fixation) is disproportionate to the cost of a vendor SDK.
4. **Clerk** (chosen) — drop-in React/Streamlit-friendly hosted UI components, generous free tier for a pre-revenue project, and — decisive for this project's application-level-tenancy stance (see below) — first-class support for verifying JWTs locally via a published JWKS endpoint, so the application never needs a network round-trip to Clerk per request to authenticate a user.

Verification approach within Clerk: **local JWT verification via JWKS** (fetch and cache Clerk's public keys, verify signature/expiry/claims in-process) rather than calling Clerk's backend API on every request. This avoids adding Clerk's API latency and availability to the critical path of every pipeline run, at the cost of needing to handle JWKS caching/rotation correctly in `auth_service.py` (see Implementation below). Env vars: `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `CLERK_JWKS_URL`.

### Database / tenancy backing

1. **Supabase-native Auth + Row-Level Security** — Supabase's own user table, JWTs, and Postgres RLS policies enforcing tenant isolation at the database layer. Rejected for this project: it would mean running two authentication systems in parallel with no gain (Clerk is already chosen for its UI/DX), and RLS policies written in SQL are opaque to this codebase's existing audit trail and testing style (`audit/db.py`'s explicit `.where(tenant_id == ...)` clauses are unit-tested in Python today; RLS policies would move that enforcement out of testable application code and into database policy files this project has no tooling around).
2. **Plain RDS / Neon (unmanaged-auth Postgres elsewhere)** — functionally equivalent to option 3 below as far as this decision is concerned; no material advantage over Supabase for a project already leaning toward Supabase for its generous free tier and Railway-compatible connection strings, and switching away from Supabase now would be a second infrastructure decision this ADR doesn't need to make.
3. **Supabase used only as managed Postgres** (chosen) — Supabase's hosted Postgres instance as `APP_DATABASE_URL`, with Supabase's Auth/RLS features entirely unused. Tenant filtering stays explicit in application code, following the pattern ADR-005 already established in `audit/db.py` (SQLAlchemy Core, bound `.where(tenant_id == ...)`, no ORM, no f-strings). This keeps exactly one authentication system (Clerk) and keeps tenant enforcement inside code this project already tests directly, at the cost of relying on every future query author remembering to add the `tenant_id` filter — there is no database-level backstop if application code forgets it.

## Decision

Adopt **Clerk** for authentication (local JWT verification via JWKS) and **Supabase as managed Postgres only** (no Supabase Auth, no RLS) for the database, with `tenant_id` sourced from the verified Clerk user ID instead of a session UUID.

This directly closes the two SaaS Roadmap rows (`.claude/specs/sr-standard.md` §8) that ADR-005 explicitly left open — "Autenticação" (Nenhuma → JWT/OAuth2 middleware antes dos agentes) and, partially, "Isolamento de dados" (Tenant ID em todo audit log e storage: `tenant_id` now maps to a real account, though row-level security itself remains future work per the option-1 tradeoff above).

**This ADR supersedes [ADR-005](ADR-005-session-scoped-run-isolation.md).** ADR-005's session-UUID mechanism is removed by this Sprint (not run in parallel); ADR-005 remains as a historical record of the interim stopgap and its reasoning, but its Decision is no longer in effect once this Sprint's implementation lands. ADR-005 itself already anticipated this ("The future Sprint 1 auth/tenancy ADR ... will define real account-based tenancy, superseding the session-UUID stopgap described here") — this ADR is that ADR.

### Implementation shape

**Where the JWT is verified** — a new `src/ai_etl/services/auth_service.py`, mirroring the existing pure-function-returning-results style of `pipeline_service.py` (no Streamlit import, no exceptions raised into the UI layer):

```python
# sketch — not the final implementation; backend-agent-sprint1 owns the real module
def verify_session_token(token: str) -> AuthResult:
    """Verify a Clerk session JWT against the cached JWKS and return the outcome.

    Never raises for expected failure modes (expired token, bad signature,
    unreachable JWKS) — returns AuthResult(ok=False, error=...) so callers
    (app.py) decide how to present the failure, matching how pipeline_service
    functions return error strings instead of raising into the UI.
    """

class AuthResult(TypedDict):
    ok: bool
    user_id: Optional[str]   # Clerk user id -> becomes tenant_id
    error: Optional[str]
```

JWKS fetch/cache lives inside `auth_service.py` (not re-fetched per call); rotation handling and the exact caching strategy (TTL vs. `kid`-miss-triggered refetch) are implementation details for `backend-agent-sprint1`, not fixed by this ADR.

**How `tenant_id` flows**, replacing the ADR-005 session-UUID path end to end:

1. Clerk issues a JWT client-side after sign-in; the JWT (or its `sub` claim) reaches `app.py`.
2. `app.py` calls `auth_service.verify_session_token()` once per session/interaction (replacing `_get_session_id()`) and gates the app — unauthenticated users see a sign-in prompt instead of the pipeline UI, the same shape as today's `_check_api_key()` gate.
3. On success, `auth_service` returns the Clerk `user_id`, which `app.py` passes as `tenant_id` into `pipeline_service.py`'s `run_full_analysis()` / `run_silver_pipeline()` — same parameter name and position as the ADR-005 session UUID, only the source changes.
4. `pipeline_service.py` forwards `tenant_id` unchanged into `audit/db.py`'s `save_run()` / `save_analysis()` / `load_history()` — no signature changes needed there, since ADR-005 already threaded `tenant_id: str | None` through every one of those functions in anticipation of exactly this swap.
5. `PipelineState` itself does not need a `tenant_id` field — tenancy is a persistence/access concern (who may write/read a run), not pipeline-execution state the agents reason about, so it stays out of the TypedDict per ADR-002's contract and continues to be threaded as a separate parameter alongside `state`, as ADR-005 already established.

**Migration shape needed next** (described here, authored by `backend-agent-sprint1` as the actual Alembic revision — not written by this ADR):

- A new `users` (or `tenants`) table: at minimum `id` (Clerk `user_id`, primary key, string — Clerk IDs are not UUIDs), `created_at`, and room for future account metadata (plan/tier, once billing is scoped in a later sprint).
- `runs.tenant_id` and `analysis_runs.tenant_id` change from nullable `String` to `NOT NULL` with a foreign key to `users.id`. This requires a backfill decision for any pre-existing rows written under the ADR-005 session-UUID scheme: those rows have no corresponding `users` row and cannot satisfy a `NOT NULL` FK as-is. Given this is a pre-launch TCC project with no real user base yet, the expected resolution is to delete or archive session-scoped rows rather than invent synthetic user records — but that is a call for `backend-agent-sprint1`/Bruno to make explicitly in the migration PR, not implied here.
- No connection-pool-per-tenant and no row-level security are introduced by this migration — both remain open SaaS Roadmap items, consistent with the Supabase-as-Postgres tradeoff above.

## Consequences

- **Positive**: closes the "Autenticação" SaaS Roadmap gap with a managed provider instead of hand-rolled credential handling, appropriate for a solo-developer project's risk budget.
- **Positive**: local JWKS-based verification keeps Clerk out of the request-latency critical path — no per-request call to Clerk's API to authenticate a user.
- **Positive**: `tenant_id` becomes a real, durable account identifier instead of a per-browser-session UUID — users keep their run history across devices/browsers, which ADR-005 explicitly could not provide.
- **Positive**: reuses ADR-005's existing `tenant_id`-threading work almost entirely — `audit/db.py`, `pipeline_service.py` signatures do not need to change, only the value's source does.
- **Positive**: keeps exactly one authentication system (Clerk) rather than running Clerk alongside Supabase Auth, and keeps tenant enforcement inside application code this project already unit-tests, rather than moving it into opaque RLS policies.
- **Negative — accepted risk, explicit until Sprint 2**: this Sprint puts the application behind real authentication and a public Railway deploy while the `exec()` sandbox unification (three divergent, unenforced-timeout `exec()` sites documented in [ADR-003](ADR-003-exec-sandbox.md)) remains unresolved. Once real, distinct tenant accounts can submit pipeline specs that reach the Transformer/Analyst/Science `exec()` sites, the sandbox's known weaknesses (no enforced timeout, escapable via `ctypes`/object-graph introspection) are exposed to a public, multi-tenant user base rather than only to the project owner. This is a deliberately sequenced, documented risk — Sprint 2 is the sandbox-unification sprint — not an oversight; it must not be treated as closed by this ADR.
- **Negative**: no row-level security means tenant isolation has exactly one enforcement point (application-level `.where(tenant_id == ...)` filters) with no database-level backstop; a missed filter in a future query is a data leak, the same class of bug ADR-005 fixed once already.
- **Negative**: adds one new external dependency (Clerk) and its associated failure modes (JWKS endpoint unreachable, key rotation not yet cached) to the app's auth gate; `auth_service.py` must fail closed (deny access) rather than open on verification errors.
- **Negative**: the `NOT NULL` + FK migration is a breaking change for any rows written under the ADR-005 scheme — requires an explicit backfill/deletion decision before it can ship (see Implementation above), not merely an additive migration like ADR-005's was.

## Related

- [ADR-005](ADR-005-session-scoped-run-isolation.md) — session-scoped stopgap; **superseded by this ADR**.
- [ADR-002](ADR-002-shared-pipelinestate-typeddict.md) — why `tenant_id` stays outside `PipelineState` (persistence/access concern, not pipeline-execution state).
- [ADR-003](ADR-003-exec-sandbox.md) — the sandbox-unification risk this ADR explicitly accepts as open until Sprint 2.
- [ADR-004](ADR-004-sqlite-audit.md) — `audit/db.py`, the persistence layer this ADR's `tenant_id` source-swap flows through.
- `.claude/specs/sr-standard.md` §8 — SaaS Roadmap table; this ADR closes the "Autenticação" row and partially closes "Isolamento de dados".
