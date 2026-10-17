# Incident Response Plan

**Date:** 2026-09-06
**Purpose:** SOC 2 CC1 gap closure — `SECURITY.md` documents *how to report*
a vulnerability; this document covers *how the team responds* once one is
confirmed. Sized to a solo-operator, pre-revenue reality: no on-call
rotation or dedicated security team to invent. Cross-links `SECURITY.md`'s
existing intake channel.

---

## Scope

Any confirmed security incident affecting this application: unauthorized
data access, a credential leak, a sandbox escape exploited beyond the
accepted-risk boundary (Risk 1, `risk-register.md`), a dependency CVE with
a known exploit in the wild, or a tenant-isolation failure.

Does not cover: routine bug reports with no security impact (regular GitHub
issue flow), or a `bandit`/`pip-audit`/Semgrep CI finding that is caught
and fixed before merge (that is the normal development process, not an
incident).

## Roles

**Single responder: Bruno Ribeiro** — owns detection, triage, containment,
notification, and post-mortem for every incident at this stage. This
document exists so that if the team grows, the process is already written
down rather than invented mid-incident.

## Phase 1 — Detection

Sources that can surface an incident:
- `SECURITY.md`'s reporting email (external report).
- Sentry (`core/observability.py::init_sentry()`, ADR-033) — an unhandled
  exception pattern consistent with exploitation attempts.
- CI security gates (`bandit`, `pip-audit`, Semgrep) flagging something
  already merged, not just something caught pre-merge.
- Manual discovery (code review, a red-team-style self-audit like
  `docs/archive/work/2026-08-24-full-technical-product-audit.md`).
- `admin_action_log`/`audit_log` review showing unexpected cross-tenant
  access patterns.

## Phase 2 — Triage (target: same day as detection)

1. Confirm the report is real — reproduce it, don't act on an unverified
   claim.
2. Classify severity using `risk-register.md`'s High/Medium/Low scale.
3. Identify blast radius: one tenant, a subset, or every tenant? Data
   read, data written, or code-execution-level compromise?

## Phase 3 — Containment (target: within RTO, 4h — ADR-042)

Depending on the finding:
- **Leaked credential** (API key, `APP_DATABASE_URL`, Clerk secret):
  revoke/rotate immediately at the provider dashboard, redeploy with the
  new value, confirm the old one no longer works.
- **Sandbox escape beyond the accepted-risk boundary**: disable the
  affected execution path (Transformer/Analyst/Science) via a feature
  flag or emergency deploy if no flag exists, until a fix ships.
- **Tenant-isolation failure**: identify every affected tenant via
  `audit_log`/`admin_action_log`, contain the specific query/endpoint
  (revert the offending deploy if the cause is a recent change).
- **Dependency CVE**: `pip-audit`-confirmed exploitable dependency — patch
  and redeploy ahead of the normal Dependabot cadence.

## Phase 4 — Tenant notification

- Notify affected tenants if the incident involved actual unauthorized
  access to their data (not merely a theoretical vulnerability window with
  no evidence of exploitation).
- Channel: the tenant's own configured notification destination
  (`services/alerting.py`, ADR-034) if appropriate, or direct email
  otherwise — this application has no in-app incident-status page today.
- LGPD Art. 48 / GDPR Art. 33-34 timing: notify the relevant authority
  within 72 hours of becoming aware, where the incident meets those
  articles' threshold (a personal-data breach with likely risk to data
  subjects) — legal judgment call at the time, not automated.

## Phase 5 — Post-mortem

- Written summary: what happened, root cause, what was fixed, what
  monitoring/control gap allowed it, what closes that gap.
- Update `risk-register.md` if the incident reveals a risk not already
  tracked, or changes an existing row's severity/likelihood.
- Add a `bugs-solved/` note (vault) if the root cause is a reusable lesson
  for future sessions, following the existing convention
  (`feedback_verify_os_platform_changes_on_real_target`-style notes).

## Out of scope for this version

- No dedicated status page or customer-facing incident communication tool
  — direct notification only, at this scale.
- No formal SLA on notification timing beyond the legal minimums above —
  revisit if a paying customer's contract requires a tighter commitment.

## Review cadence

Reviewed whenever an incident actually occurs (post-mortem feeds back into
this document), or at minimum alongside `risk-register.md`'s quarterly
review. Last reviewed: 2026-09-06.
