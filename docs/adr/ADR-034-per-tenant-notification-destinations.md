# ADR-034: Per-tenant notification destinations

**Status:** Accepted
**Date:** 2026-08-22
**Sprint:** 37 (Fase 2 product roadmap, "Nível Big Tech")

## Context

Sprint 14 (ADR-018) and Sprint 15 (ADR-020) built the two delivery paths that exist today —
KPI-drift digests (`services/alerting.py`) and pipeline-health alerts
(`services/health_alerts.py`) — both routed through `services/notifications.py`'s four
`send_*_digest` functions. Every one of those functions reads its destination straight from a
deployment-global env var: `RESEND_API_KEY`/`AI_ETL_ALERT_EMAIL_TO`, `SLACK_WEBHOOK_URL`,
`TEAMS_WEBHOOK_URL`, `GOOGLE_CHAT_WEBHOOK_URL`. That was an accepted, explicitly documented
gap while Bruno was the only real tenant validating the mechanism (`docs/CURRENT_STATE.md`,
"Known risks", since Sprint 14/15) — every alert for every tenant's pipeline lands in the same
inbox/channel. It does not scale to a second real tenant: tenant B's drift digest has nowhere
to go but tenant A's Slack channel.

**Definition of done (roadmap):** two pipelines belonging to different tenants send an
alert/digest to different, independently configured channels.

## Decision

### 1. Storage: two new nullable columns on `saved_pipelines`, not a new table

`notification_channel` (`String(20)`), `notification_target_ciphertext` (`Text`), and
`notification_active` (`Boolean`, default `true`) are added to `saved_pipelines` (migration
0019) rather than a new `pipeline_notifications` table. The scope is a destination *per saved
pipeline*, not per tenant — a tenant with three pipelines may reasonably want three different
channels (e.g. a nightly digest to email, a high-value pipeline's health alerts to Slack). A
new table would need the same `(pipeline_id)` uniqueness `saved_pipelines` already enforces via
its own primary key, for no isolation benefit — same reasoning ADR-031 used to add
`llm_provider`/`llm_model` directly to the row instead of a side table.

`notification_channel`/`notification_target_ciphertext` are always `NULL`/`NULL` together
("no override, use this deployment's global channel(s)") or both set — the same "set or cleared
together" convention ADR-031's `llm_provider`/`llm_model` pair already established.
`notification_active` lets an operator temporarily silence a configured destination (e.g.
during a webhook rotation) without losing the stored value.

### 2. Encryption: reuse `services/secrets_service.py`'s Fernet mechanism, not `tenant_secrets`

`services/secrets_service.py` (Sprint 19, ADR-022) already solves "encrypt one string at rest,
decrypt it for one authorized read" for `tenant_secrets`. Two options were on the table for the
notification target:

- **Store it in `tenant_secrets`, referenced by name.** Rejected: `tenant_secrets` is keyed
  `(tenant_id, name)` for tenant-wide, listable, named credentials an operator manages directly
  (`GET/POST/DELETE /secrets`). A notification target is scoped to one `saved_pipelines` row,
  not tenant-wide, and was never meant to be listed or named by the operator — folding it in
  would need a synthetic name per pipeline and would make `list_secret_names` return
  notification plumbing alongside real credentials.
- **Add two new functions, `encrypt_value`/`decrypt_value`, to `secrets_service.py`, and store
  the ciphertext directly on the `saved_pipelines` row.** Accepted: reuses the exact same
  Fernet key (`AI_ETL_SECRETS_ENCRYPTION_KEY`) and "fails closed if the key is missing" posture
  `set_secret`/`get_secret` already have, with no new key material to provision or rotate
  separately, while keeping the value's lifecycle tied to the row that owns it (deleting a
  saved pipeline deletes its notification target for free, no orphaned `tenant_secrets` row to
  separately clean up).

### 3. Read/write split: `audit.db.pipelines` never lets a target value leave undecrypted-only

Mirroring ADR-031's `get_saved_pipeline_llm_config`/`set_saved_pipeline_llm_config` split, this
sprint adds three functions to `audit/db/pipelines.py`:

- `get_saved_pipeline_notification_config` — `{"notification_channel", "notification_configured"
  (bool), "notification_active"}`. Never the target value. This is the shape any API endpoint
  may return.
- `get_saved_pipeline_notification_target` — additionally returns the raw, still-encrypted
  `notification_target_ciphertext`. Internal-only: consumed exclusively by
  `services/notifications.py`'s delivery path, never imported by a router.
- `set_saved_pipeline_notification_config` — accepts a plaintext target, encrypts it via
  `encrypt_value` before it reaches a SQL statement, and returns only the config-shaped dict
  (never the value back to the caller).

The destination value is therefore never serialized into an HTTP response, at any point,
encrypted or not — a stricter posture than `llm_provider`/`llm_model` (not secret, freely
returned) needs, because a webhook URL or an internal email address is.

### 4. Delivery: an optional per-call override, global env stays the fallback

Each of `services/notifications.py`'s four `send_*_digest` functions gains one optional
keyword-only parameter (`override_recipients` for email, `override_webhook_url` for the other
three) instead of reading `os.getenv()` unconditionally. A new
`resolve_pipeline_notification_override(pipeline_id, tenant_id)` decrypts the pipeline's
configured target (best-effort — an inactive override, a missing config, or a decrypt failure
from a rotated/missing encryption key all resolve to `(None, None)`, never raise) and
`services/alerting.py`/`services/health_alerts.py` pass it through only to the matching
channel. Email's sending infrastructure (`RESEND_API_KEY`, `AI_ETL_ALERT_EMAIL_FROM`) stays
deployment-global — only the destination is per-tenant, matching the storage decision's scope
(the ciphertext holds a recipient list or a webhook URL, never provider credentials).

A pipeline with no configured override behaves exactly as before this sprint — zero behavior
change, matching the "NULL = no override" convention `llm_provider`/`llm_model` already set.

### 5. No UI

Per the roadmap's scope for this sprint, only the schema and the
`GET`/`PUT /pipelines/{id}/notification-config` endpoints (plus a
`GET /pipelines/notifications/allowed-channels` allowlist endpoint, mirroring
`GET /pipelines/llm/allowed-models`) ship here. A configuration UI is deferred to Sprint 38
("Polimento de front-end e UX não-técnico").

## Consequences

- Two tenants' pipelines can now each alert to their own channel — the roadmap's definition of
  done — with no change to a pipeline that never configures an override.
- The notification target is encrypted with the same key as `tenant_secrets`; rotating
  `AI_ETL_SECRETS_ENCRYPTION_KEY` invalidates both stores' ciphertexts identically (an existing,
  documented operational concern from ADR-022, not a new one).
- Only one channel can be overridden per pipeline (the column is a single
  `notification_channel`, not one target per channel). A pipeline that wants, say, both Slack
  *and* email overridden independently is out of scope for this sprint — accepted for now, same
  incremental-scope posture ADR-031 took for LLM config (one provider/model pair per pipeline,
  not per LLM call site).
- No email-format or webhook-URL-shape validation beyond "non-empty string" at the API layer;
  a malformed target simply fails at delivery time the same way a malformed
  `SLACK_WEBHOOK_URL` env var already does today (a caught `httpx.HTTPError`, logged as a
  failed send, never raised into the pipeline run).
