"""PostgreSQL source connector."""

import os

import pandas as pd
from sqlalchemy import create_engine, text

from ai_etl.core.sql_safety import validate_select_only_query, validate_table_name
from ai_etl.core.tenant_context import get_connection_override


def load_postgres(table: str, query: str | None = None) -> pd.DataFrame:
    """Load a PostgreSQL table (or custom query) into a DataFrame.

    Uses the current run's tenant-supplied connection string (ADR-044,
    `core/tenant_context.py`) if one is set, otherwise falls back to the
    shared POSTGRES_URL env var (ADR-022's original, still-default
    behavior). Always uses parameterized queries. A custom `query` is
    validated via `core.sql_safety.validate_select_only_query` (Wave 5
    gap-closing fix, 2026-08-25 — this connector was missed when
    `sqlite_source.py`/`mysql_source.py` got the same check during the Wave 0
    CRITICAL SQL-injection fix, 2026-08-24 audit; `agents/pipeline/extractor.py`
    doesn't currently forward a `query` to this connector at all, so this was
    latent, not reachable from `pipeline_plan` today — closed anyway, on the
    same "validate at the connector, not the caller" posture as the other two).
    """
    url = get_connection_override("postgres") or os.getenv("POSTGRES_URL")
    if not url:
        raise EnvironmentError("POSTGRES_URL environment variable is not set.")

    validate_table_name(table, allow_dots=True)
    if query is not None:
        validate_select_only_query(query)
    engine = create_engine(url)
    sql = query or f"SELECT * FROM {table}"  # nosec B608 — table name validated above
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)
