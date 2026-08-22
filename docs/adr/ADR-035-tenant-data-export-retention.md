# ADR-035: Tenant Data Export and Automatic Retention

**Status:** Accepted
**Date:** 2026-08-22
**Sprint:** 36 (post-TCC product roadmap — compliance enterprise, part 2)

## Context

ADR-025 (Sprint 24) closed LGPD Art. 18 VI / GDPR Art. 17 (right to
erasure) with `DELETE /tenant`, and its own Consequences section flagged
what was still open: no data-export endpoint (LGPD Art. 9º/GDPR Art. 15,
"right of access") and no automatic retention policy — deletion has always
been on-request only, never by expiration. The Sprint 24 self-assessment
(`docs/compliance/soc2-readiness-assessment.md`) called the missing
retention policy the *larger* gap of the two, since it has no request
trigger at all — data simply accumulates in storage forever unless a tenant
explicitly asks for full erasure.

This ADR covers both, since they share the same underlying data model
question (what does "everything a tenant owns" mean, concretely, as a set
of rows/artifacts) that ADR-025 already answered once.

## Decision 1 — Export scope: same table set as ADR-025's deletion scope, secrets metadata only, artifact keys not bytes

`GET /tenant/export` reads exactly the rows `tenant_deletion_service`
already knows how to erase — `runs`, `analysis_runs`, `stage_latencies`,
`saved_pipelines`, plus the `users` row itself. This isn't a coincidence:
`services/tenant_deletion_service.py`'s `candidate_storage_keys_for_run` and
`tenant_run_storage_candidates` (both made public in this sprint, previously
prefixed `_`) are shared verbatim between deletion and export — one
naming-convention derivation for "what storage keys might this run have
produced," never two copies that could silently drift apart on a future
change to `save_run`/`save_analysis`.

`tenant_secrets` rows are included **metadata only** — `id`, `name`,
timestamps — never `ciphertext` (the Fernet-encrypted credential), decrypted
or not. A data-access request is about *personal data the tenant is a
subject of*, not about handing back their own stored third-party API keys
in a bulk JSON export; the ciphertext is already retrievable through the
existing tenant-scoped secrets flow if genuinely needed, and this project's
own logging discipline (`services/secrets_service.py`) already treats
`ciphertext` as never-log, never-echo-back-in-bulk.

Storage artifacts are exported as **keys and metadata** (`run_id`, `key`),
not as inline bytes. Rejected alternative: streaming every CSV/JSON artifact
back inline in the export response. A tenant's actual dataset content can be
arbitrarily large (the same content ADR-025 identified as the real exposure
surface); inlining it would make `GET /tenant/export` an unbounded-size,
slow, memory-heavy endpoint for no compliance benefit — LGPD Art. 9º/GDPR
Art. 15 requires disclosing *what* is held and *that* the tenant can access
it, not necessarily bundling every byte into one HTTP response. A tenant
that wants a specific artifact's contents already has read access to it
through the product's normal run-history UI. This is a conscious,
documented trade-off, not an oversight — flagged here so a future sprint
doesn't "fix" it without re-deriving this reasoning.

Access is `viewer`-and-above (not `editor`-only like `DELETE /tenant`) — a
read of one's own data is a lower-trust action than an irreversible mutation,
matching this project's existing `GET /budget`/`GET /pipelines` tier.
Same scope-strict pattern as ADR-025 Decision 1: always the caller's own
resolved `tenant_id` from the verified JWT, never a path/query parameter —
there is still no admin role able to export another tenant's data (ADR-022's
still-open limitation, deliberately not resolved here either).

## Decision 2 — Retention: opt-in per-tenant window, storage artifacts only, DB rows and `users` untouched

A new nullable `users.retention_days` column (migration `0018`), same shape
as `monthly_budget_usd` (ADR-019) — `NULL` (the default for every existing
and new tenant) means "keep forever," zero behavior change unless a tenant
opts in via `PATCH /tenant/retention`. **Rejected alternative**: a single
global retention window (an env var, applied to every tenant uniformly).
Rejected because retention needs differ per tenant/contract (an enterprise
customer's data-processing agreement may mandate a specific window; a
self-serve trial tenant may want none) — a global knob would either be too
aggressive for tenants who need long-lived history for drift detection
(ADR-018) and comparable-run views (ADR-017), or too lax to matter for
tenants who do need a short window. Per-tenant, opt-in mirrors ADR-019's own
reasoning for why the budget cap is per-tenant, not global.

**Retention only deletes storage artifacts, never `runs`/`analysis_runs` DB
rows, and never the `users` row.** This is the one place this ADR
deliberately diverges from ADR-025's "hard delete, full cascade" model:
ADR-025's own investigation (§1) identified the storage artifacts — the
tenant's actual uploaded/transformed dataset content — as the real exposure
surface for personal data; the DB rows (`runs.spec`, cost, status,
timestamps) are lower-sensitivity execution metadata that this project's
own dashboards, run-history views, and drift detection depend on staying
queryable indefinitely. Automatically deleting DB rows on a timer would
silently break `get_previous_completed_run` (ADR-018) and the pipeline
comparable-run history (ADR-017) for any tenant who opts into retention —
an unacceptable side effect for a feature meant to reduce personal-data
exposure, not degrade product functionality. A tenant who wants their DB
rows gone too already has `DELETE /tenant` (ADR-025) for that.

Cleanup runs as a Celery beat task (`services/retention_service.py`,
`ai_etl.cleanup_expired_retention`), same infrastructure `services/
scheduler.py` (ADR-016) already established — a daily cadence
(`AI_ETL_RETENTION_INTERVAL_SECONDS`, default 24h), far coarser than the
scheduler's 60s pipeline-firing tick, since this is a compliance sweep, not
a latency-sensitive job. Each tick lists every tenant with a configured
window (`list_tenants_with_retention`) and, per tenant, deletes every
storage key belonging to a run older than that window — reusing
`candidate_storage_keys_for_run` from `tenant_deletion_service.py` (see
Decision 1). Best-effort per artifact: a single storage failure is recorded
and does not stop the rest of the sweep for that tenant, and one tenant's
total failure does not block any other tenant in the same tick — the exact
same "one bad unit must not break the tick" contract
`check_scheduled_pipelines_task` already uses for pipeline firing.

A `retention_cleanup_log` row (migration `0018`) is written per tenant per
tick — `runs_scanned`, `storage_keys_deleted`, `status`, `error` — the same
"survives what it describes" evidence shape as `tenant_deletion_log`
(ADR-025 Decision 2), but recording a recurring pass rather than a single
irreversible event. Not FK'd to `users.id`, same reasoning as
`tenant_deletion_log`: a tenant that later deletes their account (ADR-025)
must not cascade-delete this compliance evidence too.

## Consequences

- **Positive**: LGPD Art. 9º/GDPR Art. 15 (right of access) now has a real,
  working, tested self-service endpoint — closes the gap ADR-025 itself
  flagged as open.
- **Positive**: retention closes the larger of the two gaps the Sprint 24
  self-assessment identified — data no longer accumulates in storage
  forever by default for a tenant who opts into a window.
- **Positive**: zero duplication between deletion, export, and retention's
  storage-key derivation — one function, three callers.
- **Negative — accepted, explicit**: export ships artifact keys/metadata,
  not inline bytes (Decision 1) — a tenant wanting the actual dataset
  content of an old artifact still uses the normal product UI, not the bulk
  export.
- **Negative — accepted, explicit**: retention purges storage only, never DB
  rows (Decision 2) — a tenant who also wants their run history/metadata
  gone still needs `DELETE /tenant` (ADR-025); retention alone is not a
  complete erasure path.
- **Negative — accepted, explicit**: same no-cross-tenant-admin limitation
  as ADR-025 — export is self-service only, no support-initiated export on
  a tenant's behalf yet (composes with ADR-022's Organizations model and the
  Sprint 31 `admin` role in a future sprint, not this one).

## Related

- [ADR-025](ADR-025-tenant-data-deletion.md) — the deletion model, storage
  candidate-key derivation, and scope-strict access pattern this ADR reuses
  and extends.
- [ADR-019](ADR-019-tenant-budget-cap.md) — the per-tenant nullable-column
  shape `retention_days` mirrors.
- [ADR-016](ADR-016-scheduled-pipelines-data-model.md) — the Celery
  beat-schedule infrastructure the retention sweep reuses.
- [ADR-022](ADR-022-rbac-org-sso-tenant-secrets.md) — the `editor`/`viewer`
  role model this ADR reuses for gating, and the still-open "no admin role"
  limitation this ADR deliberately does not resolve.
- `docs/compliance/lgpd-gdpr-data-processing.md` — updated with this
  sprint's export/retention capabilities.
