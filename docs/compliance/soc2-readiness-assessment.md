# SOC 2 Type I Readiness Self-Assessment

**Status:** Self-assessment only — not a certification, not an external audit.
**Date:** 2026-08-21
**Sprint:** 24 (post-TCC product roadmap)
**Scope:** Trust Services Criteria (AICPA), **Security** category only (the
mandatory "Common Criteria" — CC1 through CC9). Availability, Processing
Integrity, Confidentiality, and Privacy are additional, optional TSC
categories a real SOC 2 engagement can scope in later; not assessed here
because no external customer has requested them yet (see "Not in scope"
below).

**How to read this document:** each control lists what exists today, where
in the code/docs it's implemented, and — critically — whether it is a real
finding from reading the code, not an assumption. Gaps are called GAP and
prioritized P0 (blocks even a Type I engagement) / P1 (a real auditor would
flag it, fixable before or during the engagement) / P2 (worth fixing, not
blocking).

---

## CC1 — Control Environment

| Control | Status | Evidence |
|---|---|---|
| Documented security policies | ✅ Present | `SECURITY.md`, this document, ADRs |
| Defined ownership/accountability | ⚠️ Informal | Single-operator project (Bruno) — no separation of duties possible or expected at this scale. A real SOC 2 Type I engagement for a solo-founder company typically notes this as an accepted limitation, not a blocking gap, provided compensating controls (CI gates, code review via PR) exist. |

**GAP (P2):** No written incident response plan. `SECURITY.md` documents
*how* to report a vulnerability, not *how the team responds* once one is
confirmed (who does what, in what order, when customers are notified).
Low priority pre-revenue, but a real auditor will ask for this before any
Type I engagement starts.

## CC2 — Communication and Information

| Control | Status | Evidence |
|---|---|---|
| Security policy is documented and accessible | ✅ | `SECURITY.md` (repo root) |
| Architecture decisions are documented | ✅ | `docs/adr/` — 25 ADRs, one per material decision, checked into version control |
| Vulnerability reporting channel | ✅ | `SECURITY.md` — dedicated email, 72h acknowledgement SLA |

## CC3 — Risk Assessment

| Control | Status | Evidence |
|---|---|---|
| Known risks documented | ✅ | Vault `artefact/security.md` — risk register maintained since project inception |
| Risk reassessed as features ship | ✅ | Every ADR includes a "Consequences" section with negative/accepted trade-offs; `docs/CURRENT_STATE.md` tracks what's flagged vs. fixed across sprints |

**GAP (P1):** Risk assessment is narrative (Markdown), not a structured,
periodically-reviewed risk register with owners and review dates. Adequate
for a self-assessment; a real auditor will want a lighter-weight but more
structured artifact (e.g., a risk log with severity/likelihood/status
columns) before Type I fieldwork.

## CC4 — Monitoring Activities

| Control | Status | Evidence |
|---|---|---|
| CI runs security scanning on every change | ✅ | `bandit` + `pip-audit`, every PR (`make security`, CI workflow) |
| Audit trail of application actions | ✅ | `audit/logger.py::log_action()` — every agent action logged; `audit/db.py::save_run`/`save_analysis` persist it |
| Production health monitoring | ✅ | Railway's built-in deploy/service health; `saved_pipelines.consecutive_failures`/`last_status` (Sprint 15) for pipeline-level health; `core/observability.py::init_sentry()` (ADR-033, Sprint 34) for infra-level error tracking |

**GAP CLOSED (2026-09-06 update):** the original P1 finding here — no
centralized error tracking on unhandled API/worker exceptions — was closed
by Sprint 34 (ADR-033) and independently confirmed in production: Sentry
was seen initializing (`Sentry initialized (component=api)`/`(component=worker)`
in Railway logs) and later confirmed capturing a real event
(`docs/CURRENT_STATE.md`, 2026-08-30/2026-09-01 sessions). This
self-assessment predates that fix (dated 2026-08-21); left here, marked
closed, rather than silently deleted, so the history is traceable.

## CC5 — Control Activities (access control, change management)

| Control | Status | Evidence |
|---|---|---|
| Authentication | ✅ | Clerk, JWT verified locally via JWKS (ADR-006) |
| Authorization / RBAC | ✅ | `editor`/`viewer` roles, `require_role()` (ADR-022) |
| Tenant isolation | ✅ | `tenant_id`-scoped queries at every read/write call site (ADR-006); Supabase RLS enabled on every table as a database-level backstop (`SECURITY.md`, fixed 2026-08-21) |
| Secrets management | ✅ | Tenant-scoped, Fernet-encrypted at rest (ADR-022); deployment secrets via env vars only, `.env` gitignored |
| Change management | ✅ | Branch protection (never commit to `main`), PR + CI required, ADR required for architecture decisions, `make check` gate before merge |
| Code execution sandboxing | ✅ | `core/sandbox.py`, isolated subprocess, restricted globals, enforced timeout (ADR-003/ADR-007) |

**GAP — accepted position, not a fix (2026-09-06 update):** No formal
change-management *evidence trail* beyond Git/GitHub history — a real
Type I engagement wants a documented approval record (who approved what
PR, when) distinct from "the PR was merged." **Decision**: GitHub's own PR
approval/merge history is accepted as the evidence trail — branch
protection (never commit to `main`) plus required PR plus CI gate
(`make check`) are already enforced (`CLAUDE.md`), and building a separate
approval-tracking system with no real auditor behind it yet would be
paperwork theatre, not a real control improvement. **Revisit trigger**: a
real auditor's evidence-request template explicitly asking for more than
GitHub's native history. Tracked as row 7 in
`docs/compliance/risk-register.md`.

**GAP (P1) — mitigated, minimal scope (2026-09-06, ADR-047):** No
dedicated admin/operator role beyond read-only cross-tenant access
(ADR-032's own flagged limitation) — every tenant is `editor` over its own
data with no mediated "break-glass" operator write-access model for
support scenarios. A SOC2 auditor will ask "how does staff access customer
data for support, and is it logged." **Now**: every `/admin` route
requires a stated `reason`, logged verbatim (`admin_action_log.reason`,
migration `0022`); one real support write action (retrying a tenant's
stuck pipeline) is mediated through the application (`enqueue_analysis()`,
the same function scheduled fires use) instead of direct DB access. Not
every conceivable support scenario has a mediated route yet — only the one
this project has actually needed — see ADR-047 Consequences.

## CC6 — Logical and Physical Access Controls

| Control | Status | Evidence |
|---|---|---|
| Encryption in transit | ✅ | HTTPS enforced by Railway/Vercel at the platform edge |
| Encryption at rest (secrets) | ✅ | Fernet AEAD, `tenant_secrets.ciphertext` (ADR-022) |
| Encryption at rest (database) | ✅ | Supabase-managed Postgres — encryption at rest is a Supabase platform guarantee, not application-implemented; not independently verified by this project, inherited from the sub-processor |
| Encryption at rest (S3 artifacts) | ⚠️ Default only | AWS S3 default server-side encryption (SSE-S3) applies unless explicitly disabled; not explicitly configured/verified in `audit/storage.py` or IaC — bucket-level setting, owner-managed (ADR-009 scopes bucket creation as a manual step) |
| Least-privilege database role | ⚠️ Gap | App's Postgres role has `rolbypassrls = true` (`SECURITY.md`, ADR-032 Decision 1) — the opposite of least-privilege for RLS purposes, a conscious trade-off (see GAP below), not a passing control |

**GAP — accepted risk, not a fix (2026-09-06 update):** `SECURITY.md`
documents that the app's own connection role has `rolbypassrls = true`
(bypasses Row Level Security). This is *why* enabling RLS is safe today (it
only blocks `anon`/`authenticated`, not the app), but it also means the
app's own credential is maximally privileged — a compromised
`APP_DATABASE_URL` bypasses every tenant isolation guarantee this document
otherwise claims. **Decision, formalized in ADR-032 Decision 1**: keep
`rolbypassrls = true` for the current scope, with an explicit revisit
trigger (external paying tenants at scale). Tracked as row 2 in
`docs/compliance/risk-register.md`.

**GAP (P0, closed this sprint):** Until this sprint, there was no way to
fully remove a tenant's data from the system on request — see
`docs/compliance/lgpd-gdpr-data-processing.md` and ADR-025. This is as much
an access-control/data-lifecycle gap (CC6.1 — "the entity restricts access
... and removes access when no longer required") as it is a privacy gap;
closed by `DELETE /tenant` (ADR-025).

## CC7 — System Operations

| Control | Status | Evidence |
|---|---|---|
| Vulnerability scanning | ✅ | `pip-audit` (dependency CVEs), `bandit` (code patterns) on every CI run |
| Incident detection | ✅ | Sentry (ADR-033) — see CC4 update above |
| Backup / disaster recovery | ⚠️ Partial | RTO (4h)/RPO (24h) targets and recovery strategy documented (ADR-042, Supabase PITR); enablement/retention-window confirmation and a real test restore still pending — owner action, `docs/work/2026-09-06-mvp-monetization-readiness-plan.md` WS2 |

**GAP (P1) — partially closed:** No documented, tested backup/restore
procedure. **ADR-042 now documents** the recovery strategy (rely on
Supabase PITR) and sets RTO/RPO targets. What remains open is exactly what
ADR-042 itself flags: confirm PITR is actually enabled, its retention
window, and perform one real test restore. Tracked as row 4 in
`docs/compliance/risk-register.md`.

## CC8 — Change Management

Covered under CC5 above (this project treats change management as one
control activity, not a separate process) — no additional findings.

## CC9 — Risk Mitigation

| Control | Status | Evidence |
|---|---|---|
| Third-party/sub-processor risk | ✅ | `docs/compliance/sub-processors.md` (2026-09-06) — full list (Clerk, Supabase, Railway, Vercel, AWS S3, OpenAI, Anthropic, Google) with each vendor's own compliance posture recorded |

**GAP CLOSED (2026-09-06):** A real SOC2 report requires disclosing
sub-processors and their own compliance posture. Now recorded in
`docs/compliance/sub-processors.md`.

---

## Not in scope for this self-assessment

- **Availability**: no formal uptime SLA/SLO exists yet for a pre-revenue,
  single-customer product; Railway's own platform SLA is inherited but not
  independently reviewed.
- **Processing Integrity**: partially covered implicitly by the audit trail
  (`audit_log`) and Quality agent's deterministic checks, but not assessed
  against the formal TSC criteria — would need its own pass.
- **Confidentiality / Privacy** (as distinct TSC categories, separate from
  the Security-category access controls above): the LGPD/GDPR document
  (`lgpd-gdpr-data-processing.md`) covers the *legal* privacy obligation;
  the *TSC Privacy category*'s specific control set (notice, choice,
  collection limits, etc., per the AICPA criteria) has not been separately
  mapped.

## Summary — priority-ordered gap list

**Updated 2026-09-06** — see `docs/work/2026-09-06-mvp-monetization-readiness-plan.md`
for the plan that closed most of these.

| Priority | Gap | Status (2026-09-06) |
|---|---|---|
| P0 | Tenant data deletion end-to-end | **Closed** — ADR-025, `DELETE /tenant` |
| P0 | Formal change-approval evidence beyond Git history | **Accepted position** — GitHub PR history is the evidence trail; revisit if a real auditor asks for more |
| P1 | No admin/support access model + audit log for staff data access | **Mitigated, minimal scope** — read-only closed (ADR-032); `reason` required on every route + one mediated write action (pipeline retry), see ADR-047 |
| P1 | App DB role can bypass RLS (`rolbypassrls = true`) | **Accepted risk**, formalized in ADR-032 Decision 1 |
| P1 | No centralized error tracking/alerting | **Closed** — Sentry (ADR-033), confirmed live in production |
| P1 | No documented/tested backup-restore procedure | **Partially closed** — RTO/RPO + strategy documented (ADR-042); real test restore pending, owner action (WS2) |
| P1 | No sub-processor compliance-posture register | **Closed** — `docs/compliance/sub-processors.md` |
| P1 | Risk register is narrative, not structured | **Closed** — `docs/compliance/risk-register.md` |
| P2 | No written incident response plan | **Closed** — `docs/compliance/incident-response-plan.md` |

**Overall assessment**: this project is closer to Type I-ready than a
typical pre-revenue solo project — tenant isolation, encryption, sandboxing,
RBAC, audit logging, data erasure, error tracking, a structured risk
register, a sub-processor register, an incident response plan, and
reason-gated/mediated admin access are all real, implemented, and
tested/documented controls, not paper policy. The one gap still genuinely
open is a real test restore against Supabase PITR (owner action, not a code
fix — `docs/work/2026-09-06-mvp-monetization-readiness-plan.md` WS2).
Everything else a Type I engagement would ask for at this stage now has a
paper trail.
