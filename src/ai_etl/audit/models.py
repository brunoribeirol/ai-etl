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
    JSON,
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
    UniqueConstraint,
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
    # Sprint 15 (ADR-020, migration 0010) — a fast-read health-snapshot cache
    # for this pipeline's most recent fires, not a second source of truth:
    # the real per-fire history is still `runs`/`runs.error`, joined via
    # `saved_pipeline_id` (ADR-017). These three columns exist so a health
    # view (`GET /pipelines`) doesn't need an aggregate query per pipeline
    # just to show "is this one currently broken."
    #
    # `consecutive_failures` increments once per fire whose final attempt
    # (after Level-B retries are exhausted, see execution_queue.py) is not
    # "completed"; resets to 0 on the next fire that completes successfully.
    # `services/health_alerts.py` alerts the operator once this crosses
    # `AI_ETL_HEALTH_ALERT_FAILURE_THRESHOLD` (default 3).
    Column("consecutive_failures", Integer, nullable=False, server_default="0"),
    # "completed" | "failed" — the outcome of the most recent fire's final
    # attempt. `NULL` for a pipeline that has never fired yet.
    Column("last_status", String(20), nullable=True),
    # The most recent failure's error message; cleared (NULL) on success.
    Column("last_error", Text, nullable=True),
    # Sprint 16 (ADR-023) — operator-defined data quality rules for this pipeline,
    # a plain JSON array of rule dicts (`{"column", "operator", "value", "severity",
    # "name"}` — see `agents/pipeline/quality.py::_CUSTOM_RULE_OPERATORS`). Folded into every
    # subsequent scheduled run's `quality_report` alongside the fixed checks; not a
    # derived cache of another table (unlike `consecutive_failures` above) — this is
    # the rule definitions' only source of truth.
    Column("quality_rules", JSON, nullable=False, server_default="[]"),
    # Sprint 27 (ADR-028, migration 0015) — opt-in write-approval gate. `False`
    # (the default) for every existing and new pipeline: zero behavior change
    # unless an operator explicitly turns this on. See
    # `agents/pipeline/loader.py::_is_write_gated` for the full gate decision.
    Column("require_approval", Boolean, nullable=False, server_default="false"),
    # `NULL` = "every write requires approval" (once `require_approval` is on).
    # A set value = "only a write whose row count is at or above this needs
    # approval" *after* the pipeline's first approved write (`last_approved_at`
    # below) — reuses `rows_loaded`'s existing definition (`len(transformed_data)`).
    Column("approval_threshold_rows", Integer, nullable=True),
    # `NULL` = this pipeline has never had an operator-approved write — the
    # roadmap's "before the first real write" clause is unconditional while
    # this is `NULL`, regardless of `approval_threshold_rows`. Set once, on
    # the first successful approval, by
    # `services/pipeline_service.py::resume_pending_load`.
    Column("last_approved_at", DateTime(timezone=True), nullable=True),
    # Sprint 30 (ADR-031, migration 0016) — per-pipeline LLM provider/model
    # override. Both `NULL` (the default for every existing and new pipeline) means
    # "no override, use this deployment's global `AI_ETL_LLM_PROVIDER`/
    # `AI_ETL_LLM_MODEL`" — zero behavior change unless a tenant explicitly sets
    # both via `PUT /pipelines/{id}/llm-config`. Always set or cleared together
    # (never one `NULL` and the other not) — see
    # `audit/db/pipelines.py::set_saved_pipeline_llm_config`. Validated against
    # `core/llm.ALLOWED_MODELS_BY_PROVIDER` before being persisted, never at read
    # time. Actual pipeline execution does not yet read these columns (see
    # ADR-031 §5, "Explicitly out of scope") — this is API-contract/persistence
    # only for this sprint.
    Column("llm_provider", String(20), nullable=True),
    Column("llm_model", String(100), nullable=True),
    # Sprint 37 (ADR-034, migration 0019) — per-pipeline notification destination,
    # replacing the deployment-global `RESEND_API_KEY`/`SLACK_WEBHOOK_URL`/
    # `TEAMS_WEBHOOK_URL`/`GOOGLE_CHAT_WEBHOOK_URL` env vars `services/notifications.py`
    # otherwise falls back to. `notification_channel` is `NULL` (the default for
    # every existing and new pipeline) until an operator explicitly sets it via
    # `PUT /pipelines/{id}/notifications` — zero behavior change until then, same
    # "NULL = no override" convention as `llm_provider`/`llm_model` above.
    #
    # `notification_target_ciphertext` is a Fernet token (ADR-034: reuses
    # `services/secrets_service`'s encryption mechanism/key,
    # `AI_ETL_SECRETS_ENCRYPTION_KEY` — not the `tenant_secrets` table itself,
    # since that table is keyed `(tenant_id, name)` for tenant-wide named
    # credentials, not one destination scoped to a single pipeline row). Holds an
    # email recipient list (comma-separated, "email" channel) or a webhook URL
    # ("slack"/"teams"/"google_chat") — never the plaintext value, never logged,
    # never returned by any API endpoint (see `audit/db/pipelines.py::
    # get_saved_pipeline_notification_config`, which returns only whether a target
    # is configured, not its value).
    Column("notification_channel", String(20), nullable=True),
    Column("notification_target_ciphertext", Text, nullable=True),
    # Lets an operator temporarily silence a configured destination without
    # clearing it (e.g. a webhook rotation in progress) — `True` by default so a
    # freshly configured destination is active immediately.
    Column("notification_active", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_saved_pipelines_active_next_run", "is_active", "next_run_at"),
    Index("ix_saved_pipelines_tenant", "tenant_id"),
)

# Sprint 19 (ADR-022, migration 0011) — per-tenant encrypted credentials for
# external source connectors (e.g. a REST API key), replacing the
# process-wide shared env var every connector reads today. `ciphertext` is
# a Fernet token (see services/secrets_service.py) — the decrypted value
# never lives in this table, is never logged, and is only ever returned by
# `secrets_service.get_secret`, which no read-only API endpoint calls.
tenant_secrets = Table(
    "tenant_secrets",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, ForeignKey("users.id"), nullable=False),
    Column("name", String(200), nullable=False),
    Column("ciphertext", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "name", name="uq_tenant_secrets_tenant_name"),
    Index("ix_tenant_secrets_tenant", "tenant_id"),
)

# Sprint 24 (ADR-025, migration 0014) — evidence that a tenant data-erasure
# request (LGPD/GDPR, DELETE /tenant) was fulfilled. Deliberately has no FK
# to `users.id`: the row it describes is deleted by the same operation that
# writes this log entry (services/tenant_deletion_service.py). Stores only
# the opaque tenant id (same classification as `users.id` today, not
# sensitive on its own) and row/key counts — never the personal data itself.
tenant_deletion_log = Table(
    "tenant_deletion_log",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("requested_by", String, nullable=False),
    Column("requested_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("runs_deleted", Integer, nullable=False, server_default="0"),
    Column("analysis_runs_deleted", Integer, nullable=False, server_default="0"),
    Column("stage_latencies_deleted", Integer, nullable=False, server_default="0"),
    Column("saved_pipelines_deleted", Integer, nullable=False, server_default="0"),
    Column("secrets_deleted", Integer, nullable=False, server_default="0"),
    Column("storage_keys_deleted", Integer, nullable=False, server_default="0"),
    # "completed" | "failed" — see ADR-025 Decision 3: a partial storage
    # cleanup failure still leaves status "completed" with `error` set, since
    # the DB-side erasure (the higher-confidence signal) succeeded.
    Column("status", String(20), nullable=False),
    Column("error", Text, nullable=True),
    Index("ix_tenant_deletion_log_tenant", "tenant_id"),
)

# Sprint 31 (ADR-032, migration 0017) — every platform-admin action that
# reads or writes another tenant's data. Deliberately its own table, not
# `PipelineState.audit_log` (per-run, transient) and not `tenant_deletion_log`
# above (a different, narrower event). Written by `audit/admin_log.py`, never
# by `audit/db.py` (Sprint 31 isolation: avoid touching that file while other
# sprints run in parallel against it). No FK to `users.id` on either side —
# `actor_user_id` is a platform admin (may not even be a tenant themselves)
# and `target_tenant_id` may reference a tenant that no longer exists by the
# time this row is read back, same reasoning as `tenant_deletion_log`.
admin_action_log = Table(
    "admin_action_log",
    metadata,
    Column("id", String, primary_key=True),
    Column("actor_user_id", String, nullable=False),
    Column("action", String(100), nullable=False),
    Column("target_tenant_id", String, nullable=True),
    Column("detail", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_admin_action_log_actor", "actor_user_id"),
    Index("ix_admin_action_log_target", "target_tenant_id"),
    Index("ix_admin_action_log_created", "created_at"),
)
