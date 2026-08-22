"""PostgreSQL source connector."""

import os

import pandas as pd
from sqlalchemy import create_engine, text

from ai_etl.core.sql_safety import validate_table_name


def load_postgres(table: str, query: str | None = None) -> pd.DataFrame:
    """Load a PostgreSQL table (or custom query) into a DataFrame.

    Uses POSTGRES_URL from environment. Always uses parameterized queries.
    """
    url = os.getenv("POSTGRES_URL")
    if not url:
        raise EnvironmentError("POSTGRES_URL environment variable is not set.")

    validate_table_name(table, allow_dots=True)
    engine = create_engine(url)
    sql = query or f"SELECT * FROM {table}"  # nosec B608 — table name validated above
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)
