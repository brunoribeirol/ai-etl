"""Application database connection.

This is a separate Postgres database from `POSTGRES_URL` (`sources/postgres_source.py`,
`destinations/postgres_dest.py`), which is a pipeline data source/destination the user
points at their own data. `APP_DATABASE_URL` is AI-ETL's own database, holding the
`runs`/`analysis_runs` audit tables — logically distinct even when both run in the same
docker-compose project.
"""

import os
from functools import lru_cache

from sqlalchemy import Engine, create_engine

_DEFAULT_APP_DATABASE_URL = "postgresql://ai_etl_app:ai_etl_app@localhost:5434/ai_etl_app_db"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return a process-wide engine for the application database.

    Cached per-process: `APP_DATABASE_URL` is read once. Tests that need a different
    URL should call `get_engine.cache_clear()` after setting the environment variable.
    """
    url = os.getenv("APP_DATABASE_URL", _DEFAULT_APP_DATABASE_URL)
    return create_engine(url, pool_pre_ping=True)
