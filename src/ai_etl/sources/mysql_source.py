"""MySQL/MariaDB source connector (Sprint 11, ADR-012).

Same shape and conventions as `postgres_source.py`: module-level
`load_<type>(...) -> pd.DataFrame`, a single shared connection string read
from an env var (MySQL/MariaDB are server processes, like Postgres — unlike
SQLite's per-source file path), table name validated against a safe-
identifier allowlist, SQL only through SQLAlchemy `text()`, no source-level
`try/except` (errors propagate to `agents/pipeline/extractor.py`'s single catch
point).

Driver: `pymysql` (pure Python, SQLAlchemy dialect `mysql+pymysql`) — no
compiled/system dependency, the same "drop-in" bar `psycopg2-binary` already
meets for `postgres_source.py`. MariaDB is wire-compatible with this driver
and dialect, so `MYSQL_URL` works unchanged against either engine — this
connector deliberately doesn't distinguish "MySQL" from "MariaDB" the way it
would need to for an engine with a different wire protocol.
"""

import os

import pandas as pd
from sqlalchemy import create_engine, text

from ai_etl.core.sql_safety import validate_select_only_query, validate_table_name

# Unlike sqlite_source.py, MySQL supports `database.table` — pass
# allow_dots=True to core.sql_safety.validate_table_name below.


def load_mysql(table: str, query: str | None = None) -> pd.DataFrame:
    """Load a MySQL/MariaDB table (or custom query) into a DataFrame.

    Uses MYSQL_URL from environment (e.g.
    `mysql+pymysql://user:pass@host:3306/db`). Always uses parameterized
    queries. A custom `query` (LLM-generated, from `pipeline_plan`) is
    validated via `core.sql_safety.validate_select_only_query` (Wave 0,
    2026-08-24 audit) — there's no bind-parameter syntax for an entire query,
    so this is that path's injection defense.
    """
    url = os.getenv("MYSQL_URL")
    if not url:
        raise EnvironmentError("MYSQL_URL environment variable is not set.")

    validate_table_name(table, allow_dots=True)
    if query is not None:
        validate_select_only_query(query)
    engine = create_engine(url)
    sql = query or f"SELECT * FROM {table}"  # nosec B608 — table name validated above
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)
