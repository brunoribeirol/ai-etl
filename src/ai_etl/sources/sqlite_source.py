"""SQLite source connector (Sprint 11, ADR-012).

Same shape as `postgres_source.py`: module-level `load_<type>(...) ->
pd.DataFrame`, table name validated against a safe-identifier allowlist, SQL
only through SQLAlchemy `text()`, no source-level `try/except` (errors
propagate to `agents/pipeline/extractor.py`'s single catch point).

One deliberate difference from `postgres_source.py`: SQLite is file-based,
not a server a single `POSTGRES_URL` env var can point at — each source in
`pipeline_plan.sources` can reference a different `.db` file, so `path` is a
per-source argument here (like `csv_source.py`'s `path`), not an env var.
SQLite also has no `schema.table` notion the way Postgres does, so the table
name regex is stricter (no dots allowed).
"""

import pandas as pd
from sqlalchemy import create_engine, text

from ai_etl.core.sql_safety import validate_table_name


def load_sqlite(path: str, table: str, query: str | None = None) -> pd.DataFrame:
    """Load a SQLite table (or custom query) into a DataFrame.

    `path` is the `.db`/`.sqlite` file path. Always uses parameterized
    queries; `table` is validated via `core.sql_safety.validate_table_name`
    (dots disallowed — SQLite has no `schema.table` notion) before being
    interpolated into the default `SELECT *` (matching `postgres_source.py`'s
    validated-identifier pattern — SQLAlchemy has no bind-parameter syntax
    for identifiers, only values).
    """
    validate_table_name(table, allow_dots=False)
    engine = create_engine(f"sqlite:///{path}")
    sql = query or f"SELECT * FROM {table}"  # nosec B608 — table name validated above
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)
