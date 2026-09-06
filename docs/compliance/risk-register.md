# Structured Risk Register

**Date:** 2026-09-06
**Purpose:** SOC 2 CC3 — a periodically-reviewed, structured artifact
(severity/likelihood/owner/review date), distinct from the narrative risk
discussion in vault `artefact/security.md` (canonical source for *why*,
kept as-is) and from `docs/adr/`'s per-decision Consequences sections
(canonical source for *what was decided*). This document is the auditor-
facing summary that CC3 asks for; it does not replace either of those.

**Severity/Likelihood scale:** High / Medium / Low, qualitative — this
project has no incident history to size a quantitative model from (same
honest constraint ADR-033's SLO and ADR-042's RTO/RPO already stated
explicitly rather than inventing false precision).

**Owner:** Bruno Ribeiro (solo operator) for every row — no separation of
duties exists at this stage; noted once here rather than repeated per row.

---

| # | Risk | Severity | Likelihood | Mitigation | Status | Review date |
|---|---|---|---|---|---|---|
| 1 | Sandbox `exec()` introspection bypass (`().__class__.__mro__[1].__subclasses__()`) allows arbitrary code execution inside the sandboxed child process | High | Low (requires a maliciously-crafted LLM prompt/dataset reaching the Transformer/Analyst/Science exec path) | Env vars cleared before user code runs (ADR-007); seccomp filter denies `execve`/`execveat` (ADR-046); accepted risk for current scope, revisit trigger = external self-serve signup at scale (ADR-032 Decision 4) | Accepted, mitigated | Next: on any signup-volume change |
| 2 | `rolbypassrls = true` on the app's own Postgres role — a leaked `APP_DATABASE_URL` bypasses every RLS tenant-isolation guarantee | High | Low (requires credential leak, not just an app bug) | RLS enabled as defense-in-depth against a leaked Supabase anon key (ADR-040); app-level `tenant_id` scoping is the primary isolation control; accepted risk, revisit trigger = external paying tenants at scale (ADR-032 Decision 1) | Accepted, mitigated | Next: on first paying-tenant cohort |
| 3 | No mediated/audited admin write-access path — support access to a tenant's data today is direct DB access, unmediated, unlogged | Medium | Medium (only exercised when the operator manually intervenes) | Read-only `admin_action_log` exists (ADR-032); write-access mediation scoped in `docs/work/2026-09-06-mvp-monetization-readiness-plan.md` WS3 | In progress | This plan's WS3 completion |
| 4 | Backup/restore for the application Postgres database unverified — PITR enablement and a real restore have not been confirmed | Medium | Low (Supabase-managed, but never independently tested) | RTO/RPO targets set (ADR-042); verification + one test restore pending, owner action | Open | Owner-scheduled, see this plan's WS2 |
| 5 | No verification that LLM sub-processors (OpenAI/Anthropic/Google) delete data after a completed request | Low | Low | Governed by each provider's own enterprise/API data-retention terms; outside this application's control layer | Accepted | Revisit if a customer's DPA requires stronger guarantees |
| 6 | Uploaded/connected datasets may contain personal data (names, emails, CPF) the tenant is the processor for | Medium | Medium (depends entirely on tenant-supplied data) | On-request full erasure (ADR-025); automatic artifact retention/expiry (ADR-035); tenant remains the data controller for their own dataset content | Mitigated | Ongoing — no fixed date, tied to feature changes touching storage |
| 7 | No formal change-approval evidence trail beyond GitHub's own PR history | Low | Low | Branch protection + required PR + CI gate (`make check`) enforced (`CLAUDE.md`); GitHub's PR approval/merge history accepted as the evidence trail — see `docs/compliance/soc2-readiness-assessment.md` CC5 | Accepted | Revisit if a real auditor's evidence-request template asks for more |
| 8 | Single-operator project — no separation of duties | Low | N/A (structural, not an event risk) | Compensating controls: CI gates, PR-based change management, audit logging | Accepted | Revisit if the team grows |

## Review cadence

Reviewed at minimum once per quarter, or immediately after any incident,
new sub-processor, or architecture decision that introduces a new risk
class. Last reviewed: 2026-09-06.
