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
| Production health monitoring | ⚠️ Partial | Railway's built-in deploy/service health; `saved_pipelines.consecutive_failures`/`last_status` (Sprint 15) gives pipeline-level health, not infra-level (no APM/error-tracking tool wired — e.g. no Sentry) |

**GAP (P1):** No centralized error tracking / alerting on unhandled
exceptions in the API/worker processes themselves (as opposed to pipeline
*business logic* failures, which Sprint 15 already handles). An
unhandled exception in `api/main.py` today is visible only via Railway logs,
not proactively alerted. Real gap for a production SaaS, independent of
SOC2 — flagged here because CC4 explicitly asks for it.

## CC5 — Control Activities (access control, change management)

| Control | Status | Evidence |
|---|---|---|
| Authentication | ✅ | Clerk, JWT verified locally via JWKS (ADR-006) |
| Authorization / RBAC | ✅ | `editor`/`viewer` roles, `require_role()` (ADR-022) |
| Tenant isolation | ✅ | `tenant_id`-scoped queries at every read/write call site (ADR-006); Supabase RLS enabled on every table as a database-level backstop (`SECURITY.md`, fixed 2026-08-21) |
| Secrets management | ✅ | Tenant-scoped, Fernet-encrypted at rest (ADR-022); deployment secrets via env vars only, `.env` gitignored |
| Change management | ✅ | Branch protection (never commit to `main`), PR + CI required, ADR required for architecture decisions, `make check` gate before merge |
| Code execution sandboxing | ✅ | `core/sandbox.py`, isolated subprocess, restricted globals, enforced timeout (ADR-003/ADR-007) |

**GAP (P0 for Type I fieldwork, not for this self-assessment):** No
formal change-management *evidence trail* beyond Git/GitHub history — a
real Type I engagement wants a documented approval record (who approved
what PR, when) distinct from "the PR was merged." GitHub's own PR
approval/merge history likely satisfies this in practice, but this hasn't
been validated against a real auditor's evidence-request template.

**GAP (P1):** No dedicated admin/operator role (ADR-022's own flagged
limitation) — every tenant is `editor` over its own data with no separate
"break-glass" operator access model documented for support scenarios. Not
urgent pre-enterprise-customer, but a SOC2 auditor will ask "how does staff
access customer data for support, and is it logged" — today the honest
answer is "direct DB access by Bruno, not mediated by the application, not
separately logged." This is the single most auditor-visible gap in this
assessment.

## CC6 — Logical and Physical Access Controls

| Control | Status | Evidence |
|---|---|---|
| Encryption in transit | ✅ | HTTPS enforced by Railway/Vercel at the platform edge |
| Encryption at rest (secrets) | ✅ | Fernet AEAD, `tenant_secrets.ciphertext` (ADR-022) |
| Encryption at rest (database) | ✅ | Supabase-managed Postgres — encryption at rest is a Supabase platform guarantee, not application-implemented; not independently verified by this project, inherited from the sub-processor |
| Encryption at rest (S3 artifacts) | ⚠️ Default only | AWS S3 default server-side encryption (SSE-S3) applies unless explicitly disabled; not explicitly configured/verified in `audit/storage.py` or IaC — bucket-level setting, owner-managed (ADR-009 scopes bucket creation as a manual step) |
| Least-privilege database role | ✅ | App's Postgres role documented as *not* the RLS-bypassing superuser by design intent, though `SECURITY.md` notes the current role does have `rolbypassrls = true` — see GAP below |

**GAP (P1):** `SECURITY.md` documents that the app's own connection role has
`rolbypassrls = true` (bypasses Row Level Security). This is *why* enabling
RLS is safe today (it only blocks `anon`/`authenticated`, not the app), but
it also means the app's own credential is maximally privileged — a
compromised `APP_DATABASE_URL` bypasses every tenant isolation guarantee
this document otherwise claims. A real hardening step (not done this
sprint, flagged for a future one) would be a dedicated non-bypassing
application role with explicit per-table grants, closing this gap
structurally rather than relying on application code discipline alone.

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
| Incident detection | ⚠️ Partial | See CC4 gap — no proactive error-tracking/alerting beyond pipeline-level health |
| Backup / disaster recovery | ⚠️ Undocumented | Supabase's own backup policy applies (platform-managed); no documented, tested restore procedure specific to this application |

**GAP (P1):** No documented, tested backup/restore procedure. Supabase
almost certainly has point-in-time recovery available on paid tiers, but
whether it's enabled, and whether a restore has ever been rehearsed, is
unverified — flagged, not assumed either way.

## CC8 — Change Management

Covered under CC5 above (this project treats change management as one
control activity, not a separate process) — no additional findings.

## CC9 — Risk Mitigation

| Control | Status | Evidence |
|---|---|---|
| Third-party/sub-processor risk | ⚠️ Undocumented | No explicit sub-processor list (Clerk, Supabase, Railway, Vercel, AWS S3, OpenAI/Anthropic/Google) with each vendor's own SOC2/security posture reviewed and recorded |

**GAP (P1):** A real SOC2 report requires disclosing sub-processors and
their own compliance posture (most of the above — Clerk, Supabase, Vercel,
AWS — publish their own SOC2 reports; this hasn't been collected into one
place). Cheap to fix (a table in a future doc), not done this sprint —
out of scope for "prepare the terrain," in scope for the actual engagement.

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

| Priority | Gap | Sprint 24 status |
|---|---|---|
| P0 | Tenant data deletion end-to-end | **Closed** — ADR-025, `DELETE /tenant` |
| P0 | Formal change-approval evidence beyond Git history | Not addressed — needs validation against a real auditor's request, not a code fix |
| P1 | No admin/support access model + audit log for staff data access | Not addressed — inherits ADR-022's known limitation |
| P1 | App DB role can bypass RLS (`rolbypassrls = true`) | Not addressed — flagged for a future hardening sprint |
| P1 | No centralized error tracking/alerting | Not addressed — infra gap, not SOC2-specific |
| P1 | No documented/tested backup-restore procedure | Not addressed |
| P1 | No sub-processor compliance-posture register | Not addressed |
| P1 | Risk register is narrative, not structured | Not addressed |
| P2 | No written incident response plan | Not addressed |

**Overall assessment**: this project is closer to Type I-ready than a
typical pre-revenue solo project — tenant isolation, encryption, sandboxing,
RBAC, audit logging, and now data erasure are real, implemented, and tested
controls, not paper policy. The remaining gaps are mostly *documentation and
process* (evidence trails, sub-processor registers, an incident response
plan) rather than missing technical controls, which is the cheaper category
to close before an actual paid engagement — consistent with this sprint's
explicit scope ("prepare the terrain," not certify).
