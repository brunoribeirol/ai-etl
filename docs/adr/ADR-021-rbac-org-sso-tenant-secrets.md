# ADR-021: RBAC via Clerk Organizations, Org-Level SSO, and Tenant-Scoped Secrets Management

**Status:** Proposed (code complete, `make check` green locally; migration `0011` is syntax-checked and mirrors migration `0006`'s additive-table shape but was **not** run against a live Postgres this session — no Docker daemon available in this sandbox — and is not applied to production)
**Date:** 2026-08-21
**Sprint:** 19 (post-TCC product roadmap)

## Context

Sprint 19's roadmap entry bundles three items under one "enterprise security"
umbrella: SSO/SAML via Clerk (org-level), RBAC inside a tenant (who can
configure a pipeline vs. who can only view), and real secrets management for
external source credentials (Postgres/MySQL/MongoDB/REST auth) — today
isolated by nothing beyond `tenant_id`. The roadmap explicitly flags this as
unconfirmed ("provavelmente não pronto pra multi-tenant self-serve") rather
than assumed, so this ADR opens with what was actually investigated.

### Investigation (before any code)

1. **`tenant_id` today is an individual Clerk user id, not an account/org
   id** (ADR-006: `tenant_id = verify_session_token(token)["user_id"]`, the
   JWT's `sub` claim). One tenant maps to exactly one human. This is the
   load-bearing finding for this ADR: RBAC's own definition of done ("dois
   usuários do mesmo tenant, papéis diferentes") is **not expressible**
   today — two different Clerk accounts can never share a `tenant_id`, so
   there is no "same tenant" for two users to disagree about. SSO and RBAC
   turn out to share one prerequisite: tenancy has to be able to represent
   more than one user before either feature means anything.
2. **Source credentials, checked per connector** (`src/ai_etl/sources/`):
   - `postgres_source.py::load_postgres` reads a single process-wide
     `POSTGRES_URL` env var — the same URL for every tenant on the
     deployment, no per-tenant credential at all.
   - `mysql_source.py`/`mongodb_source.py` follow the identical
     single-shared-env-var pattern (verified by inspection, not assumed).
   - `rest_source.py::_build_auth` is the one connector already designed
     with multi-tenancy in mind: `auth` never carries a literal secret, only
     an *env var name* (`{"type": "bearer", "env_var": "NAME"}`), resolved
     via `os.environ` at call time — this keeps secrets out of
     `pipeline_plan`/audit JSON, but the env var itself is still
     process-wide, not tenant-scoped: two tenants configuring "their" REST
     API key must use two different env var names on the same shared
     Railway process, which is not self-serve (requires Bruno to edit
     Railway env vars and redeploy per tenant) and does not isolate one
     tenant's credential from another's code path.
   - **Confirms the roadmap's hypothesis**: no per-tenant secret isolation
     exists today for any connector.
3. **Clerk session JWT organization claims** (Clerk docs, 2026-08-21): v2
   session tokens nest organization data under an `o` claim — `o.id` (org
   id), `o.rol` (role), `o.slg` (slug) — present only when the session has
   an *active* organization; a personal session omits `o` entirely. Legacy
   v1 templates use flat `org_id`/`org_role`. Both shapes are handled
   defensively (see Decision 1).

## Decision 1 — Tenant and role resolution: Clerk Organizations, JWT-derived

`tenant_id` resolves to the active organization id (`o.id`, falling back to
legacy `org_id`) when the session has one; otherwise it falls back to the
individual `sub`, exactly as today. This is additive and backward
compatible: every existing account with no organization keeps the exact
`tenant_id` it has now — zero behavior change, zero data migration for the
`runs`/`analysis_runs`/`saved_pipelines` history already keyed by user id.

Role is derived **live from the JWT on every request**, not stored in the
database: `o.rol`/`org_role` containing `"admin"` (covers both Clerk's
legacy `"admin"` and current `"org:admin"` role-key conventions) maps to
`editor`; any other org role maps to `viewer`; no active organization at all
maps to `editor` (a solo tenant is its own sole owner — this matches every
existing account's current unrestricted behavior, so upgrading is opt-in:
nothing breaks until a tenant actually creates a Clerk Organization and
invites a second member).

**Alternatives considered:**
- **A `role` column on `users`, set via a new admin-only endpoint** —
  rejected: still requires solving "how do two Clerk *accounts* share one
  tenant" first (same blocker as above), and would mean this app
  re-implements membership/invitation management Clerk Organizations
  already provides for free. Reinventing that is a bigger, riskier surface
  for a solo-developer project than trusting Clerk's own role claim.
- **Keep `tenant_id` = individual user id, add a `shared_with` join table**
  — rejected: does not compose with SSO at all (SAML is inherently
  org-scoped in Clerk — an enterprise IT admin configures SSO once for
  their whole organization, not per employee), so it would solve RBAC
  without solving SSO, defeating the point of bundling this sprint.

**Why role mapping is a heuristic, flagged explicitly:** Clerk allows fully
custom role keys per organization. The `"admin"` substring match is the
sane default for Clerk's own built-in roles (`org:admin`/`admin` vs.
`org:member`/`basic_member`) but a deployment that defines custom Clerk
roles (e.g. `"org:lead"`) would need this mapping revisited — called out as
a known limitation below, not silently assumed correct forever.

## Decision 2 — RBAC enforcement: `require_role()` FastAPI dependency

`api/deps.py` gains `get_current_auth_context()` (verifies the JWT once,
resolves `{tenant_id, role}` per Decision 1, calls `ensure_user()` — same
single-verification cost as before) and `require_role(min_role)`, a
dependency factory raising `403` when the caller's role doesn't meet
`min_role`. `get_current_tenant_id()` (used by ~20 existing call sites)
becomes a thin wrapper over `get_current_auth_context()` — its signature and
behavior for every existing caller are unchanged.

Applied as `editor`-only: `POST/PATCH /pipelines`, `POST /runs`,
`PATCH /budget` (closes a limitation ADR-019 explicitly flagged — "sem
papel de admin/billing separado"), `POST/DELETE /secrets`. Left
`viewer`-accessible (any authenticated tenant member): every `GET` endpoint,
unchanged.

## Decision 3 — Org-level SSO/SAML: Clerk-side configuration, not app code

Clerk SAML/OIDC Enterprise Connections are configured per-Organization
entirely in the Clerk dashboard (requires a paid Clerk plan) — Clerk owns
the SAML handshake, certificate exchange, and attribute mapping; the
application never sees SAML XML. The code-side prerequisite this ADR
delivers is exactly Decision 1: once an enterprise customer's IT admin
configures SAML on their Clerk Organization, every employee who signs in
via SSO lands in a session with `o.id` set to that Organization, is resolved
to the shared tenant, and gets a role from `o.rol` — without any
SSO-specific code in this repository. **Enabling SAML for a real customer
is an operational step for Bruno post-merge** (Clerk dashboard, on a paid
plan), out of this ADR's code scope by design — there is nothing further
for the application to implement.

## Decision 4 — Tenant-scoped secrets management

New `tenant_secrets` table (migration `0011`): `id`, `tenant_id` (FK to
`users.id`), `name`, `ciphertext`, `created_at`, `updated_at`, unique on
`(tenant_id, name)`. Encrypted at rest with Fernet (symmetric AEAD,
`cryptography` — already a locked transitive dependency of `pyjwt[crypto]`,
no new dependency added), key from `AI_ETL_SECRETS_ENCRYPTION_KEY` (a
single deployment-wide encryption key, distinct from any tenant's secret
value — analogous to how a database's disk encryption key is not itself
tenant data). `services/secrets_service.py` exposes `set_secret`,
`get_secret`, `list_secret_names` (names only — decrypted values are never
returned by any list operation, only by `get_secret`, which this sprint's
API layer never calls from a `GET` handler), `delete_secret`. No decrypted
value is ever passed to `log_action()` or any other logging call — the
service module logs only `name` and outcome, matching the "API keys em
logs" non-negotiable rule the same way the existing logger's automatic
redaction of "key"/"token"/"secret"-shaped fields already does for
everything else.

New `POST/GET/DELETE /secrets` router, `editor`-only (Decision 2) — a
`viewer` cannot read, list, or write another member's credentials, closing
the DoD's isolation requirement for the management surface itself.

**Deliberately out of scope this sprint — wiring secrets into pipeline
execution.** `rest_source.py::_build_auth` (the one connector already
env-var-name-indirected) is the natural next integration point — extend its
`auth` shape with `secret_ref` alongside `env_var`, resolved via
`secrets_service.get_secret(tenant_id, name)` instead of `os.environ`. This
was investigated and explicitly **not implemented**: `tenant_id` is not
available inside `extractor_node` (`agents/extractor.py`), and getting it
there means either (a) threading an extra parameter into `extractor_node`,
which breaks the non-negotiable LangGraph node contract
(`(state: PipelineState) -> PipelineState`, no other parameters), or (b)
adding `tenant_id` to `PipelineState`, which ADR-002 and ADR-006 both
deliberately keep out of that TypedDict as a persistence/access concern,
not pipeline-execution state. Resolving this cleanly (most likely: resolve
`secret_ref` → a transient, never-persisted literal value in
`pipeline_service.py`, which already receives `tenant_id`, *before*
constructing `pipeline_plan` — never storing the decrypted value in
`pipeline_plan` itself, since that dict is what gets snapshotted into each
run's JSON by `save_run()`) is a real design decision on its own, deserving
its own review rather than being folded into this ADR under time pressure.
Postgres/MySQL/MongoDB connectors are not touched at all this sprint —
migrating them off a single shared connection string to a
per-tenant-configurable one is a larger, connector-by-connector change,
also deferred.

## Consequences

- **Positive**: RBAC's stated DoD becomes literally testable — two users of
  one Clerk Organization, different org roles, one blocked with `403` from
  an action the other can perform.
- **Positive**: SSO is unblocked with zero app-side SAML code — Clerk absorbs
  that entire risk surface, consistent with ADR-006's original rationale for
  choosing Clerk over a hand-rolled auth system.
- **Positive**: every existing single-user tenant is unaffected — `o` claim
  absent, `tenant_id`/role resolve exactly as before.
- **Positive**: a tenant secret is Fernet-encrypted at rest, scoped by
  `tenant_id`, never logged, and only ever returned in decrypted form by
  `get_secret` — which no `GET` handler calls this sprint (list returns
  names only).
- **Negative — accepted, explicit**: secrets management ships as
  storage/API infrastructure only; no pipeline connector actually
  *consumes* a tenant secret yet. A tenant can store a credential safely but
  cannot yet point a running pipeline at it. Flagged, not silently implied
  as done.
- **Negative — accepted, explicit**: Postgres/MySQL/MongoDB source
  connectors remain on the single shared `POSTGRES_URL`-style env var
  pattern; only REST source has an integration path designed (and not yet
  wired).
- **Negative**: the `"admin"`-substring org-role mapping is a heuristic
  tied to Clerk's default role-key conventions; a deployment using fully
  custom Clerk role keys needs this revisited (see Decision 1).
- **Negative**: `AI_ETL_SECRETS_ENCRYPTION_KEY` is a new operational
  secret Bruno must generate and set in Railway before this feature is
  usable in production — `secrets_service` fails closed (raises, does not
  silently store plaintext) if it is unset.

## Related

- [ADR-006](ADR-006-clerk-auth-supabase-postgres-tenancy.md) — the
  individual-user tenancy model this ADR extends (additively) to
  organizations.
- [ADR-002](ADR-002-shared-pipelinestate-typeddict.md) — why `tenant_id`
  (and now, by the same reasoning, tenant secrets) stays out of
  `PipelineState`; the reason execution-side wiring is deferred.
- [ADR-019](ADR-019-tenant-budget-cap.md) — flagged the missing
  admin/billing role this ADR's `editor` role partially closes for
  `PATCH /budget`.
- `docs/work/2026-08-21-sprint19-security-enterprise.md` — investigation
  notes and scope.
