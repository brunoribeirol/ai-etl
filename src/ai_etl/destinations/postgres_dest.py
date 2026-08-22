"""PostgreSQL destination connector."""

import os
from typing import Any, Literal

import pandas as pd
from sqlalchemy import create_engine, text

from ai_etl.core.sql_safety import validate_table_name


def save_postgres(
    df: pd.DataFrame,
    table: str,
    if_exists: Literal["fail", "replace", "append", "delete_rows"] = "replace",
) -> dict[str, Any]:
    """Save DataFrame to a PostgreSQL table. Validates row count after load.

    Never uses f-strings in SQL queries — table name is passed to pandas
    which uses SQLAlchemy's parameterized execution path.
    """
    url = os.getenv("POSTGRES_URL")
    if not url:
        raise EnvironmentError("POSTGRES_URL environment variable is not set.")

    validate_table_name(table, allow_dots=True)
    engine = create_engine(url)
    df.to_sql(table.split(".")[-1], engine, schema=_schema(table), if_exists=if_exists, index=False)

    with engine.connect() as conn:
        count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # nosec B608 — table name validated above

    if count != len(df):
        raise RuntimeError(f"Load count mismatch: expected {len(df)}, got {count}")

    return {"rows_loaded": count, "destination": table}


def _schema(table: str) -> str | None:
    parts = table.split(".")
    return parts[0] if len(parts) == 2 else None  # noqa: PLR2004


def preview_postgres(df: pd.DataFrame, table: str) -> dict[str, Any]:
    """Sprint 27 (ADR-028) — what `save_postgres` would do, without writing
    anything. Never calls `to_sql` — reads the table's current row count (if
    it already exists) for the diff, same read-only `SELECT COUNT(*)` shape
    `save_postgres` itself uses to verify a write after the fact, guarded by
    the same `core.sql_safety.validate_table_name` check.

    `existing_rows` is `None` if the table doesn't exist yet (a fresh table,
    or a real connection error surfacing as "can't tell") — distinguished
    from "0 existing rows" (a real, empty table) so the preview doesn't claim
    certainty it doesn't have.
    """
    url = os.getenv("POSTGRES_URL")
    if not url:
        raise EnvironmentError("POSTGRES_URL environment variable is not set.")

    validate_table_name(table, allow_dots=True)
    engine = create_engine(url)
    existing_rows: int | None = None
    try:
        with engine.connect() as conn:
            existing_rows = conn.execute(
                text(f"SELECT COUNT(*) FROM {table}")  # nosec B608 — table name validated above
            ).scalar()
    except Exception:  # nosec B110 — table doesn't exist yet, not a preview error
        existing_rows = None

    return {
        "destination_type": "postgres",
        "destination": table,
        "would_write_rows": len(df),
        "existing": {"existing_rows": existing_rows} if existing_rows is not None else None,
    }
