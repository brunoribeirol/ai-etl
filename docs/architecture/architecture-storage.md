# Storage architecture — why S3 and Supabase/Postgres coexist

**Sprint 33 finding:** the auditoria that opened this sprint flagged this as a real
gap — not a technical ambiguity, but the absence of a page saying *why* two storage
systems exist and which one owns what. This page fills that gap.

## The two systems

| | S3 (`audit/storage.py`) | Supabase/Postgres (`audit/db/`) |
|---|---|---|
| **What it holds** | Execution artifacts — run JSON, Silver CSVs, Gold/Science CSVs and Plotly figures | Relational, queryable data — `users`, `runs`, `analysis_runs`, `saved_pipelines`, `tenant_secrets`, `admin_action_log`, etc. |
| **Access pattern** | Write-once, read-back-whole-blob by key | Filtered, joined, aggregated (`WHERE tenant_id = ...`, `ORDER BY`, `JOIN`, `COUNT`) |
| **Selected via** | `StorageBackend` (`STORAGE_BACKEND=local\|s3`, ADR-009) | `APP_DATABASE_URL` (always Postgres — Supabase-hosted in production, ADR-006) |
| **Key shape** | `{AI_ETL_ENV}/{tenant_id}/{run_id}...` (S3) or `{log_dir}/{run_id}...` (local) | Rows scoped by a `tenant_id` foreign key to `users.id` |

## Why not just one

**S3 artifacts are blobs, not queryable rows.** A run's full state snapshot
(`{run_id}.json`), the Silver DataFrame (`{run_id}_silver.csv`), and every
Gold/Science sub-task's data/figure (`{run_id}_gold_{i}.csv`,
`{run_id}_gold_{i}_fig.json`, `{run_id}_analysis.json`) are read back whole, by
key, exactly once per view (the History tab, a completed-run poll). Nothing in
the product ever needs to `SELECT` inside a Gold DataFrame or filter across many
runs' artifact contents at once — so there's no reason to pay Postgres's
per-row overhead for CSV/JSON blobs that can be tens of megabytes, and no
reason to duplicate pandas/plotly's native serialization (`to_csv`,
`fig.to_json()`) into a relational schema that would have to be kept in sync
with every field either library adds.

**Postgres rows are exactly what the product's own UI needs to filter, join,
and aggregate.** The History tab needs "the last 20 runs for *this* tenant,
newest first, joined against `analysis_runs` for cost." The budget cap
(ADR-019) needs "sum of `cost_usd` this calendar month for *this* tenant." The
scheduler (ADR-016) needs "every saved pipeline whose `next_run_at` has
passed." None of this is expressible as "fetch a blob by key" — it's
relational access by construction, and SQLAlchemy Core's `select()`/`where()`
already gets it for free, unit-tested against a real (if in-memory SQLite)
engine (see `tests/unit/test_audit_db.py`).

**They're independently scaled and independently backed up on purpose.**
Postgres (Supabase-managed) is the system of record for anything a foreign
key, an index, or `ON CONFLICT DO UPDATE` needs — it's small (rows, not
blobs) and needs point-in-time recovery. S3 (or local disk in dev,
`STORAGE_BACKEND=local`) is where the potentially-large per-run artifacts
live — cheap, durable, and disposable independently of the relational schema
(Sprint 36's retention policy can expire `{run_id}_gold_*.csv` objects after N
days without touching a single Postgres row, and does not need to).

## What lives where — concretely

**S3 (or local `./runs/`, `STORAGE_BACKEND=local`) via `audit/storage.py`:**
- `{run_id}.json` — full pipeline state snapshot
- `{run_id}_transform.py` — generated transformation code (if any)
- `{run_id}_silver.csv` — the Silver DataFrame
- `{run_id}_gold_{i}.csv` / `{run_id}_gold_{i}_fig.json` — each Gold sub-task's data/chart
- `{run_id}_science_{i}.csv` / `{run_id}_science_{i}_fig.json` — each Science sub-task's data/chart
- `{run_id}_analysis.json` — narratives, model_info, recommendations manifest

**Supabase/Postgres via `audit/db/` (Sprint 33 split — see that package's
`__init__.py` for the full module breakdown):**
- `users` — one row per Clerk account; `monthly_budget_usd` (ADR-019)
- `runs` / `analysis_runs` — per-run metadata, status, cost, token counts (never the DataFrames/figures themselves — those are S3 keys referenced by `run_id`)
- `saved_pipelines` — scheduled pipeline configuration, health-snapshot cache, LLM provider/model override
- `stage_latencies` — per-stage timing (ADR-007)
- `tenant_secrets` — encrypted per-tenant source credentials (Sprint 19)
- `admin_action_log` — auditable cross-tenant admin access (Sprint 31)

## The rule of thumb for future additions

If it's read back whole by a single key and never filtered/joined/aggregated
across rows — it's an S3 artifact. If a query needs a `WHERE`, `JOIN`,
`ORDER BY`, or `COUNT` — it's a Postgres row. A row can (and often does) point
at an S3 key (`analysis_runs.run_id` -> `{run_id}_analysis.json`); an S3
artifact never needs to know about a Postgres row.
