"""Unit tests for the SQLite source connector (Sprint 11, ADR-012).

Uses real `.db` files under `tmp_path` and the stdlib `sqlite3` module to
seed them — no mocking of the connector itself, matching ADR-012's rationale
for picking SQLite (real end-to-end coverage with zero added infrastructure).
"""

import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy.exc import OperationalError

from ai_etl.sources.sqlite_source import _validate_table_name, load_sqlite


def _seed_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, product TEXT, revenue REAL)")
        conn.executemany(
            "INSERT INTO orders (id, product, revenue) VALUES (?, ?, ?)",
            [(1, "A", 100.0), (2, "B", 200.0), (3, "C", 300.0)],
        )
        conn.commit()


def test_load_sqlite_reads_table(tmp_path: Path) -> None:
    db_path = tmp_path / "shop.db"
    _seed_db(db_path)

    df = load_sqlite(str(db_path), "orders")

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["id", "product", "revenue"]
    assert len(df) == 3
    assert df.loc[df["id"] == 2, "product"].iloc[0] == "B"


def test_load_sqlite_custom_query(tmp_path: Path) -> None:
    db_path = tmp_path / "shop.db"
    _seed_db(db_path)

    df = load_sqlite(str(db_path), "orders", query="SELECT product FROM orders WHERE revenue > 150")

    assert list(df.columns) == ["product"]
    assert set(df["product"]) == {"B", "C"}


def test_load_sqlite_missing_table_raises(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.db"
    with sqlite3.connect(db_path):
        pass  # create an empty db file, no tables

    with pytest.raises(OperationalError):
        load_sqlite(str(db_path), "orders")


@pytest.mark.parametrize("name", ["orders", "_private_table", "orders2"])
def test_valid_table_names_pass(name: str) -> None:
    _validate_table_name(name)  # must not raise


@pytest.mark.parametrize(
    "name",
    [
        "orders; DROP TABLE orders; --",
        "public.orders",  # SQLite has no schema.table notion — dots rejected
        "1_invalid_start",
        "",
        "table name with spaces",
        "table'injection",
    ],
)
def test_invalid_table_names_raise(name: str) -> None:
    with pytest.raises(ValueError, match="Invalid table name"):
        _validate_table_name(name)


def test_load_sqlite_rejects_invalid_table_before_touching_db(tmp_path: Path) -> None:
    db_path = tmp_path / "shop.db"
    _seed_db(db_path)

    with pytest.raises(ValueError, match="Invalid table name"):
        load_sqlite(str(db_path), "orders; DROP TABLE orders; --")
