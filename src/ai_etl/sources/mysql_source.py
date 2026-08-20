"""MySQL/MariaDB source connector (Sprint 11, ADR-012).

Same shape and conventions as `postgres_source.py`: module-level
`load_<type>(...) -> pd.DataFrame`, a single shared connection string read
from an env var (MySQL/MariaDB are server processes, like Postgres — unlike
SQLite's per-source file path), table name validated against a safe-
identifier allowlist, SQL only through SQLAlchemy `text()`, no source-level
`try/except` (errors propagate to `agents/extractor.py`'s single catch
point).

Driver: `pymysql` (pure Python, SQLAlchemy dialect `mysql+pymysql`) — no
compiled/system dependency, the same "drop-in" bar `psycopg2-binary` already
meets for `postgres_source.py`. MariaDB is wire-compatible with this driver
and dialect, so `MYSQL_URL` works unchanged against either engine — this
connector deliberately doesn't distinguish "MySQL" from "MariaDB" the way it
would need to for an engine with a different wire protocol.
"""

import os
import re

import pandas as pd
from sqlalchemy import create_engine, text

# Unlike sqlite_source.py, MySQL supports `database.table` — same allowlist
# shape as postgres_source.py's `_SAFE_TABLE_RE`.
_SAFE_TABLE_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")


def _validate_table_name(table: str) -> None:
    if not _SAFE_TABLE_RE.match(table):
        raise ValueError(
            f"Invalid table name '{table}': only alphanumeric, underscores, and dots allowed."
        )


def load_mysql(table: str, query: str | None = None) -> pd.DataFrame:
    """Load a MySQL/MariaDB table (or custom query) into a DataFrame.

    Uses MYSQL_URL from environment (e.g.
    `mysql+pymysql://user:pass@host:3306/db`). Always uses parameterized
    queries.
    """
    url = os.getenv("MYSQL_URL")
    if not url:
        raise EnvironmentError("MYSQL_URL environment variable is not set.")

    _validate_table_name(table)
    engine = create_engine(url)
    sql = query or f"SELECT * FROM {table}"  # nosec B608 — table name validated above
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)
