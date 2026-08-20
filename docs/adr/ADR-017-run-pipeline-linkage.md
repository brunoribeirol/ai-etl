# ADR-017: `saved_pipeline_id` linkage on `runs`/`analysis_runs`

**Status:** Proposed — schema-changing PR open, not merged (checkpoint required, same rigor as
migration `0006`/ADR-016).
**Date:** 2026-08-20
**Sprint:** 17 — Histórico comparável entre execuções

## Context

Sprint 13 (ADR-016) introduced `saved_pipelines`: a persisted spec + cron schedule + tenant,
fired unattended by Celery beat. Every fire still produces a normal execution — a `runs` row
(and, if a `business_question` was set, an `analysis_runs` row) — through the same
`execution_queue.enqueue_analysis()` an avulso (one-off) `POST /runs` call uses.

Sprint 17's job is to make that run history *comparable*: a time-series view of a saved
pipeline's KPIs across its executions, and a diff between two runs the user picks. That requires
grouping `runs`/`analysis_runs` rows by the saved pipeline that produced them — and today,
nothing does that.

**Investigated before writing any code** (per this project's own standard — don't assume, check):
`saved_pipelines.last_task_id`/`last_run_at` (ADR-016) only remember the single *most recent*
fire — enough for the manager UI's "última execução" line, not for a history of N executions.
`services/scheduler.py::check_scheduled_pipelines_task` calls `enqueue_analysis(pipeline["spec"],
pipeline["business_question"], run_dir=..., tenant_id=pipeline["tenant_id"])` — no pipeline
identity crosses into the execution at all. Following the call chain
(`enqueue_analysis` → `run_full_analysis_task` → `run_full_analysis`/`run_silver_pipeline` →
`save_run`/`save_analysis` → `_write_run_row`/`_write_analysis_row`) confirms `runs`/
`analysis_runs` (`audit/models.py`) have no column that could carry it, and no caller anywhere
threads one through. **Confirmed gap, not an oversight to route around** — a real schema decision.

## Decision

Add a nullable `saved_pipeline_id` column to both `runs` and `analysis_runs`
(migration `0007`), `FOREIGN KEY ... REFERENCES saved_pipelines(id) ON DELETE SET NULL`, each
with its own index (`ix_runs_saved_pipeline_id`, `ix_analysis_runs_saved_pipeline_id`) — the
column `audit/db.py::list_pipeline_run_history` filters and orders on. Thread it as a new
optional keyword argument (default `None`, so every existing call site is unaffected) through
the whole call chain: `enqueue_analysis` → `run_full_analysis_task` (Celery task kwarg,
JSON-serializable, same as every other task argument) → `run_full_analysis`/
`run_silver_pipeline` → `save_run`/`save_analysis` → `_write_run_row`/`_write_analysis_row`.
`services/scheduler.py` passes its own `pipeline_id` at the one real call site
(`enqueue_analysis(..., saved_pipeline_id=pipeline_id)`); `POST /runs` (avulso) passes nothing,
so its rows read back `NULL` — exactly like every run created before this migration.

### Why a column, not a derived/joined answer

- **`saved_pipelines.last_task_id` cannot be extended into a history** — it is a single mutable
  field (Sprint 13 deliberately kept it that way: "the eventual real run_id is only known once
  `run_full_analysis_task` starts... a future tick's read (or the run's own Histórico entry) is
  the source of truth"). Turning it into a list would mean a second table anyway — at which point
  it is simpler and more queryable to put the reference on the row it actually describes.
- **No new join/aggregation table** (e.g. a `pipeline_run_links` bridge table) — `runs`/
  `analysis_runs` already are one-row-per-execution; a loosely-coupled link on the row itself
  matches ADR-004's original design intent (audit rows describe one execution each) without
  adding a third table and a second write per execution.
- **`ON DELETE SET NULL`, not `CASCADE` or `RESTRICT`**: run history is audit data — ADR-004's
  entire premise is durable execution audit trail. Deleting a saved pipeline (the recurring
  schedule/config) must never delete the runs it already produced; `SET NULL` demotes those rows
  to "no longer linked to a live pipeline" (same as a Sprint-13-vintage run) rather than losing
  them or blocking the delete.

### Why nullable, no backfill

Every row written before migration `0007` — every avulso run ever, and every Sprint 13 scheduled
fire before this sprint shipped — has no way to recover which saved pipeline (if any) produced
it; that information was never captured. `saved_pipeline_id IS NULL` therefore means "avulso, or
scheduled before Sprint 17" — the two are indistinguishable, and that is an accepted, explicitly
documented limitation of this migration, not a bug to chase. A backfill is not possible without
inventing data.

### Coordination with Sprint 14 (parallel session, drift detection)

Sprint 14 compares only the most recent run against the one before it (a narrower, standalone
feature). This ADR does not change `runs`/`analysis_runs`' *existing* columns, `load_history`'s
existing signature/output shape, or `load_full_result` — only adds one new nullable column each
table and one new read-only query function (`list_pipeline_run_history`) and one new endpoint
(`GET /pipelines/{id}/history`). Sprint 14's access pattern (reading the two most recent rows for
a pipeline/tenant via whatever it queries) is unaffected either way; if it later wants the same
linkage, this column is already there for it to read, with no coordination needed beyond "don't
also add a same-named column."

## Consequences

- `runs`/`analysis_runs` each grow one nullable `String` FK column + one index — small, additive,
  backward-compatible. No existing query (`load_history`, `load_full_result`,
  `_run_belongs_to_tenant`) changes shape.
- `enqueue_analysis`/`run_full_analysis_task`/`run_full_analysis`/`run_silver_pipeline`/
  `save_run`/`save_analysis`/`_write_run_row`/`_write_analysis_row` all gain one new optional
  keyword argument, default `None`. Every existing caller (including `POST /runs`, every test)
  is unaffected — verified by running the full existing unit suite after the change, not assumed.
- New: `audit/db.py::list_pipeline_run_history(pipeline_id, tenant_id)` — tenant-scoped time
  series (oldest first), `LEFT OUTER JOIN` onto `analysis_runs` (same pattern as `load_history`,
  so a Silver-only fire reads back `None` KPIs, not zero). New: `GET /pipelines/{id}/history`
  (404s if the pipeline is unknown/not owned, same as `GET /pipelines/{id}`).
- **Not addressed by this ADR**: retroactively linking pre-Sprint-17 scheduled runs (impossible,
  see above); a dedicated diff endpoint (the frontend fetches two full runs via the existing
  `GET /runs/{run_id}` and diffs client-side — no new backend surface needed for that half of the
  sprint); deleting a saved pipeline's own row still requires no special handling for its linked
  runs beyond the FK's own `ON DELETE SET NULL` (already exercised by the Postgres FK, not
  application code).

## Verification

- Migration `0007` applied/reverted against a real local Postgres (not Docker — see ADR-016's
  own note on why; same environment, same pattern), `alembic upgrade head` (0001→0007) clean,
  `\d runs` / `\d analysis_runs` show the new column/index/FK exactly as declared,
  `alembic downgrade -1` drops both columns/indexes/constraints cleanly, re-`upgrade head`
  reapplies cleanly. See `docs/CURRENT_STATE.md`'s Sprint 17 section for the exact commands/output
  captured this session.
- `tests/unit/test_saved_pipelines_db.py` — new `list_pipeline_run_history` tests against a real
  in-memory SQLite engine (rows inserted directly via dialect-agnostic `sqlalchemy.insert`, since
  `save_run`/`save_analysis` use `postgresql.insert` `ON CONFLICT`, which doesn't translate to
  SQLite — same constraint the existing saved-pipeline CRUD tests already work around).
- `tests/unit/test_api_pipelines.py` — new `GET /pipelines/{id}/history` tests (404 on unknown
  pipeline, scoped time-series response on success).
- `tests/unit/test_pipeline_service.py`, `test_execution_queue.py`, `test_audit_db.py` — existing
  fakes/monkeypatches widened to accept the new optional kwarg; new tests confirm
  `saved_pipeline_id` actually threads end-to-end from `enqueue_analysis` through to the Celery
  task payload and into `run_full_analysis`.
