# LGPD / GDPR Personal Data Processing — Record of Processing Activities

**Date:** 2026-08-21
**Sprint:** 24 (post-TCC product roadmap)
**Legal basis for this document:** LGPD Art. 37 ("Registro das operações de
tratamento de dados pessoais") / GDPR Art. 30 ("Records of processing
activities"). This is the project's own record, written from a direct read
of `src/ai_etl/audit/models.py` and `src/ai_etl/audit/storage.py` — not
inferred from documentation alone.

**Two distinct roles this application plays**, load-bearing for everything
below:

1. **Controller**, for the tenant's own account data (`users` table — a
   Clerk user id, a spend cap, timestamps). Minimal personal data — no
   email, name, or IP address is stored in this application's own database;
   those live in Clerk (the identity provider), outside this app's data
   plane.
2. **Processor**, for whatever a tenant uploads or connects as a data
   source and runs through a pipeline. If a tenant's dataset contains their
   own customers'/employees' personal data (names, emails, CPF, addresses —
   exactly the scenario Vault `artefact/security.md` already flagged as a
   risk before this sprint), that data is processed and, until this sprint,
   had no confirmed way to be fully erased on request. This is the
   dominant compliance surface for this product, not the smaller
   controller role above.

---

## 1. What personal data is collected, and by whom

| Data | Role | Source | Table/location |
|---|---|---|---|
| Clerk user id (`tenant_id`) | Controller | Clerk (identity provider) | `users.id` |
| Account creation timestamp | Controller | Application-generated | `users.created_at` |
| Monthly spend cap (if set) | Controller | Tenant-configured | `users.monthly_budget_usd` |
| Pipeline spec / business question (free text) | Controller (unless the tenant writes personal data into it — see below) | Tenant-authored | `runs.spec`, `saved_pipelines.spec`/`business_question` |
| Third-party API credentials (e.g. a REST API key) | Controller (a credential, not personal data about a data subject, but sensitive) | Tenant-configured | `tenant_secrets.ciphertext` (Fernet-encrypted) |
| **Uploaded/connected dataset content** (post-extraction, post-transform) | **Processor** — the actual customer/employee data of the tenant's own business, if their dataset contains it | Tenant-uploaded CSV/Excel or tenant-configured live source (Postgres/MySQL/MongoDB/REST) | `{run_id}_silver.csv`, `{run_id}_gold_{i}.csv`, `{run_id}_analysis.json`, via `audit/storage.py` (local disk in dev, S3 in production — ADR-009) |
| Generated narratives/recommendations referencing dataset content | **Processor** — may quote or summarize personal data present in the source dataset (e.g. an Advisor narrative referencing a named customer) | LLM-generated, derived from the above | `{run_id}_analysis.json` |

**Not collected**: email, phone number, physical address, government ID,
biometric data, or any other identity attribute of the *account holder* —
that entire category is delegated to Clerk and never touches this
application's database or storage. This materially reduces the controller
surface, but does **not** reduce the processor surface (§ above), which
depends entirely on what a tenant chooses to upload/connect.

## 2. Purpose of processing

- **Controller data**: authenticate the tenant, enforce spend caps,
  associate pipeline runs with an account, bill (future — no billing system
  exists yet).
- **Processor data**: execute the ETL/analysis pipeline the tenant
  requested (extract → transform → quality-check → load; optionally
  Gold/Science/Advisor analysis) and persist an auditable record of what
  was done, per this project's own "auditable pipeline" value proposition
  (`audit_log`, ADR-002/ADR-004).

## 3. Where data is stored

- **Database**: Supabase-managed Postgres (`APP_DATABASE_URL`), a sub-
  processor. RLS enabled on every table (`SECURITY.md`).
- **Artifacts** (`audit/storage.py`, ADR-009): `local` disk in development
  (default, no tenant-directory separation — see ADR-025's investigation
  finding), or AWS S3 in production, prefixed
  `{environment}/{tenant_id}/...`. AWS is a sub-processor for production
  artifact storage.
- **LLM providers** (OpenAI/Anthropic/Google, per ADR-014): dataset samples
  and specs are sent to whichever provider is configured
  (`AI_ETL_LLM_MODEL`) as part of normal pipeline execution — this is a
  necessary sub-processor relationship for the product's core function
  (LLM-generated transformation code, narratives). **Prompts and full
  generated code are never logged** (`SECURITY.md`) — only metadata — but
  the LLM provider itself does receive dataset content as part of a
  request, per its own API contract and data-retention policy (not
  independently re-verified here, inherited from each provider's published
  terms).

## 4. Retention period

**No retention/deletion policy existed before this sprint** — `runs`,
`analysis_runs`, `saved_pipelines`, and their storage artifacts persisted
indefinitely, with no automatic expiry. This sprint does not add automatic
retention limits (out of scope — the roadmap's own DoD is "exclusão sob
pedido," not "retenção automática"), but does add the erasure mechanism a
retention policy would eventually call. **Flagged as a real follow-up**:
LGPD Art. 6 III / GDPR Art. 5(1)(e) ("storage limitation") expect data to be
kept only as long as necessary for its purpose — indefinite retention with
no policy is a genuine, not-yet-closed gap, distinct from (and larger than)
the on-request erasure this sprint closes. Recommended for a future sprint:
a configurable retention window with automatic purge, reusing this sprint's
same deletion primitives per-run rather than per-tenant.

## 5. How data is erased on request (the data subject's right to erasure)

**Before this sprint**: no mechanism existed. `DELETE /secrets/{name}`
(ADR-022) deleted one named credential; nothing deleted a tenant's run
history, saved pipelines, or storage artifacts. Confirmed gap (see
ADR-025's Investigation section for the full trace through the code).

**As of this sprint**: `DELETE /tenant` (self-service, `editor`-only,
requires an explicit `{"confirm": "DELETE"}` body — ADR-025) hard-deletes,
in order: storage artifacts (every CSV/JSON/figure a tenant's runs
produced), then `stage_latencies`, `analysis_runs`, `runs`,
`saved_pipelines`, `tenant_secrets`, and finally the `users` row itself. A
minimal `tenant_deletion_log` entry survives (tenant id, timestamps, row/key
counts, outcome) as evidence the request was fulfilled — this log entry is
not itself personal data about a living process (the account it describes
no longer exists), so it does not reintroduce the problem it documents. See
ADR-025 for the full design rationale, including its known limitations
(no cross-tenant/admin-initiated deletion; local-backend storage cleanup
depends on DB-derived key enumeration rather than a real tenant-prefixed
directory).

**Third-party sub-processor erasure**: this sprint does not (and cannot,
from the application layer) verify or force deletion from LLM providers'
own systems for data already sent as part of a completed API request — that
depends on each provider's own data-retention/deletion policy. Flagged as
an explicit limitation, not silently assumed handled.

## 6. Data subject rights — LGPD Art. 18 / GDPR Chapter III coverage

| Right | Mechanism |
|---|---|
| Access (Art. 18 II / GDPR Art. 15) | `GET /pipelines`, `GET /runs/{id}` and history endpoints — a tenant can already read back everything stored about its own account |
| Correction (Art. 18 III / GDPR Art. 16) | A tenant can `PATCH /pipelines`, `PATCH /budget`, rotate/delete secrets — covers configurable fields; historical run records are immutable by design (an audit trail should not be editable after the fact — same reasoning `SECURITY.md`/ADR-004 already apply to `audit_log`) |
| **Erasure (Art. 18 VI / GDPR Art. 17)** | **`DELETE /tenant`, new this sprint (ADR-025)** |
| Portability (Art. 18 V / GDPR Art. 20) | Partial — CSV/JSON downloads exist per-run today; no single "export everything" endpoint. Flagged as a future, lower-priority follow-up (not blocking, no roadmap item requests it yet) |
| Objection to automated decision-making (Art. 20 / GDPR Art. 22) | Not directly applicable — this product's pipelines execute on the tenant's own explicit request, not as an automated decision made *about* the tenant without their involvement |

## 7. Known limitations (explicit, not silently assumed closed)

- No automatic retention/expiry policy (§4) — deletion is on-request only.
- Local storage backend has no tenant-prefixed directory (ADR-025's
  investigation finding) — production runs on S3, where this doesn't apply,
  but a local/dev deployment's isolation depends on DB-derived key
  enumeration being complete.
- No verification of erasure at LLM sub-processors (§5).
- No data portability "export everything" endpoint (§6).
- No sub-processor register with each vendor's own compliance posture
  (tracked as a SOC2 gap too — see `soc2-readiness-assessment.md` CC9).

## Related

- [ADR-025](../adr/ADR-025-tenant-data-deletion.md) — the erasure mechanism.
- [ADR-006](../adr/ADR-006-clerk-auth-supabase-postgres-tenancy.md),
  [ADR-009](../adr/ADR-009-tenant-scoped-storage-and-config.md),
  [ADR-022](../adr/ADR-022-rbac-org-sso-tenant-secrets.md) — the tenancy,
  storage, and secrets models this document maps.
- `docs/compliance/soc2-readiness-assessment.md` — the companion SOC2
  self-assessment.
- Vault `artefact/security.md` — the original LGPD risk flag this sprint
  responds to.
