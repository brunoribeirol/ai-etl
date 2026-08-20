"""SQLAlchemy Core table definitions for the application database.

Mirrors the `runs`/`analysis_runs` tables Phase 1 kept in SQLite
(`{log_dir}/runs.db`), now backed by the application Postgres so run
history survives across processes and Streamlit redeploys instead of
living in one file per deployment. `tenant_id` maps to a real Clerk user
account (see `users` below and ADR-006) — it is a required foreign key,
not the per-browser-session UUID stopgap ADR-005 originally introduced.

Core (not the ORM) matches the style already used in
`sources/postgres_source.py` and `destinations/postgres_dest.py`.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),  # Clerk user_id — not a UUID
    Column("created_at", DateTime(timezone=True), nullable=False),
    # Sprint 29 (ADR-019) — optional per-tenant spend cap, USD, calendar-month
    # window. NULL (the default for every existing and new tenant) means "no
    # cap configured" — opt-in, zero behavior change for a tenant that never
    # sets one. See services/execution_queue.py::check_budget_cap for
    # enforcement and ADR-019 for why this is a single nullable column rather
    # than a new table (no history/versioning of the cap value is needed —
    # actual spend history already lives in analysis_runs.cost_usd).
    Column("monthly_budget_usd", Float, nullable=True),
)

runs = Table(
    "runs",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("spec", Text, nullable=False),
    Column("status", String(20), nullable=False),
    Column("error", Text, nullable=True),
    Column("rows_loaded", Integer, nullable=True),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("tenant_id", String, ForeignKey("users.id"), nullable=False),
    # Sprint 17 (ADR-017, migration 0007) — which saved pipeline (if any)
    # produced this execution. Nullable: every avulso (one-off) run, and
    # every run that predates this column, has none. No FK
    # ON DELETE CASCADE — deleting a saved pipeline must not delete its run
    # history (ADR-017); the column reads back NULL after such a delete
    # instead (ON DELETE SET NULL, set explicitly in the migration).
    Column(
        "saved_pipeline_id",
        String,
        ForeignKey("saved_pipelines.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Index("ix_runs_saved_pipeline_id", "saved_pipeline_id"),
    # Sprint 14 (ADR-018, migration 0008) — additional composite index on top
    # of Sprint 17's single-column one above:
    # `get_previous_completed_run`'s `WHERE saved_pipeline_id = :id ...
    # ORDER BY timestamp DESC LIMIT 1` query (drift detection's "most recent
    # fire of this pipeline" lookup) is satisfied by one index scan with this
    # composite form instead of a single-column filter followed by a
    # separate sort.
    Index("ix_runs_saved_pipeline_timestamp", "saved_pipeline_id", "timestamp"),
)

stage_latencies = Table(
    "stage_latencies",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # runs.run_id or analysis_runs.run_id, disambiguated by run_type — see ADR-007
    # for why this is one narrow long-format table instead of two nullable FKs.
    Column("run_id", String, nullable=False),
    Column("run_type", String(10), nullable=False),  # "silver" | "analysis"
    Column("tenant_id", String, ForeignKey("users.id"), nullable=False),
    Column("stage", String(30), nullable=False),
    # 2nd+ Analyst/Science call for the same run (repair/multi-subtask); always
    # 1 for Silver's LangGraph nodes, which run exactly once each.
    Column("seq", Integer, nullable=False, server_default="1"),
    Column("duration_seconds", Float, nullable=False),
    Column("timed_out", Boolean, nullable=False, server_default="false"),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Index("ix_stage_latencies_tenant_stage", "tenant_id", "stage"),
)

analysis_runs = Table(
    "analysis_runs",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("gold_subtasks", Integer, nullable=False),
    Column("science_subtasks", Integer, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("total_tokens", Integer, nullable=False),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("tenant_id", String, ForeignKey("users.id"), nullable=False),
    # Sprint 3 (ADR-008, migration 0005) — evaluation metric 3, cost per
    # execution. model_name is persisted per-run (not just read live from
    # AI_ETL_LLM_MODEL) so a later change to the env var doesn't silently
    # reprice historical runs. cost_usd is nullable: core/pricing.py returns
    # None for an unpriced model, which must round-trip as NULL, not 0.0 —
    # see compute_cost_usd's docstring.
    Column("model_name", String(50), nullable=True),
    Column("cost_usd", Float, nullable=True),
    # Sprint 17 (ADR-017, migration 0007) — same linkage as runs.saved_pipeline_id
    # above, kept on this table too since a saved pipeline's analysis-side KPIs
    # (cost, tokens, gold/science subtask counts) are what Sprint 17's time-series
    # view actually charts; joining back to `runs` for every query would work but
    # duplicating the column here keeps `list_pipeline_run_history`'s query a
    # single indexed lookup on this table instead of a join-then-filter.
    Column(
        "saved_pipeline_id",
        String,
        ForeignKey("saved_pipelines.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Index("ix_analysis_runs_saved_pipeline_id", "saved_pipeline_id"),
)

# Sprint 13 (ADR-016, migration 0006) — a "saved pipeline" is a persisted
# spec + cron schedule + tenant, reexecuted unattended by Celery beat
# (services/scheduler.py). Distinct from `runs`/`analysis_runs`, which stay
# one row per *execution* (avulso or scheduled alike) — a saved pipeline
# produces many `runs` rows over time, linked loosely via `last_task_id` only
# (no FK: a saved pipeline can be deleted independently of its run history,
# and `runs.run_id` values already exist before this table does).
saved_pipelines = Table(
    "saved_pipelines",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, ForeignKey("users.id"), nullable=False),
    Column("name", String(200), nullable=False),
    # ADR-016 Decision 3: declared explicitly by the caller (not LLM-inferred
    # from spec) and validated against `core.scheduling.SCHEDULABLE_SOURCE_TYPES`
    # at the API layer before a row is ever written — only "live" sources
    # (postgres/sqlite/mysql/mongodb/rest) may be scheduled, since only those
    # are re-resolvable from connection info alone on every scheduled fire,
    # with no dependency on a browser-uploaded file surviving between runs.
    Column("source_type", String(20), nullable=False),
    # Same free-text shape as runs.spec — the Orchestrator LLM re-derives the
    # actual structured plan from this at execution time, same as an avulso
    # run. `source_type` above is only a save-time validation gate, not
    # threaded any further into execution.
    Column("spec", Text, nullable=False),
    Column("business_question", Text, nullable=False, server_default=""),
    # Standard 5-field cron string (e.g. "0 3 * * *"), validated via
    # croniter at the API layer before being persisted.
    Column("cron_schedule", String(100), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    # Precomputed via croniter so services/scheduler.py's due-pipeline query
    # is an indexed range scan, not a per-row cron evaluation every tick.
    Column("next_run_at", DateTime(timezone=True), nullable=False),
    Column("last_task_id", String, nullable=True),
    Column("last_run_at", DateTime(timezone=True), nullable=True),
    # Sprint 14 (ADR-018, migration 0008) — "% change on a tracked KPI worth
    # alerting on" for this pipeline's own recurring drift check (see
    # core/drift.py). Server-side default backfills every pre-existing row;
    # a customer with a naturally noisier pipeline can loosen it per pipeline
    # without a code change.
    Column("drift_threshold_pct", Float, nullable=False, server_default="20.0"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_saved_pipelines_active_next_run", "is_active", "next_run_at"),
    Index("ix_saved_pipelines_tenant", "tenant_id"),
)
