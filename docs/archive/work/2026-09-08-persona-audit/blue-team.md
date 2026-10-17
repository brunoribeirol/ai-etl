# Blue Team — Defensive Gaps

**Date:** 2026-09-09. Mode: real reads + a real redaction test; no code modified.

## Findings

### BT-1 — Secret redaction is key-name-only and NOT applied to persisted state (MEDIUM)
`audit/logger.py::_sanitize` redacts by keyword in the field NAME (`key/token/secret/
password/credential/authorization/bearer`) and recurses into nested dicts/lists. Live test:
```
api_key, openai_api_key, password, token, authorization -> ***REDACTED***
nested my_secret, list access_token                      -> ***REDACTED***   (recursion works)
connection_string / postgres_connection_string / dsn / db_url -> LEAKED (value passes through)
raw "sk-proj-..." in a benignly-named field              -> LEAKED
```
Two real gaps:
1. **Value-based secrets escape.** A DSN with an embedded password in a field named
   `connection_string`/`dsn`/`db_url`, or a raw `sk-...` in any non-keyword field, is not
   redacted. Add `connection`, `dsn`, (carefully) `url`/`uri` to the keyword set, or add a
   value-pattern check.
2. **State snapshots are never sanitized.** `audit/db/runs.py::_write_json` does
   `_make_serializable(dict(state))` then `json.dumps` — no `_sanitize`. Redaction only
   ever runs on `audit_log` entries via `log_action`, not on the full state JSON in S3 nor
   the `error` column in Postgres. README/SECURITY's "API keys are never logged — the audit
   logger redacts sensitive fields automatically" is narrower than it reads.

### BT-2 — Tenant-secret leak guard covers only the happy path (MEDIUM)
ADR-044 keeps decrypted tenant DSNs out of `PipelineState` via `core/tenant_context.py`
(ContextVar) — good design. The regression test
`test_run_silver_pipeline_never_persists_tenant_connection_string_in_saved_state` proves it
**only for a successful run** (`_FakeGraph([{"loader":{"status":"completed"}}])`). It does
not exercise the **failure path**: `agents/pipeline/extractor.py:91` wraps a source
exception as `"error": f"Extractor failed on source '{name}': {e}"` and
`{"error": str(e)}` into the audit log; combined with BT-1's unsanitized state persistence,
a DB connection error whose driver message includes connection metadata (host/user/port;
password depending on driver) would be persisted. Add an error-path variant of the
regression test and sanitize `state["error"]` before persisting.

### BT-3 — RBAC boundaries: PASS (strong)
Every mutating route is guarded. Map:
| Route | Guard |
|---|---|
| `POST/PATCH/DELETE /pipelines*`, `PUT .../llm-config`, `.../notification-config` | `require_role("editor")` |
| `POST /runs`, `POST /runs/{id}/approve|reject` | `require_role("editor")` |
| `POST/DELETE /secrets*`, `GET /secrets` | `require_role("editor")` |
| `DELETE /tenant`, `PATCH /tenant/retention|locale` | `require_role("editor")` |
| `PATCH /budget` | `require_role("editor")` |
| all `/admin/*` incl. `POST .../pipelines/{id}/retry` | `require_admin` |
| `POST /runs/excel-sheets` | `get_current_tenant_id` (documented lighter guard — parse only) |
| `POST /runs/estimate` | `get_current_tenant_id` (read-only estimate) |
| `POST /llm/test-connectivity` | `get_current_tenant_id` |
Minor: `/llm/test-connectivity` (any authenticated, incl. `viewer`) triggers a real paid
LLM call — a spend-incurring action available below `editor`. Very low severity.

### BT-4 — Tenant isolation (ADR-040): real, but see QA — the tests don't run in required CI
`audit/connection.py` binds `app.tenant_id` per-transaction via `set_config(..., true)` and
`get_tenant_engine()` fails closed (`EnvironmentError`) rather than falling back to the
bypass role. `test_tenant_isolation_rls.py` passes locally. But those tests self-skip in the
required `check` CI job (no Postgres:5435 there) — see QA/CTO findings. Control is real;
its regression guard is not enforced in CI.

## Verdict
RBAC is genuinely strong. The defensive gaps are in **data-at-rest redaction** (key-name
only, state never sanitized) and the **error-path** of the tenant-secret guard — both
defense-in-depth, both currently unproven for the failure case.
