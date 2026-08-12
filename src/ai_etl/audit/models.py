"""SQLAlchemy Core table definitions for the application database.

Mirrors the `runs`/`analysis_runs` tables Phase 1 kept in SQLite
(`{log_dir}/runs.db`), now backed by the application Postgres so run
history survives across processes and Streamlit redeploys instead of
living in one file per deployment. `tenant_id` is a Sprint A stopgap
holding a per-browser-session UUID for isolating history between
concurrent Streamlit sessions — it is not a real tenant/account column;
that's still future work.

Core (not the ORM) matches the style already used in
`sources/postgres_source.py` and `destinations/postgres_dest.py`.
"""

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text

metadata = MetaData()

runs = Table(
    "runs",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("spec", Text, nullable=False),
    Column("status", String(20), nullable=False),
    Column("error", Text, nullable=True),
    Column("rows_loaded", Integer, nullable=True),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("tenant_id", String, nullable=True),
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
    Column("tenant_id", String, nullable=True),
)
