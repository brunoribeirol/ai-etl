"""Unit tests for `destinations/postgres_dest.py::save_postgres` (ADR-044).

Pre-existing gap: `save_postgres` had zero unit coverage before this (only
its sibling `preview_postgres` was tested, in `test_destination_previews.py`)
— closed here alongside the tenant-connection-override behavior this module
adds, using the same real-sqlite-via-POSTGRES_URL trick already established
by `test_destination_previews.py` rather than mocking SQLAlchemy.
"""

from __future__ import annotations

import pandas as pd
import pytest
import sqlalchemy

from ai_etl.core.tenant_context import tenant_connections
from ai_etl.destinations.postgres_dest import save_postgres

_DF = pd.DataFrame({"a": [1, 2, 3]})


def test_save_postgres_missing_env_raises(monkeypatch) -> None:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(EnvironmentError, match="POSTGRES_URL"):
        save_postgres(_DF, "output")


def test_save_postgres_invalid_table_name_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("POSTGRES_URL", f"sqlite:///{tmp_path}/db.sqlite")
    with pytest.raises(ValueError, match="Invalid table name"):
        save_postgres(_DF, "bad; drop table users;")


def test_save_postgres_writes_to_the_shared_env_database(monkeypatch, tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/shared.sqlite"
    monkeypatch.setenv("POSTGRES_URL", db_url)

    result = save_postgres(_DF, "output")

    assert result == {"rows_loaded": 3, "destination": "output"}
    engine = sqlalchemy.create_engine(db_url)
    with engine.connect() as conn:
        count = conn.execute(sqlalchemy.text("SELECT COUNT(*) FROM output")).scalar()
    assert count == 3


def test_save_postgres_uses_tenant_override_over_shared_env(monkeypatch, tmp_path) -> None:
    # ADR-044: the shared env var points at one sqlite file, the tenant
    # override at another — only the override's file should receive the write.
    shared_db_url = f"sqlite:///{tmp_path}/shared.sqlite"
    tenant_db_url = f"sqlite:///{tmp_path}/tenant.sqlite"
    monkeypatch.setenv("POSTGRES_URL", shared_db_url)

    with tenant_connections({"postgres": tenant_db_url}):
        result = save_postgres(_DF, "output")

    assert result == {"rows_loaded": 3, "destination": "output"}

    tenant_engine = sqlalchemy.create_engine(tenant_db_url)
    with tenant_engine.connect() as conn:
        tenant_count = conn.execute(sqlalchemy.text("SELECT COUNT(*) FROM output")).scalar()
    assert tenant_count == 3

    shared_engine = sqlalchemy.create_engine(shared_db_url)
    with shared_engine.connect() as conn:
        tables = conn.execute(
            sqlalchemy.text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    assert tables == []  # nothing was ever written to the shared database
