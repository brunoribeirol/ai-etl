"""Unit tests for the MySQL/MariaDB source connector (Sprint 11, ADR-012).

No live MySQL here (that's `tests/integration/test_mysql_source_real.py`,
self-skipping without `make mysql-test-up`) — this covers what's testable
without a server: table-name validation (real, unmocked) and the
MYSQL_URL-missing failure path, same split `postgres_source.py`'s own test
coverage already uses (`tests/unit/test_sources.py` covers only its
validation function directly).
"""

import pandas as pd
import pytest

from ai_etl.core.sql_safety import validate_table_name
from ai_etl.core.tenant_context import tenant_connections
from ai_etl.sources.mysql_source import load_mysql


def _validate_table_name(name: str) -> None:
    validate_table_name(name, allow_dots=True)


@pytest.mark.parametrize("name", ["orders", "shop.orders", "schema_1.table_2", "_private"])
def test_valid_table_names_pass(name: str) -> None:
    _validate_table_name(name)  # must not raise


@pytest.mark.parametrize(
    "name",
    [
        "orders; DROP TABLE orders; --",
        "orders; SELECT 1",
        "1_invalid_start",
        "",
        "table name with spaces",
        "table'injection",
    ],
)
def test_invalid_table_names_raise(name: str) -> None:
    with pytest.raises(ValueError, match="Invalid table name"):
        _validate_table_name(name)


def test_load_mysql_missing_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MYSQL_URL", raising=False)
    with pytest.raises(EnvironmentError, match="MYSQL_URL"):
        load_mysql("orders")


def test_load_mysql_rejects_invalid_table_before_reading_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # MYSQL_URL is set, so the failure must come from table validation, not
    # the environment-variable check — this ordering matters because the
    # invalid-table error message shouldn't be masked by a missing-URL one.
    monkeypatch.setenv("MYSQL_URL", "mysql+pymysql://user:pass@localhost/db")
    with pytest.raises(ValueError, match="Invalid table name"):
        load_mysql("orders; DROP TABLE orders; --")


def test_load_mysql_rejects_destructive_query(monkeypatch: pytest.MonkeyPatch) -> None:
    # Red Team, 2026-08-24 audit: same unvalidated-`query` gap as sqlite_source.py.
    monkeypatch.setenv("MYSQL_URL", "mysql+pymysql://user:pass@localhost/db")
    with pytest.raises(ValueError):
        load_mysql("orders", query="DROP TABLE orders; --")


def test_load_mysql_uses_tenant_override_over_shared_env(monkeypatch, mocker) -> None:
    # ADR-044: a tenant's own stored connection string must win over the
    # deployment-wide MYSQL_URL, even when the latter is set.
    monkeypatch.setenv("MYSQL_URL", "mysql+pymysql://shared:pass@shared-host/db")
    mock_engine = mocker.MagicMock()
    create_engine_mock = mocker.patch(
        "ai_etl.sources.mysql_source.create_engine", return_value=mock_engine
    )
    mocker.patch("ai_etl.sources.mysql_source.pd.read_sql", return_value=pd.DataFrame({"x": [1]}))

    with tenant_connections({"mysql": "mysql+pymysql://tenant:pass@tenant-host/db"}):
        load_mysql("orders")

    create_engine_mock.assert_called_once_with("mysql+pymysql://tenant:pass@tenant-host/db")


def test_load_mysql_falls_back_to_shared_env_with_no_override(monkeypatch, mocker) -> None:
    monkeypatch.setenv("MYSQL_URL", "mysql+pymysql://shared:pass@shared-host/db")
    mock_engine = mocker.MagicMock()
    create_engine_mock = mocker.patch(
        "ai_etl.sources.mysql_source.create_engine", return_value=mock_engine
    )
    mocker.patch("ai_etl.sources.mysql_source.pd.read_sql", return_value=pd.DataFrame({"x": [1]}))

    load_mysql("orders")

    create_engine_mock.assert_called_once_with("mysql+pymysql://shared:pass@shared-host/db")
