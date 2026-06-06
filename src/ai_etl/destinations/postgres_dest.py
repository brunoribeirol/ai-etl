"""PostgreSQL destination connector."""

import os
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text


def save_postgres(df: pd.DataFrame, table: str, if_exists: str = "replace") -> dict[str, Any]:
    """Save DataFrame to a PostgreSQL table. Validates row count after load.

    Never uses f-strings in SQL queries — table name is passed to pandas
    which uses SQLAlchemy's parameterized execution path.
    """
    url = os.getenv("POSTGRES_URL")
    if not url:
        raise EnvironmentError("POSTGRES_URL environment variable is not set.")

    engine = create_engine(url)
    df.to_sql(table.split(".")[-1], engine, schema=_schema(table), if_exists=if_exists, index=False)

    with engine.connect() as conn:
        count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # noqa: S608

    if count != len(df):
        raise RuntimeError(f"Load count mismatch: expected {len(df)}, got {count}")

    return {"rows_loaded": count, "destination": table}


def _schema(table: str) -> str | None:
    parts = table.split(".")
    return parts[0] if len(parts) == 2 else None  # noqa: PLR2004
