"""Integration test for the MySQL/MariaDB source connector (Sprint 11, ADR-012).

Exercises `sources/mysql_source.py::load_mysql` against a live MySQL —
`mysql-test` in docker-compose.yml (`make mysql-test-up`). Skipped
automatically when that database isn't reachable, matching
`test_audit_persistence.py`'s skip convention — so `make test`/CI keep
passing on machines without Docker.
"""

import os
import uuid

import pandas as pd
import pytest
import sqlalchemy
from sqlalchemy import text

from ai_etl.sources.mysql_source import load_mysql

_TEST_MYSQL_URL = os.getenv("TEST_MYSQL_URL", "mysql+pymysql://test:test@localhost:3307/testdb")


def _database_reachable() -> bool:
    try:
        engine = sqlalchemy.create_engine(_TEST_MYSQL_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_reachable(),
    reason="mysql-test not reachable; run `make mysql-test-up` to enable this test",
)


@pytest.fixture
def mysql_orders_table(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("MYSQL_URL", _TEST_MYSQL_URL)
    table_name = f"orders_{uuid.uuid4().hex[:8]}"
    engine = sqlalchemy.create_engine(_TEST_MYSQL_URL)
    with engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE TABLE {table_name} (id INT PRIMARY KEY, product VARCHAR(50), revenue FLOAT)"
            )
        )
        conn.execute(
            text(
                f"INSERT INTO {table_name} (id, product, revenue) VALUES (:id, :product, :revenue)"
            ),
            [
                {"id": 1, "product": "A", "revenue": 100.0},
                {"id": 2, "product": "B", "revenue": 200.0},
            ],
        )
    yield table_name
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE {table_name}"))
    engine.dispose()


def test_load_mysql_reads_table_for_real(mysql_orders_table: str) -> None:
    df = load_mysql(mysql_orders_table)

    assert isinstance(df, pd.DataFrame)
    assert set(df["product"]) == {"A", "B"}
    assert len(df) == 2


def test_load_mysql_custom_query_for_real(mysql_orders_table: str) -> None:
    df = load_mysql(
        mysql_orders_table, query=f"SELECT product FROM {mysql_orders_table} WHERE revenue > 150"
    )

    assert list(df.columns) == ["product"]
    assert list(df["product"]) == ["B"]


def test_load_mysql_invalid_table_name_raises(mysql_orders_table: str) -> None:
    with pytest.raises(ValueError, match="Invalid table name"):
        load_mysql("orders; DROP TABLE orders; --")
