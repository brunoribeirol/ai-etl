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

from sqlalchemy import Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),  # Clerk user_id — not a UUID
    Column("created_at", DateTime(timezone=True), nullable=False),
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
)
