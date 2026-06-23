# ADR-004 — Use SQLite + JSON files for audit persistence

**Status:** Accepted  
**Date:** 2026-06-22  
**Deciders:** Bruno Ribeiro

---

## Context

Every pipeline run must be auditable: which agents ran, what decisions were made, what data was produced, and what errors occurred. Options:

1. **In-memory only**: no persistence — unacceptable for a research artifact
2. **PostgreSQL**: requires a running DB server for audit, coupling audit to the operational DB
3. **SQLite + JSON files**: zero-dependency, portable, easy to inspect with any SQLite viewer or `jq`
4. **Structured logging to file only (JSONL)**: queryable but requires parsing; no relational queries

## Decision

Dual persistence in `src/ai_etl/audit/db.py`:

1. **JSON file** (`{run_id}.json`): full state snapshot — every field, including audit_log array. Useful for per-run inspection and diffing.
2. **SQLite table** (`runs.db`): lightweight relational index with `run_id`, `spec`, `status`, `error`, `rows_loaded`, `timestamp`. Useful for querying across runs (e.g., success rate, average rows loaded).

`log_action()` in `audit/logger.py` appends entries to `state["audit_log"]` during execution; `save_run()` persists the final state after the graph completes.

## Consequences

- **Positive**: zero infrastructure requirement — works in any environment without a DB server
- **Positive**: JSON snapshots are self-contained and version-control friendly for the case study results
- **Positive**: SQLite enables cross-run queries without a server
- **Negative**: SQLite is not suitable for concurrent writes (multiple pipelines running simultaneously); acceptable for TCC sequential runs
- **Negative**: the `runs.db` schema is minimal — no agent-level timing or per-step metrics; these are in the JSON audit_log

## Related

- [ADR-002](ADR-002-shared-pipelinestate-typeddict.md) — audit reads from PipelineState TypedDict
