# ADR-032: RLS Posture, Platform-Admin RBAC Role, Semgrep SAST, and Sandbox Bypass Risk Acceptance

**Status:** Accepted (code complete for Decision 2 and 3; Decision 1 is a
conscious risk-acceptance decision with no production code change this
sprint; **Decision 4 superseded 2026-08-26 by
[ADR-038](ADR-038-docker-sandbox-migration.md)** — see each Decision for
what "accepted"/"superseded" means concretely)
**Date:** 2026-08-22
**Sprint:** 31 (Phase 2 roadmap, "Segurança avançada e acesso administrativo
auditável")

## Context

The 2026-08-22 technical audit (`product-roadmap-fase2-big-tech.md`)
confirmed four security gaps this project's own documentation already
flagged without ever becoming scheduled work:

1. `rolbypassrls=true` on this app's Postgres connection role neutralizes
   Row Level Security as a real second layer of defense (`SECURITY.md`).
2. No admin/support access model exists — the project's own SOC2
   self-assessment (Sprint 24, `docs/compliance/soc2-readiness-assessment.md`)
   named this its single largest finding; today the only "admin access" is
   Bruno via direct `psql`, unaudited.
3. CodeQL (`.github/workflows/codeql.yml`) has been disabled since it was
   added — this repository is private and code scanning requires GitHub
   Advanced Security, which the current plan doesn't include. Every run
   failed at the upload step with no config fix available.
4. The `exec()` sandbox's introspection bypass
   (`().__class__.__mro__[1].__subclasses__()`) is real, confirmed, and has
   never been mitigated (`SECURITY.md`, `docs/adr/ADR-003-exec-sandbox.md`,
   `docs/adr/ADR-007-unified-sandbox-policy.md`).

This ADR bundles four decisions under one number because they share one
sprint, one theme ("segurança avançada"), and — critically — none of them
change the others' trade-off: each is independently accept-or-fix. Splitting
into four ADR numbers would not make any single decision clearer; it would
just make cross-referencing them harder. Each decision below is a complete,
independently readable ADR section.

---

## Decision 1 — RLS posture: keep `rolbypassrls=true`, documented trade-off

**Decision: keep the current posture.** This app's own connection role
retains `rolbypassrls=true`. No production code or database role change
ships this sprint.

**What RLS actually protects against today, restated precisely (from
`SECURITY.md`):** Supabase grants the `anon`/`authenticated` roles full CRUD
on every `public`-schema table by default, reachable via Supabase's
PostgREST/GraphQL API using nothing but the project's `anon` key — a
non-secret, client-embeddable value. This project never uses that API
(auth and every read/write route through FastAPI + Clerk-verified JWTs), so
RLS's only job here is closing that PostgREST-reachable hole for
`anon`/`authenticated`. Every table added since 2026-08-21 already enables
RLS in its own migration (`SECURITY.md`'s standing rule, followed by
`0014`/`0017`). This app's own role bypasses RLS entirely
(`rolbypassrls=true`), so RLS is **not** a second layer against a bug in
this application's own `tenant_id` filtering — a `WHERE tenant_id = :id`
omitted by mistake in a future query would leak data to any authenticated
tenant who can reach that endpoint, and RLS as currently configured would
not catch it.

**Alternatives considered:**

- **A. Keep `rolbypassrls=true` (chosen).** Zero implementation cost,
  zero regression risk. RLS keeps doing the one job it was actually
  deployed for (closing the PostgREST/anon-key hole). The accepted gap: a
  `tenant_id`-filter bug in application code is not caught by a database-
  level backstop.
- **B. Create a second, non-bypass Postgres role for the app connection,
  plus real RLS policies (`USING (tenant_id = current_setting('app.tenant_id'))`)
  on every tenant-scoped table.** This is the textbook "defense in depth"
  answer and was seriously evaluated. Rejected for this sprint, not
  indefinitely, for three concrete reasons found during investigation:
  1. It requires the application to set a session-scoped Postgres GUC
     (`SET LOCAL app.tenant_id = ...`) on every connection checkout, inside
     the same transaction as every query — `audit/connection.py`'s
     `get_engine()` is a single process-wide, `lru_cache`d SQLAlchemy
     engine shared across every tenant's requests; wiring per-request GUC
     scoping into it correctly (and verifying it can never leak across a
     pooled connection reused by a different tenant's request) is a real,
     testable, non-trivial change to the connection layer — the exact kind
     of "not necessarily implementation of isolation now" work this
     roadmap entry explicitly scoped out (see the roadmap's own framing:
     "decisão de trade-off real, não assumida de antemão", not "ship RLS
     policies this sprint regardless").
  2. Every one of the ~15 query functions in `audit/db.py` (`load_history`,
     `list_saved_pipelines`, `get_monthly_budget`, etc.) would need
     re-auditing to confirm each one either sets the GUC or is exempt
     (e.g. Sprint 31's own `admin_action_log` cross-tenant reads, which
     *must* bypass any RLS tenant filter by design — see Decision 2).
     Getting that boundary wrong (an admin query accidentally scoped, or a
     tenant query accidentally not) is a worse outcome than today's known,
     documented gap.
  3. The actual residual risk today is narrow: every existing tenant-scoped
     query in `audit/db.py` already binds `tenant_id` as a SQLAlchemy
     parameter in its `WHERE` clause (verified by inspection, consistent
     with this project's non-negotiable "no f-string SQL" rule) — the
     failure mode RLS-as-backstop would catch is a *future* query written
     without that filter, not a demonstrated current bug.
- **C. Enable RLS with policies but keep `rolbypassrls=true`.** Rejected as
  actively misleading: this would look like defense-in-depth in a
  `\d+ table` inspection or a security review, while providing exactly zero
  protection, since the bypass flag makes every policy a no-op for this
  app's own role. A posture that looks safer than it is is worse than an
  honestly documented gap.

**Trigger for revisiting this decision** (not vague — a concrete signal):
opening the product beyond Bruno's own controlled testing to external paying
tenants with real data-sensitivity requirements (enterprise SSO customers,
per ADR-022 Decision 3, are the natural trigger — an enterprise buyer's
security review is exactly the audience that will ask this question
directly). At that point, Option B's session-GUC connection-layer work
becomes a real, scoped follow-up ADR, not a "someday" item.

---

## Decision 2 — Platform-admin RBAC role, own audit trail

**Decision:** extend ADR-022's RBAC with a third role, `admin`, ranked above
`editor`. Unlike `editor`/`viewer` (resolved per-request from a Clerk
Organization role claim — `o.rol`/`org_role`, a **tenant-scoped** concept),
`admin` is resolved from the individual Clerk user id (`sub` claim) against
a new `AI_ETL_PLATFORM_ADMINS` env var allowlist — a **platform-operator**
identity, orthogonal to any tenant's own organization membership.

**Why not encode platform admin as a Clerk org role:** a Clerk org role is
controlled by *that organization's own admins* (any tenant can promote a
member to their org's `admin` role via the Clerk dashboard). Platform admin
means "can read any tenant's data," which must never be a decision one
tenant's org admin can grant. These are different axes entirely — ADR-022's
`_EDITOR_ROLE_MARKER` heuristic maps a Clerk org role to `editor`
specifically to prevent this collision (see the updated comment in
`api/deps.py`).

**Why an env var allowlist, not a DB column:** the roster of platform admins
is small (Bruno today, eventually a handful of support staff), changes
rarely, and is an *operational* fact about who runs the deployment — not
tenant data. A DB column would need its own RBAC to prevent a compromised
tenant account from granting itself admin via a write path; an env var only
the deployment operator (Railway config) can change has no such attack
surface. This mirrors `AI_ETL_SECRETS_ENCRYPTION_KEY`'s existing pattern
(ADR-022 Decision 4) of a deployment-level secret distinct from any tenant's
own data.

**Enforcement:** `api/deps.py::require_admin` — a new dependency (not a
`require_role("admin")` call site reused as-is, because unlike `editor`/
`viewer` checks, an admin route's *target* tenant is never the caller's own
`tenant_id`; `require_admin` returns the full `AuthContext`, including
`user_id`, so the route can log who acted).

**Audit trail — deliberately not `PipelineState.audit_log`:** that log
(`audit/logger.py`) is per-*run*, ephemeral pipeline-execution history,
persisted only as part of one run's JSON/SQLite artifact
(`audit/db.py::save_run`). A platform-admin action is not scoped to a run at
all (e.g. "list tenant X's runs," "view tenant Y's budget") and must survive
independent of any single run's lifecycle. New table `admin_action_log`
(migration `0017`) plus a new, standalone module,
`src/ai_etl/audit/admin_log.py` — deliberately **not** added to
`audit/db.py`, both because the concepts are genuinely different (a
cross-run operator-action log vs. a per-run pipeline-execution log) and to
avoid touching a 1300+-line file three other sprints in this batch also
touch in parallel (Sprint 31's own isolation constraint).

**Scope: read-only this sprint.** Every `/admin/*` route
(`api/routers/admin.py`) reads another tenant's data (run history, budget
status) or reads the admin log itself; none writes or mutates another
tenant's data. This mirrors ADR-022's own precedent of shipping
infrastructure narrower than the theoretical full scope and flagging the
rest explicitly (there, secrets storage without connector wiring; here,
admin visibility without admin mutation). A future admin *write* path (e.g.
force-cancel a stuck run for support purposes) is real, plausible future
work — deliberately not built here, since "can platform support silently
change a tenant's data" is a materially bigger trust decision than "can
platform support read it for triage," and deserves its own review.

**Alternatives considered:**

- **A `role` column on `users`, admin-settable via an endpoint** — rejected
  for the same reason ADR-022 rejected it for tenant roles: reinvents
  membership management Clerk already provides, and for platform admin
  specifically, would mean the *database* is the source of truth for "who
  can read any tenant's data," reachable by anyone who can write to that
  table — a materially worse blast radius than an env var only Railway
  config access can change.
- **Reusing `tenant_deletion_log`'s shape/table for admin actions** —
  rejected: that table's schema is deletion-specific (per-table row counts);
  forcing admin actions (which vary wildly in shape — a read vs. a future
  write) into those columns would be a worse fit than a purpose-built table
  with a generic `action`/`detail` shape.

---

## Decision 3 — Semgrep community rules as the CodeQL substitute

**Decision:** add `.github/workflows/semgrep.yml`, running Semgrep's free
community rulesets (`p/security-audit`, `p/secrets`, `p/python`,
`p/owasp-top-ten` for the Python backend; `p/typescript`, `p/react`,
`p/nextjs` for the Next.js frontend) on every PR and push to `main`.

**Why this closes the gap CodeQL couldn't:** CodeQL's blocker was entirely
a GitHub plan/billing decision (GHAS required for private-repo code
scanning) — not fixable from workflow config. Semgrep's OSS/CLI mode
(`semgrep scan` against public registry rulesets) requires no GitHub App,
no GHAS entitlement, and no `security-events: write` permission — it runs
as a plain CI job and fails the job (`--error`) on a match, same signal
shape a required check needs. **Deliberately does not upload SARIF** to
GitHub's code-scanning API — that upload path requires the same GHAS
entitlement CodeQL was blocked on, so it would silently recreate CodeQL's
exact failure mode.

**Calibrated against this repo before merging**, not assumed clean: ran the
exact configured ruleset (`p/security-audit`, `p/secrets`, `p/python`,
`p/typescript`, `p/react`, `p/nextjs`) against the full working tree
(2026-08-22). Result: five findings, all the same rule
(`python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text`),
all in `sources/postgres_source.py`, `sources/mysql_source.py`,
`sources/sqlite_source.py`, `destinations/postgres_dest.py` — exactly the
four call sites `SECURITY.md`'s own "SQL" section already documents as a
deliberate, reviewed exception (`# nosec B608`, regex-allowlisted table
identifiers, bandit already aware of and accepting this exact pattern).
Zero other findings, any severity, across the whole Python and
TypeScript/React/Next.js surface. The workflow excludes exactly that one
rule (`--exclude-rule`) with an inline comment pointing at `SECURITY.md`,
rather than either (a) leaving it in and having every future PR fail CI on
an already-accepted, unrelated-to-this-sprint pattern, or (b) editing the
four connector files to silence it — out of this sprint's scope and not
requested. Every other rule stays enabled and blocking.

**Alternatives considered:**

- **Snyk / Trivy / other free-tier SAST** — not evaluated in depth; Semgrep
  was chosen because its community ruleset is genuinely free with no
  account/token required for the configs used here (confirmed by running
  the exact CI command locally without any `SEMGREP_APP_TOKEN`), and its
  rule granularity (`--exclude-rule` on one specific check id) made the
  calibration in the paragraph above possible without a broader
  ruleset-vs-ruleset evaluation.
- **Re-enabling CodeQL's dormant `workflow_dispatch`-only config as-is** —
  not a fix; the underlying GHAS entitlement gap is unchanged. Left in
  place, untouched, exactly as `codeql.yml`'s own header comment already
  says: it activates for free the moment the GitHub plan changes.

---

## Decision 4 — Sandbox introspection bypass: accept the risk, defer real isolation

> **Superseded 2026-08-26 by [ADR-038](ADR-038-docker-sandbox-migration.md).**
> The project owner decided to migrate to a real Docker-isolated sandbox
> ahead of this Decision's own stated trigger ("self-serve, unvetted
> external signups"). `core/sandbox.py` now has an opt-in `"docker"` backend
> (`core/sandbox_docker.py`) that contains the introspection bypass
> documented below inside a network-disabled, read-only, resource-capped
> container — demonstrated, not just asserted, by
> `tests/integration/test_sandbox_docker.py`. The risk acceptance below is
> kept as-written for history; it no longer reflects this project's current
> posture. **Important caveat ADR-038 documents in full:** the `"docker"`
> backend is dev/local-verified only — this project's Railway deployment
> cannot run Docker-in-Docker (non-privileged containers), so the
> `"process"` backend described below remains the actual default in every
> deployed environment until a separate execution service ships as
> follow-up work. The risk below is mitigated in local development and
> designed, not yet mitigated in production.

**Decision: accept the risk for the current scope, do not implement Docker/
gVisor isolation this sprint.** `core/sandbox.py`'s documented limitation —
`exec()` with restricted globals is bypassable via introspection
(`().__class__.__mro__[1].__subclasses__()`) reaching already-imported
modules' `__globals__`, enough to reach arbitrary code execution within the
sandboxed child process — remains unmitigated. This is a conscious,
re-confirmed decision, not silence: it was already documented in ADR-003 and
ADR-007's own text; this ADR is the first to weigh it explicitly against
concrete alternatives and set an explicit trigger for revisiting it, rather
than letting it sit as an inherited footnote.

**What is and isn't at risk today, precisely:** the bypass grants arbitrary
code execution *inside* the already-isolated child process ADR-007
introduced — a separate OS process (`multiprocessing`, `spawn` context),
with `os.environ` cleared before any user code runs (closing the concrete
secret-exposure angle: `APP_DATABASE_URL`, `OPENAI_API_KEY`-style vars,
Clerk config are never reachable through it, since the child process never
holds them). What the bypass does **not** get contained by: OS-level process
isolation (no seccomp/cgroups/namespace sandboxing — a bypass could still
attempt filesystem access, network calls, or resource exhaustion within
whatever the child process's OS user can do), and no enforced CPU/memory
ceiling beyond the wall-clock `timeout_seconds` ADR-007 already enforces.

**Why accept now, deliberately:**

1. **Threat model fit.** Every LLM-generated code path through
   `execute_in_sandbox()` (Transformer, Analyst, Science) executes code the
   *tenant's own LLM prompt produced*, against *that same tenant's own
   uploaded/connected data*, inside a per-request child process, on a
   deployment with a small number of known, controlled tenants (Bruno's own
   testing plus early external testers this roadmap's audit was
   preparing for). A malicious actor exploiting this bypass today would need
   to already control the spec/prompt fed to their own pipeline run — i.e.,
   attack their own sandboxed process to affect infrastructure they don't
   otherwise have access to. This is a real, non-zero risk, not a
   theoretical one — but the blast radius of "arbitrary code in an
   env-scrubbed child process" is meaningfully smaller than "arbitrary code
   with production secrets," which ADR-007 already closed.
2. **Real isolation is a genuinely bigger change than this sprint's scope.**
   Docker-per-execution or gVisor requires: a container/microVM runtime
   available in the deployment environment (Railway's current setup was not
   verified to support this without infrastructure changes), a new
   image-build/distribution pipeline for whatever base image runs the
   sandboxed code, and re-plumbing `execute_in_sandbox()`'s
   `extra_globals`/`extra_modules` pickling contract (ADR-007's own design)
   into a shape that works across a container boundary rather than a
   `multiprocessing.Process`. This is infrastructure work, not a
   config flag — sizing it accurately requires its own investigation this
   sprint didn't have scope for, matching the roadmap's own framing
   ("decisão, não necessariamente implementação de isolamento novo agora").
3. **No incident, no evidence of exploitation** — the bypass has been
   documented and unmitigated since ADR-003 (Sprint 3-era) with zero
   reported or observed misuse.

**Alternatives considered:**

- **Docker-per-execution (chosen as the pre-requisite for public opening,
  not implemented now).** Real process + filesystem + network isolation via
  the kernel's own container primitives. Rejected for *this* sprint on
  scope/infrastructure-readiness grounds above, not on merit — this is the
  most likely eventual answer.
- **gVisor / Firecracker microVMs** — stronger isolation than plain Docker
  (a userspace kernel intercepting syscalls, or a full lightweight VM
  boundary), same category of infrastructure investment as Docker, same
  scope rejection. Worth a head-to-head evaluation against Docker when this
  becomes a scheduled implementation sprint, not decided here.
- **Patch the specific introspection vector (block `__class__`/`__mro__`
  access via a custom `__getattribute__` proxy or AST-level rejection of
  dunder-attribute chains)** — rejected as a false sense of security: this
  is a well-known cat-and-mouse game with Python's own object model (dozens
  of documented equivalent bypasses beyond `__mro__`); patching the one
  named in this codebase's comments would not close the class of bypass,
  only this specific incantation of it.
- **`RestrictedPython` or a similar third-party sandboxing library** — not
  evaluated in depth this sprint; a real candidate for the eventual
  Docker/gVisor sizing investigation, since it could reduce or eliminate the
  need for container-level isolation if its own restriction model proves
  airtight against the same introspection class of attack. Flagged as
  unexplored, not rejected.

**Trigger for revisiting this decision — explicit, not vague:** opening the
product to *self-serve, unvetted external signups* (as opposed to Bruno
personally vetting each early tester, which is today's actual process) is
the roadmap's own stated pre-requisite ("pré-requisito de abertura
pública") and this ADR's concrete line: real isolation (Docker/gVisor,
per the alternatives above) must land *before* that point, not after.

## Consequences

- **Positive:** `admin` access is now a real, narrower-than-`editor`-or-
  broader RBAC tier, resolved independently of tenant org membership, with
  every cross-tenant read recorded in a queryable, permanent log
  (`admin_action_log`) — the SOC2 self-assessment's largest finding is
  closed for read access; write access is explicitly flagged as future work,
  not silently assumed done.
- **Positive:** every PR now runs a real, free SAST gate
  (`.github/workflows/semgrep.yml`) with zero GHAS dependency — calibrated
  against this exact codebase to confirm it doesn't just fail on day one
  from an already-accepted pattern.
- **Positive:** both open security questions this project's own
  documentation already flagged (RLS-as-bypass, sandbox introspection) now
  have an explicit decision record with a stated trigger for revisiting,
  instead of sitting as inherited comments nobody formally signed off on.
- **Negative — accepted, explicit:** RLS remains a no-op for this app's own
  connection role; a future `tenant_id`-filter bug in application code has
  no database-level backstop. Mitigated only by the fact that every current
  query already binds `tenant_id` correctly (verified, not assumed).
- **Negative — accepted, explicit:** the sandbox introspection bypass
  remains fully exploitable by anyone who can get their own code into a
  Transformer/Analyst/Science prompt — i.e. any authenticated tenant,
  against their own sandboxed process. No new mitigation ships this sprint.
- **Negative — accepted, explicit:** platform-admin access is read-only;
  there is still no audited path for an admin to *act* on a tenant's behalf
  (e.g. cancel a stuck run), which real support operations will eventually
  need.
- **Negative:** `AI_ETL_PLATFORM_ADMINS` is a new operational secret/config
  Bruno must set in Railway; an unset value means the `admin` role is
  simply unreachable (fails closed — no platform admin exists — rather than
  failing open to everyone), consistent with `AI_ETL_SECRETS_ENCRYPTION_KEY`'s
  existing fail-closed precedent (ADR-022 Decision 4).

## Related

- [ADR-022](ADR-022-rbac-org-sso-tenant-secrets.md) — the `editor`/`viewer`
  RBAC this ADR extends with `admin`; `AI_ETL_SECRETS_ENCRYPTION_KEY`'s
  fail-closed precedent this ADR's `AI_ETL_PLATFORM_ADMINS` follows.
- [ADR-003](ADR-003-exec-sandbox.md) — original documentation of the
  introspection bypass, pre-unification.
- [ADR-007](ADR-007-unified-sandbox-policy.md) — the process-isolation +
  env-clearing mitigation this ADR does not extend, and the
  `extra_globals`/`extra_modules` pickling contract a future container-based
  sandbox would need to redesign.
- [ADR-025](ADR-025-tenant-data-deletion.md) — `tenant_deletion_log`, the
  precedent this ADR's `admin_action_log` follows (standalone table,
  additive migration, no FK to `users.id`).
- `docs/compliance/soc2-readiness-assessment.md` — the self-assessment
  whose largest finding (no auditable admin access) this ADR's Decision 2
  addresses.
- `SECURITY.md` — RLS posture (Decision 1) and sandbox limitation
  (Decision 4) source documentation this ADR formalizes into an explicit
  decision.
