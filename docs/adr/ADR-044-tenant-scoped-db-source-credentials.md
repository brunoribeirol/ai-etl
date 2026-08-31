# ADR-044: Tenant-Scoped Database Source/Destination Credentials

**Status:** Accepted
**Date:** 2026-08-31
**Sprint:** post-TCC hardening (owner-requested during a live operational session)

## Context

ADR-022 (Sprint 19) built tenant-scoped secret storage (`services/
secrets_service.py`, `tenant_secrets` table, Fernet encryption at rest) but
explicitly deferred wiring any stored secret into pipeline execution —
Postgres/MySQL/MongoDB source connectors were, and until this ADR remained,
on a single process-wide `POSTGRES_URL`/`MYSQL_URL`/`MONGODB_URI` env var
shared by every tenant on the deployment. A tenant could save a credential
through the UI (`/secrets`) but no code path ever read it back for
extraction — a real, previously-flagged gap between what the product
appeared to offer and what it did (2026-08-31 session notes,
`docs/CURRENT_STATE.md`).

ADR-022 Decision 4 named the reason this was hard, not just undone:
`tenant_id` is not available inside `extractor_node`/`loader_node` (both
pure functions of `PipelineState`, per the LangGraph node contract this
project treats as non-negotiable), and the two obvious ways to get it there
are both worse than the problem:

- Add a parameter to the node function — breaks the
  `(state: PipelineState) -> PipelineState` contract every node in
  `core/graph.py` relies on.
- Add `tenant_id` (or the resolved secret) to `PipelineState` — ADR-002/
  ADR-006 already keep `tenant_id` out of `PipelineState` on purpose, and
  investigating this ADR surfaced the sharper reason why a *secret*
  specifically can never go there: `audit/db/runs.py::save_run` ->
  `_write_json` calls `_make_serializable(dict(state))` on the **entire**
  state and writes it to `{run_id}.json` (local disk or S3), with no
  per-key redaction — unlike `audit/logger.py::log_action`, which sanitizes
  its `details` dict via `_sanitize`'s `_SENSITIVE_KEYWORDS` matcher.
  Putting a decrypted connection string anywhere in `PipelineState` means
  writing a tenant's real DB password to disk/S3 in plaintext on every run.

## Decision

A new module, `core/tenant_context.py`, holds resolved tenant connection
strings in a `contextvars.ContextVar` — never part of `PipelineState`, never
touched by `save_run`/`log_action`, and requiring no change to any node's
signature.

**Resolution point:** `services/pipeline_service.py` — the same place
`llm_provider_override`/`locale`/`approval_policy` are already resolved
from the DB before being carried into the graph, and the one place in the
call chain that reliably has a real `tenant_id`. Two call sites:

1. `run_silver_pipeline`: `resolve_tenant_overrides(tenant_id)` is called
   once, and the entire `graph.stream(state)` loop (Orchestrator ->
   Extractor -> Transformer -> Quality -> Loader) runs inside a
   `with tenant_connections(overrides):` block.
2. `resume_pending_load` (ADR-028's deferred write after an operator
   approves a gated run): the direct `loader_node(granted_state)` call —
   the *other* place a connector opens a real connection — gets the same
   wrap.

**Secret naming convention:** three fixed secret names, resolved via the
existing `secrets_service.get_secret(tenant_id, name)` — no new API
endpoint:

| Source type | Secret name                  |
|-------------|-------------------------------|
| postgres    | `postgres_connection_string` |
| mysql       | `mysql_connection_string`    |
| mongodb     | `mongodb_connection_string`  |

A tenant stores a full connection string (e.g.
`postgresql://user:pass@host:5432/db`) under that name via the existing
`POST /secrets` endpoint. This is deliberately one arbitrary string per
name, not separate host/user/password fields — `tenant_secrets` already
stores exactly that shape, so this is the smallest schema-compatible slice,
not a new secrets model. A tenant who configures nothing keeps today's
exact behavior: `resolve_tenant_overrides` returns `{}`, every connector
falls back to the shared env var.

**Connector changes:** `sources/postgres_source.py`, `sources/
mysql_source.py`, `sources/mongodb_source.py`, and `destinations/
postgres_dest.py` (`save_postgres`/`preview_postgres`) each try
`get_connection_override(source_type)` first, falling back to
`os.getenv(...)` — a one-line change per connector, in the same place the
env var was already being read.

## Alternatives considered

- **Resolve inside the node, given `tenant_id` as an extra `initial_state`
  field carried in `PipelineState`.** Rejected for the reason above: the
  full state gets serialized to the run's JSON snapshot. This would require
  either redacting `_write_json`'s output (a much bigger, riskier change
  touching every existing run's audit trail) or accepting a real credential
  leak to storage.
- **A module-level "current tenant" global set/cleared around each call,
  instead of `contextvars`.** Rejected: not safe under concurrent
  requests/tasks in the same process (the Celery worker runs
  `--pool=threads`, `concurrency=2` — two tasks' tenants could interleave).
  `ContextVar` is the standard-library-correct tool for exactly this:
  per-logical-task state that must not leak across concurrent execution
  contexts in the same process.
- **Wire `rest_source.py`'s already-designed `secret_ref` extension at the
  same time.** Out of scope for this ADR — this session's request was
  specifically "credencial de banco por tenant" (DB connectors). REST's
  `_build_auth` env-var-name indirection is untouched; it can reuse this
  same `tenant_context` module later as its own, separate follow-up.

## Consequences

- **Positive**: the Secrets feature (`/secrets`, ADR-022) is no longer
  purely decorative for DB sources/destinations — a tenant can point a
  pipeline at their own Postgres/MySQL/MongoDB, isolated from every other
  tenant's credential and from the deployment's own shared env var.
- **Positive**: zero behavior change for every tenant who hasn't configured
  one of the three secret names — falls back to the exact pre-ADR-044 path.
- **Positive**: no `PipelineState` field added, no node signature changed —
  every non-negotiable contract in `CLAUDE.md` stays intact.
- **Positive**: a decrypted connection string is provably never persisted —
  covered by an explicit regression test
  (`tests/unit/test_pipeline_service.py::
  test_run_silver_pipeline_never_persists_tenant_connection_string_in_saved_state`)
  asserting the literal value never appears in what gets passed to
  `save_run`.
- **Negative — accepted**: a tenant must know to name their secret exactly
  `postgres_connection_string`/`mysql_connection_string`/
  `mongodb_connection_string` — there is no per-source-instance credential
  (a tenant with two different Postgres sources in one pipeline spec cannot
  give them different credentials this way). Revisit if/when a tenant
  actually needs more than one DB source of the same type in one pipeline.
- **Negative — accepted**: `rest_source.py` remains on its original
  env-var-name indirection; REST secret wiring is a separate follow-up, not
  bundled here.
- **Negative — accepted**: `Sandbox.create()`-style destination-side
  connectors other than Postgres (there is no `mysql_dest.py`/
  `mongodb_dest.py` — see `destinations/` folder structure in `CLAUDE.md`)
  have nothing to wire; if a MySQL/MongoDB destination is ever added, it
  should follow the same `get_connection_override` pattern from day one.

## Related

- [ADR-022](ADR-022-rbac-org-sso-tenant-secrets.md) — tenant-scoped secret
  storage this ADR wires into execution; Decision 4's "deliberately out of
  scope" section is the direct predecessor of this ADR.
- [ADR-002](ADR-002-shared-pipelinestate-typeddict.md) — why `tenant_id`
  stays out of `PipelineState`, the constraint this ADR's `ContextVar`
  design satisfies without exception.
- [ADR-028](ADR-028-human-approval-dry-run.md) — `resume_pending_load`, the
  second call site this ADR wraps.
