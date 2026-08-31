"""Unit tests for the PostgreSQL source connector (Sprint 11, ADR-012).

No live Postgres here (that's `tests/integration/`'s job) — this covers what's
testable without a server: table-name validation (real, unmocked) and the
POSTGRES_URL-missing failure path, same split `mysql_source.py`'s own test
docstring already documents.

Gap-closing (2026-08-24 QA audit, Wave 5): `load_postgres` itself (8 lines)
previously had zero coverage, not even mocked. Writing that coverage surfaced
a second real gap, also closed here (2026-08-25): `load_postgres` accepted a
custom `query` with no validation at all — sqlite_source.py/mysql_source.py
both got `validate_select_only_query` during the Wave 0 CRITICAL SQL-
injection fix (2026-08-24 audit), postgres_source.py was missed.
"""

import pandas as pd
import pytest

from ai_etl.core.sql_safety import validate_table_name
from ai_etl.core.tenant_context import tenant_connections
from ai_etl.sources.postgres_source import load_postgres


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


def test_load_postgres_missing_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(EnvironmentError, match="POSTGRES_URL"):
        load_postgres("orders")


def test_load_postgres_rejects_invalid_table_before_reading_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # POSTGRES_URL is set, so the failure must come from table validation, not
    # the environment-variable check — this ordering matters because the
    # invalid-table error message shouldn't be masked by a missing-URL one.
    monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pass@localhost:5432/db")
    with pytest.raises(ValueError, match="Invalid table name"):
        load_postgres("orders; DROP TABLE orders; --")


def test_load_postgres_rejects_destructive_query(monkeypatch: pytest.MonkeyPatch) -> None:
    # Gap-closing fix (2026-08-25, Wave 5) — this connector was missed when
    # sqlite_source.py/mysql_source.py got the same check during the Wave 0
    # CRITICAL SQL-injection fix (2026-08-24 audit). Same payload Red Team
    # used against sqlite: {"query": "DROP TABLE orders; --"}.
    monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pass@localhost:5432/db")
    with pytest.raises(ValueError):
        load_postgres("orders", query="DROP TABLE orders; --")


def test_load_postgres_uses_tenant_override_over_shared_env(monkeypatch, mocker) -> None:
    # ADR-044: a tenant's own stored connection string (resolved by
    # `services/pipeline_service.py` into a `tenant_connections` block) must
    # win over the deployment-wide POSTGRES_URL, even when the latter is set.
    monkeypatch.setenv("POSTGRES_URL", "postgresql://shared:pass@shared-host/db")
    mock_engine = mocker.MagicMock()
    create_engine_mock = mocker.patch(
        "ai_etl.sources.postgres_source.create_engine", return_value=mock_engine
    )
    mocker.patch(
        "ai_etl.sources.postgres_source.pd.read_sql", return_value=pd.DataFrame({"x": [1]})
    )

    with tenant_connections({"postgres": "postgresql://tenant:pass@tenant-host/db"}):
        load_postgres("orders")

    create_engine_mock.assert_called_once_with("postgresql://tenant:pass@tenant-host/db")


def test_load_postgres_falls_back_to_shared_env_with_no_override(monkeypatch, mocker) -> None:
    # No active tenant_connections block (the default, e.g. an avulso run
    # with no tenant_id, or a tenant that never configured this secret) —
    # behavior must be identical to before ADR-044.
    monkeypatch.setenv("POSTGRES_URL", "postgresql://shared:pass@shared-host/db")
    mock_engine = mocker.MagicMock()
    create_engine_mock = mocker.patch(
        "ai_etl.sources.postgres_source.create_engine", return_value=mock_engine
    )
    mocker.patch(
        "ai_etl.sources.postgres_source.pd.read_sql", return_value=pd.DataFrame({"x": [1]})
    )

    load_postgres("orders")

    create_engine_mock.assert_called_once_with("postgresql://shared:pass@shared-host/db")
