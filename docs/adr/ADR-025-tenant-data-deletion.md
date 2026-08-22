# ADR-025: Tenant Data Deletion (LGPD/GDPR Right to Erasure)

**Status:** Accepted
**Date:** 2026-08-21
**Sprint:** 24 (post-TCC product roadmap — compliance enterprise)

## Context

Sprint 24's roadmap entry bundles a SOC2 Type I self-assessment and formal
LGPD/GDPR personal-data documentation, flagging as *probable, not assumed* a
new feature: "delete a tenant's data on request." This ADR covers only that
feature — the self-assessment and data-processing documentation are
non-code deliverables (`docs/compliance/soc2-readiness-assessment.md`,
`docs/compliance/lgpd-gdpr-data-processing.md`) that do not need an ADR per
this project's own convention (ADR is for architecture/implementation
decisions, not for research/policy documents).

### Investigation (before any code) — is this actually a gap?

1. **What personal data does this app actually hold?** Read
   `src/ai_etl/audit/models.py` directly rather than assuming:
   - `users`: `id` (Clerk user id, an opaque identifier — no email/name/IP
     stored locally; that lives in Clerk, outside this app's data plane),
     `created_at`, `monthly_budget_usd`. Low sensitivity on its own.
   - `runs`, `analysis_runs`, `stage_latencies`, `saved_pipelines`: all
     FK'd to `users.id` via `tenant_id`. `runs.spec`/`saved_pipelines.spec`/
     `business_question` are free-text fields a tenant writes themselves —
     can legally contain personal data if the tenant references it (e.g. "a
     coluna de e-mail do cliente X").
   - `tenant_secrets` (ADR-022): Fernet-encrypted third-party API
     credentials, tenant-scoped.
   - **The real exposure, easy to miss by only reading the DB schema**: the
     artifacts `audit/storage.py` persists per run —
     `{run_id}_silver.csv`, `{run_id}_gold_{i}.csv`, `{run_id}_analysis.json`
     — are the tenant's **actual uploaded dataset content**, post-transform.
     A tenant using this product on customer data (names, emails, CPF,
     addresses — exactly the roadmap's own worked concern, Vault
     `artefact/security.md`) has that data sitting in `./runs/` (local) or
     the S3 bucket (ADR-009), scoped by `{environment}/{tenant_id}/...` for
     S3, **not tenant-prefixed at all for local** (`get_storage_backend`'s
     `LocalStorageBackend(log_dir)` branch ignores `tenant_id`; only the S3
     branch uses it in the key prefix) — confirmed by reading
     `get_storage_backend` directly, not assumed from the ADR-009 docstring
     alone. This makes this app a data **processor** for a tenant's own
     third-party subjects' data, not just a controller for its own account
     data — the LGPD/GDPR erasure obligation is real, not hypothetical.
2. **Does any existing feature delete this today?** Grepped the whole
   `api/routers/` and `services/` tree for `DELETE`-shaped tenant-wide
   operations: `DELETE /secrets/{name}` (ADR-022) deletes one named secret
   only; nothing deletes a `users` row, a tenant's `runs`/`saved_pipelines`
   history, or its storage artifacts. **Confirmed gap, not assumed** — the
   roadmap's own "provavelmente" is correct.
3. **Is there a role that should gate this?** ADR-022 (Decision 1) is
   explicit that no admin/billing role exists yet — `editor`/`viewer` is the
   entire role model, and `editor` already gates every other
   irreversible/mutating self-service action (`PATCH /budget`,
   `POST/DELETE /secrets`). Building a separate admin role just for this one
   endpoint is out of scope and would re-open ADR-022's own deferred
   question under time pressure; `editor`-gated self-service (a tenant
   deletes *its own* data, the same trust boundary as everything else this
   app lets a tenant do to itself) is the right scope for this sprint.

## Decision 1 — Scope: self-service, own-tenant-only deletion; no cross-tenant admin deletion

`DELETE /tenant` (new router, `editor`-only via `require_role("editor")`, no
path parameter — always acts on the caller's own resolved `tenant_id`, the
same pattern `PATCH /budget` already uses). A tenant can never delete
another tenant's data — there is no admin role able to target an arbitrary
`tenant_id`, by design: this app has no operator role today (ADR-022), and
building one is out of this sprint's scope. If a real enterprise customer
later needs "our org admin deletes a departed member," that composes with
ADR-022's Organizations model in a future sprint, not this one.

**Alternative considered and rejected**: a Railway/CLI-only operator script
(no API surface at all). Rejected because LGPD/GDPR's "right to erasure"
implies a data subject can *request* deletion and expect it to happen in a
bounded, auditable way — an API endpoint a tenant calls themselves (or that
a support flow calls on their explicit request) is more defensible evidence
of "this works end-to-end" than a manual script Bruno runs by hand, and
costs almost nothing extra to build given `require_role` already exists.

## Decision 2 — Hard delete of tenant content; a separate, minimal deletion-event log survives

Personal data (the tenant's own `runs`/`analysis_runs`/`stage_latencies`/
`saved_pipelines`/`tenant_secrets` rows, the `users` row itself, and every
storage artifact under that tenant) is **hard-deleted**, not soft-deleted.
Erasure means erasure — a `deleted_at` flag that leaves the actual
personal-data columns intact would not satisfy LGPD Art. 18 VI / GDPR Art.
17 on inspection, and this app has no legal-hold/retention requirement that
would justify keeping it (no billing/invoicing system yet that would need
historical spend rows to survive a tenant's own account).

A new, narrow `tenant_deletion_log` table (migration `0014`) is the one
exception, and deliberately does **not** FK to `users.id` (the row it
describes is gone by the time the log entry is written) — it stores the raw
`tenant_id` string (an opaque Clerk id, not itself sensitive — same
classification `users.id` already has today), `requested_at`,
`completed_at`, per-table row counts deleted, `storage_keys_deleted`,
`status` (`"completed"` | `"failed"`), and `error` (nullable). This exists
for exactly the SOC2 side of this sprint: "prove a deletion request was
fulfilled" needs a durable record that isn't itself deleted by the
deletion it describes. This is evidence of compliance, not personal data
about the tenant — it doesn't recreate the erasure problem it's solving.

**Alternative considered**: soft-delete (`is_deleted` flag on `users`,
filtered out of every query). Rejected — this project's tenant-scoping
convention (ADR-006) is explicit `.where(tenant_id == ...)` clauses at
~20+ call sites; retrofitting every one of them to also exclude
soft-deleted tenants is a much larger, more error-prone surface than a
one-time hard delete, and does not actually satisfy "erasure" (the data
still exists, just hidden) — it would need a second, later hard-delete pass
anyway, so it only defers the real work.

## Decision 3 — Deletion order: application-level cascade, not `ON DELETE CASCADE`

No existing FK from `runs`/`analysis_runs`/`stage_latencies`/
`saved_pipelines`/`tenant_secrets` to `users.id` has `ondelete="CASCADE"`
today (`models.py`, confirmed by reading every FK declaration) — deleting a
`users` row while dependents exist would hit a Postgres FK violation.
Rather than a migration altering five FK constraints' `ondelete` behavior
(riskier — `ON DELETE CASCADE` on `users` would also silently cascade-delete
`runs`/`analysis_runs` if a `users` row were ever deleted through any other
path, e.g. a future support tool, with no chance to run the storage-artifact
cleanup first), `services/tenant_deletion_service.py` deletes explicitly, in
one order, inside one DB transaction: `stage_latencies` → `analysis_runs` →
`runs` → `saved_pipelines` → `tenant_secrets` → `users`. This order is safe
under the *existing* FK graph without any FK-behavior change: nothing else
references `runs`/`analysis_runs`/`stage_latencies` rows by FK, and
`saved_pipelines` is only referenced by `runs.saved_pipeline_id`/
`analysis_runs.saved_pipeline_id` (`ON DELETE SET NULL`), which are already
gone by the time `saved_pipelines` rows are deleted.

Storage artifacts are deleted **before** the DB transaction starts, not
after — deriving the exact key set (`{run_id}.json`,
`{run_id}_transform.py`, `{run_id}_silver.csv`, `{run_id}_analysis.json`,
`{run_id}_gold_{i}.csv`/`_fig.json`, `{run_id}_science_{i}.csv`/`_fig.json`
for `i` in `range(gold_subtasks)`/`range(science_subtasks)`, per the
existing naming convention in `audit/db.py::save_run`/`save_analysis`)
requires reading `runs`/`analysis_runs` rows first, before they're deleted.
`StorageBackend` gains `delete_bytes(key) -> None` (ADR-009's Protocol,
extended) — a no-op if the key doesn't exist, so a partially-persisted run
(e.g. no Silver CSV because the pipeline failed before that stage) never
raises. Storage deletion is best-effort and logged, not
transactional with the DB delete — an S3 object failing to delete (network
blip) must not block the DB-side erasure of the same tenant's rows, since
the DB rows are the higher-confidence signal an auditor/regulator would
check first; a failed storage key is recorded in the `tenant_deletion_log`
row's `error` field (non-fatal — `status` stays `"completed"` with a
non-empty `error` noting the partial storage failure) so it's visible for a
manual follow-up, not silently swallowed.

**Known limitation, explicit**: `LocalStorageBackend` doesn't prefix by
tenant (only `S3StorageBackend` does — see Investigation §1), so the
local-backend deletion path relies entirely on enumerating exact keys from
DB rows, not on removing a tenant directory. If a local-backend artifact
was ever written outside the `save_run`/`save_analysis` naming convention
(there is none today), it would not be caught by this cleanup. Flagged, not
silently assumed complete; `local` is the dev-only default backend anyway
(ADR-009) — production runs on `s3`, where this limitation does not apply.

## Decision 4 — Idempotency and confirmation

The endpoint requires an explicit body `{"confirm": "DELETE"}` (a Pydantic
`Literal["DELETE"]` field) — mirrors the "type the resource name to confirm"
pattern common for irreversible actions (GitHub repo deletion), cheap to
implement, and prevents an accidental `DELETE /tenant` with no body/a stray
client retry from immediately destroying data. Calling it a second time
after a tenant's data is already gone returns `404` (mirrors
`get_saved_pipeline`'s existing "no row for a deleted resource" contract) —
this endpoint is not designed to be silently idempotent past the first
successful call, since a second call from a compromised session should be
visibly rejected, not silently accepted as a no-op.

## Consequences

- **Positive**: LGPD Art. 18 VI / GDPR Art. 17 now has a real, working,
  tested end-to-end erasure path — closes the roadmap's flagged gap.
- **Positive**: `tenant_deletion_log` gives SOC2 self-assessment real
  evidence for the "data subject requests are fulfilled and recorded"
  control, without reintroducing the personal data it just erased.
- **Positive**: no FK `ondelete` behavior changed on existing tables — zero
  risk of an unrelated future code path accidentally cascading a deletion
  it didn't intend.
- **Negative — accepted, explicit**: no cross-tenant admin deletion (a
  support agent cannot delete *another* user's tenant on their behalf via
  this API yet) — acceptable for a single-operator, pre-enterprise-customer
  stage; the roadmap's own DoD only requires "funciona ponta a ponta,"
  which self-service satisfies.
- **Negative — accepted, explicit**: storage deletion for the `local`
  backend depends on DB-row-derived key enumeration, not a real
  tenant-prefixed directory removal (see Decision 3's known limitation).
- **Negative**: this is a genuinely irreversible, destructive endpoint —
  covered by the `confirm` field and `editor`-only gating, but no
  "cancel within N days" grace period exists (out of scope; the roadmap
  does not ask for one, and adding one would need a whole deferred-deletion
  scheduler this sprint doesn't otherwise need).

## Related

- [ADR-006](ADR-006-clerk-auth-supabase-postgres-tenancy.md) — the tenancy
  model (`tenant_id` = Clerk user id) this ADR erases.
- [ADR-009](ADR-009-tenant-scoped-storage-and-config.md) — the storage
  abstraction (`StorageBackend`) this ADR extends with `delete_bytes`.
- [ADR-022](ADR-022-rbac-org-sso-tenant-secrets.md) — the `editor`/`viewer`
  role model this ADR reuses for gating, and the still-open "no admin role"
  limitation this ADR deliberately does not resolve.
- `docs/compliance/soc2-readiness-assessment.md`,
  `docs/compliance/lgpd-gdpr-data-processing.md` — the non-code
  deliverables this sprint also produces.
