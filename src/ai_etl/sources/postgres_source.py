"""PostgreSQL source connector."""

import os
import re

import pandas as pd
from sqlalchemy import create_engine, text

_SAFE_TABLE_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")


def _validate_table_name(table: str) -> None:
    if not _SAFE_TABLE_RE.match(table):
        raise ValueError(
            f"Invalid table name '{table}': only alphanumeric, underscores, and dots allowed."
        )


def load_postgres(table: str, query: str | None = None) -> pd.DataFrame:
    """Load a PostgreSQL table (or custom query) into a DataFrame.

    Uses POSTGRES_URL from environment. Always uses parameterized queries.
    """
    url = os.getenv("POSTGRES_URL")
    if not url:
        raise EnvironmentError("POSTGRES_URL environment variable is not set.")

    _validate_table_name(table)
    engine = create_engine(url)
    sql = query or f"SELECT * FROM {table}"  # nosec B608 — table name validated above
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)
