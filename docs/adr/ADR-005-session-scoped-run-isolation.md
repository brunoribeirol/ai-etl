# ADR-005 — Session-scoped run isolation to stop cross-session history leak

**Status:** Accepted  
**Date:** 2026-08-11  
**Deciders:** Bruno Ribeiro

---

## Context

`load_history()` in `audit/db.py` queries the shared `runs` table with no filter of any kind. `_tab_historico` in `app.py` renders whatever `load_history()` returns, and lets the current Streamlit session browse and download the generated code and results of *any* run in the table — including runs produced by other sessions/users. There is no `tenant_id` or `user_id` column anywhere in `audit/models.py` today; `runs` and `analysis_runs` are keyed only by `run_id`. This is an active, exploitable data leak in the Histórico tab, not a hypothetical one.

Options considered:

1. **Do nothing until real auth ships (Sprint 1)** — rejected. Leaves the leak open for an unacceptable window, especially since Sprint 1 is expected to include a real public deploy — shipping a public deploy on top of a known cross-user data leak is not acceptable sequencing.
2. **Full real authentication now (Clerk + tenant accounts), skipping the stopgap** — rejected. Real auth is a bigger, slower piece of work with its own set of decisions (already earmarked for a future ADR — see Related). Blocking the leak fix on that larger effort is the wrong sequencing: it leaves the leak open exactly as long as option 1 while adding scope.
3. **Session-scoped pseudo-tenancy** (chosen) — generate a random UUID per Streamlit `st.session_state`, store it alongside each run, and filter `load_history()` and the Histórico tab's visible/downloadable runs by it. Cheap, fast, no new external dependency, closes the actual leak surface today without pretending to be real authentication.

## Decision

Add session-scoped pseudo-tenancy as a stopgap:

- Add a nullable `tenant_id` String column to `runs` (and, where applicable, `analysis_runs`) via an additive Alembic migration. (Exact migration filename/revision not fixed by this ADR — it is being authored concurrently; see the migration actually present under `alembic/versions/` for the final name.)
- Populate `tenant_id` from a random UUID generated once per Streamlit `st.session_state` and passed through to `save_run()`/`log_action()` at write time.
- Filter `load_history()` and the Histórico tab's browse/download paths by the current session's `tenant_id`, so a session can only see and download its own runs.

This is explicitly a partial step toward closing two rows of the SaaS Roadmap in `.claude/specs/sr-standard.md` §8 — "Isolamento de dados" (Nenhuma → Tenant ID em todo audit log e storage) and "Multi-tenancy" (POSTGRES_URL único → connection pool por tenant; row-level security). It does **not** close either row: there is no connection pooling per tenant, no row-level security, and no real account behind `tenant_id` — only a per-browser-session UUID. Real auth/accounts is deferred to a separate future ADR ("the Sprint 1 ADR"; number not yet assigned).

## Consequences

- **Positive**: closes the active cross-session data leak in the Histórico tab today, without waiting on the larger auth effort.
- **Positive**: the migration is additive and non-breaking — `tenant_id` is nullable, no backfill is required for existing rows.
- **Positive**: the column is designed to be reused, not thrown away — the future Sprint 1 auth ADR's migration only needs to switch the source of `tenant_id` from a session UUID to a real account ID and enforce `NOT NULL`, rather than introducing a new column.
- **Negative**: this is explicitly not real authentication or persistent identity. A session UUID lives only in that browser's `st.session_state` — a user loses access to their own history if they clear cookies, switch browsers, or switch devices.
- **Negative**: it does not prevent someone with unusual access from tampering with `st.session_state` directly to assume another session's `tenant_id`.
- **Negative**: it is a stopgap that must be superseded, not a permanent design — it does not implement connection pooling per tenant or row-level security, both still open items on the SaaS Roadmap.

## Related

- [ADR-002](ADR-002-shared-pipelinestate-typeddict.md) — `PipelineState` contract; `tenant_id` will eventually need to flow through it once runs are attributed to real accounts.
- [ADR-004](ADR-004-sqlite-audit.md) — audit persistence layer; this ADR's migration touches the same `runs`/`analysis_runs` tables.
- The future Sprint 1 auth/tenancy ADR (number not yet assigned) — will define real account-based tenancy, superseding the session-UUID stopgap described here.
