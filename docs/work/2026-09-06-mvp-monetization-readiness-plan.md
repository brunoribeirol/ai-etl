# 2026-09-06 — MVP monetization-readiness plan

**Status:** In progress.
**Owner:** Bruno Ribeiro (decisions) + Claude (execution)

## Objective

Close the real remaining gaps between today's state and a monetization-ready
MVP, so the owner needs to adjust as little as possible before onboarding a
paying tenant. Triggered by the owner asking for "an MVP FODA" after the
mega-audit remediation plan (`docs/work/2026-09-05-mega-audit-remediation-plan.md`)
closed — this plan picks up from there, scoped only to what is actually still
open (verified against current code/docs on 2026-09-06, not assumed from the
2026-08-21 SOC2 self-assessment, which is stale on two points — see below).

## Pre-planning correction — what turned out to already be done

Before writing this plan, each item the owner asked about was re-verified
against current code, not taken from the (dated) SOC2 assessment:

- **Sprint 30 gap (per-pipeline LLM provider/model override not reaching
  execution) — already fixed.** `core/state.py`'s `llm_provider_override`/
  `llm_model_override` are threaded through every `agents/pipeline/*.py` and
  `agents/analysis/*.py` node's `get_llm()` call (ADR-031 §5), including the
  avulso-run path (PRs #137/#138, closed same day). No further action.
- **Automatic data retention (LGPD Art. 6 III / GDPR Art. 5(1)(e)) — already
  built.** ADR-035 (Sprint 36): `services/retention_service.py`,
  `retention_cleanup_log` table (migration `0018`), Celery Beat task running
  every 24h (`AI_ETL_RETENTION_INTERVAL_SECONDS`). No further action.
- **Backup/restore — partially closed.** ADR-042 already sets RTO (4h)/RPO
  (24h) targets and the recovery strategy (rely on Supabase PITR). What it
  explicitly leaves open — "confirm PITR is enabled, its retention window,
  and perform one real test restore" — is still undone. This plan's WS2.

So this plan's real scope is smaller than the owner's original framing:
1 owner-executed verification (WS2), 4 short docs (WS1), and 1 scoped code
change (WS3) — not a rebuild of already-shipped features.

## Non-goals

- Not re-touching Sprint 30/ADR-031 or the retention system — already done.
- Not building a redundant `pg_dump` pipeline alongside Supabase PITR
  (ADR-042 Decision 2 already rejected this).
- Not defining a pricing model or wiring actual billing (Stripe etc.) —
  out of scope unless the owner asks for it explicitly next.
- Not attempting Availability/Processing Integrity/Confidentiality/Privacy
  SOC2 TSC categories (assessment's own "Not in scope" section) — Security
  category only, same boundary as the existing self-assessment.
- Not seeking a real SOC2 Type I engagement — this closes the *cheap*,
  defensible gaps; the P0 (formal change-approval evidence) is documented
  as an accepted position, not manufactured paperwork with no real auditor
  behind it.

## Assumptions

- No paying tenant exists yet — every risk-acceptance decision below is
  sized to pre-revenue, solo-founder stage, with an explicit revisit
  trigger (same pattern ADR-032/ADR-040 already use for `rolbypassrls`).
- The owner holds the Supabase dashboard credentials this plan's WS2
  needs — Claude cannot verify or change PITR settings directly.
- "Monetization-ready" means defensible to a technical evaluator or first
  paying customer's own due-diligence questionnaire, not a certified SOC2
  report.

## Affected contracts

- WS3 (admin write access) touches `api/deps.py::require_admin`,
  `api/routers/admin.py`, `audit/admin_log.py` — must not weaken
  `require_role`'s existing tenant isolation for non-admin users, and every
  new mutating admin route must call `log_admin_action()` before returning
  (existing non-negotiable pattern from ADR-032).
- No LangGraph node signature or `PipelineState` field changes in this plan.
- No SQL string interpolation — any new query uses SQLAlchemy `text()`
  with bound parameters.

## Risks

- **WS3 is the one item that expands the attack surface** (a mediated
  write path into another tenant's data, even if logged and reason-gated).
  Needs the tightest review of everything in this plan — see the scoping
  question below before implementation starts.
- Docs-only workstreams (WS1) carry no execution risk but are only as good
  as their accuracy — sub-processor list and risk register must reflect
  what is actually integrated today, not aspirational.
- WS2 depends on the owner actually running a test restore against a real
  (or cloned) Supabase project — if skipped, the gap stays open on paper
  only, same as before this plan.

## Acceptance criteria

- WS1: 4 new/updated docs merged via PR, `make check` green (docs-only, so
  mainly lint/format on any touched code fences).
- WS2: ADR-042 updated with the real, dated answer to "is PITR enabled, what
  window, was a restore actually tested" — closed only when that update
  lands, not when the runbook is written.
- WS3: scoped explicitly by the owner (see question below) before any code
  is written; once scoped, standard bar applies — tests (happy path +
  short-circuit + audit log + error), `make check` clean, PR merged.

---

## Workstream 1 — SOC2 documentation gaps (docs-only, low risk)

Branch: `docs/soc2-remaining-gaps`. One PR.

1. **Sub-processor register** — new `docs/compliance/sub-processors.md`:
   table of every third-party vendor with tenant/customer data exposure
   (Clerk, Supabase, Railway, Vercel, AWS S3, OpenAI, Anthropic, Google),
   what data each sees, and a link to that vendor's own published
   compliance posture (SOC2 report page, security page). Sourced from
   `docs/compliance/lgpd-gdpr-data-processing.md`'s existing sub-processor
   mentions plus `.env.example`/`core/llm.py`'s provider list — not
   invented.
2. **Structured risk register** — new `docs/compliance/risk-register.md`:
   convert vault `artefact/security.md`'s narrative risks into a table
   (risk / severity / likelihood / owner / mitigation / review date).
   Vault stays the canonical narrative source; this is the auditor-facing
   structured artifact SOC2 CC3 asked for.
3. **Incident response plan** — new `docs/compliance/incident-response-plan.md`:
   who does what when a security incident is confirmed (detection →
   triage → containment → tenant notification → post-mortem), sized to a
   solo-operator reality (no on-call rotation to invent). Cross-links
   `SECURITY.md`'s existing vulnerability-reporting intake.
4. **Change-approval evidence position** — short addition to
   `docs/compliance/soc2-readiness-assessment.md`'s CC5 gap: document the
   accepted position that GitHub's own PR-approval/merge history is the
   evidence trail (branch protection + required PR already enforced per
   `CLAUDE.md`), explicitly not building a separate approval system, with
   a revisit trigger (a real auditor's evidence-request template asking
   for more).
5. Update `soc2-readiness-assessment.md`'s summary table: mark the error-
   tracking gap (Sentry) as closed (was already true, doc was stale), and
   point each remaining P1 row at its new doc.

## Workstream 2 — Backup/restore verification (owner action)

Not a branch — no code. Claude writes a short verification runbook (folded
into WS1's PR, appended to ADR-042, or its own tiny doc — decide at write
time); the owner performs the actual Supabase dashboard check + one test
restore, then reports the result back so ADR-042's Consequences section can
be updated with real dates/findings instead of "unverified."

## Workstream 3 — Admin mediated write-access + support audit log (code, needs scoping first)

**Do not start until the owner answers the scoping question below.** This is
the one item in this plan that is a real, non-trivial code change touching
auth/security, and ADR-032 itself flagged it as "future work" rather than
something to build reflexively.

Candidate minimal scope, reusing 100% of existing infrastructure
(`require_admin()`, `admin_action_log`, `api/routers/admin.py`'s existing
pattern):
- Every existing/new admin route gains a required `reason: str` field
  (freeform justification, logged verbatim in `admin_action_log.detail`) —
  turns today's unmediated "admin can read" into "admin must state why,"
  which is most of what a SOC2 auditor is actually checking for.
- One concrete write action as the proof point — e.g. an admin-triggered
  retry of a stuck/failed run on a tenant's behalf (a real support scenario
  that exists today only as "Bruno does it by hand in the DB") — mediated
  through the application, reason-logged, instead of direct DB access.

Alternative (broader) scope: a general-purpose "support session" concept
(time-boxed, every query during the session logged) — more auditor-
convincing, meaningfully more code and design surface.

## Suggested execution order

1. WS1 (docs) — no dependencies, start immediately.
2. WS2 (owner verifies Supabase) — can run in parallel with WS1, owner's own
   time.
3. WS3 — only after the owner picks a scope; own branch, own PR, full
   `make check` + live-verification of the new admin endpoint against a
   real (non-production) tenant before merge, per this project's standing
   rule for anything touching auth.
