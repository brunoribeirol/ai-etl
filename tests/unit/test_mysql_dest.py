"""Unit tests for `destinations/mysql_dest.py::save_mysql`/`preview_mysql`.

Same real-sqlite-via-env-var trick `test_postgres_dest.py` uses — SQLAlchemy
doesn't care that the URL scheme says `sqlite` while the module's own name
says MySQL; the connector just passes whatever URL it resolves to
`create_engine`, so this exercises the real read/write path without a real
MySQL server.
"""

from __future__ import annotations

import pandas as pd
import pytest
import sqlalchemy

from ai_etl.core.tenant_context import tenant_connections
from ai_etl.destinations.mysql_dest import preview_mysql, save_mysql

_DF = pd.DataFrame({"a": [1, 2, 3]})


def test_save_mysql_missing_env_raises(monkeypatch) -> None:
    monkeypatch.delenv("MYSQL_URL", raising=False)
    with pytest.raises(EnvironmentError, match="MYSQL_URL"):
        save_mysql(_DF, "output")


def test_save_mysql_invalid_table_name_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MYSQL_URL", f"sqlite:///{tmp_path}/db.sqlite")
    with pytest.raises(ValueError, match="Invalid table name"):
        save_mysql(_DF, "bad; drop table users;")


def test_save_mysql_writes_to_the_shared_env_database(monkeypatch, tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/shared.sqlite"
    monkeypatch.setenv("MYSQL_URL", db_url)

    result = save_mysql(_DF, "output")

    assert result == {"rows_loaded": 3, "destination": "output"}
    engine = sqlalchemy.create_engine(db_url)
    with engine.connect() as conn:
        count = conn.execute(sqlalchemy.text("SELECT COUNT(*) FROM output")).scalar()
    assert count == 3


def test_save_mysql_uses_tenant_override_over_shared_env(monkeypatch, tmp_path) -> None:
    # ADR-044: the shared env var points at one sqlite file, the tenant
    # override at another — only the override's file should receive the write.
    shared_db_url = f"sqlite:///{tmp_path}/shared.sqlite"
    tenant_db_url = f"sqlite:///{tmp_path}/tenant.sqlite"
    monkeypatch.setenv("MYSQL_URL", shared_db_url)

    with tenant_connections({"mysql": tenant_db_url}):
        result = save_mysql(_DF, "output")

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


def test_preview_mysql_missing_env_raises(monkeypatch) -> None:
    monkeypatch.delenv("MYSQL_URL", raising=False)
    with pytest.raises(EnvironmentError, match="MYSQL_URL"):
        preview_mysql(_DF, "output")


def test_preview_mysql_table_does_not_exist_yet(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MYSQL_URL", f"sqlite:///{tmp_path}/db.sqlite")

    result = preview_mysql(_DF, "output")

    assert result["destination_type"] == "mysql"
    assert result["would_write_rows"] == 3
    assert result["existing"] is None


def test_preview_mysql_existing_table_reports_row_count(monkeypatch, tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/db.sqlite"
    monkeypatch.setenv("MYSQL_URL", db_url)

    engine = sqlalchemy.create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text("CREATE TABLE output (a INTEGER)"))
        conn.execute(sqlalchemy.text("INSERT INTO output (a) VALUES (1), (2)"))

    result = preview_mysql(_DF, "output")

    assert result["would_write_rows"] == 3
    assert result["existing"]["existing_rows"] == 2


def test_preview_mysql_never_writes(monkeypatch, tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path}/db.sqlite"
    monkeypatch.setenv("MYSQL_URL", db_url)

    preview_mysql(_DF, "output")

    engine = sqlalchemy.create_engine(db_url)
    with engine.connect() as conn:
        tables = conn.execute(
            sqlalchemy.text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    assert tables == []
