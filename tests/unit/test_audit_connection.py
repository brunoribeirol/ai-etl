"""Unit tests for the application database connection module."""

import pytest

from ai_etl.audit import connection


@pytest.fixture(autouse=True)
def _clear_engine_cache() -> None:
    connection.get_engine.cache_clear()
    connection.get_tenant_engine.cache_clear()
    yield
    connection.get_engine.cache_clear()
    connection.get_tenant_engine.cache_clear()


def test_get_engine_raises_when_app_database_url_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)

    with pytest.raises(EnvironmentError, match="APP_DATABASE_URL"):
        connection.get_engine()


def test_get_engine_uses_app_database_url_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://user:pass@localhost:5434/db")

    engine = connection.get_engine()

    assert str(engine.url) == "postgresql://user:***@localhost:5434/db"


def test_get_tenant_engine_raises_when_app_database_url_tenant_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APP_DATABASE_URL_TENANT", raising=False)

    with pytest.raises(EnvironmentError, match="APP_DATABASE_URL_TENANT"):
        connection.get_tenant_engine()


def test_get_tenant_engine_uses_app_database_url_tenant_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_DATABASE_URL_TENANT", "postgresql://tenant_role:pass@localhost:5434/db")

    engine = connection.get_tenant_engine()

    assert str(engine.url) == "postgresql://tenant_role:***@localhost:5434/db"


def test_get_tenant_engine_is_a_distinct_cached_singleton_from_get_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two engines must never collapse into the same cached object — they
    connect with different roles/privileges (ADR-040)."""
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://bypass_role:pass@localhost:5434/db")
    monkeypatch.setenv("APP_DATABASE_URL_TENANT", "postgresql://tenant_role:pass@localhost:5434/db")

    bypass_engine = connection.get_engine()
    tenant_engine = connection.get_tenant_engine()

    assert bypass_engine is not tenant_engine
    assert str(bypass_engine.url) != str(tenant_engine.url)
    # Cached: a second call returns the same object, not a fresh engine.
    assert connection.get_engine() is bypass_engine
    assert connection.get_tenant_engine() is tenant_engine


def test_tenant_scope_sets_guc_via_bound_parameter_not_string_interpolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for this project's non-negotiable "no f-string SQL"
    rule (CLAUDE.md) — even applied to an internally-sourced `tenant_id`: the
    GUC-setting statement must be a fixed string with `tenant_id` passed as a
    bound parameter, never interpolated into the SQL text itself. Captures
    the actual `(statement, params)` pair passed to `Connection.execute`
    without needing a real Postgres."""
    captured: list[tuple[str, dict]] = []

    class _FakeConnection:
        def execute(self, statement, params=None):  # noqa: ANN001
            captured.append((str(statement), dict(params or {})))

            class _Result:
                def first(self):
                    return None

            return _Result()

    class _FakeTransactionCtx:
        def __enter__(self):
            return _FakeConnection()

        def __exit__(self, *exc_info):
            return False

    class _FakeEngine:
        def begin(self):
            return _FakeTransactionCtx()

    monkeypatch.setattr(connection, "get_tenant_engine", lambda: _FakeEngine())

    malicious_tenant_id = "tenant'); DROP TABLE users; --"
    with connection.tenant_scope(malicious_tenant_id):
        pass

    assert len(captured) == 1
    statement, params = captured[0]
    # The tenant_id value must appear only as a bound parameter, never spliced
    # into the SQL string itself.
    assert malicious_tenant_id not in statement
    assert params == {"tenant_id": malicious_tenant_id}
    assert "set_config" in statement.lower()


def test_scoped_connection_uses_tenant_scope_when_tenant_id_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str | None] = []

    from contextlib import contextmanager

    @contextmanager
    def _fake_tenant_scope(tenant_id):  # noqa: ANN001
        calls.append(("tenant_scope", tenant_id))
        yield "tenant-conn"

    def _fake_get_engine():
        raise AssertionError("get_engine() should not be called when tenant_id is given")

    monkeypatch.setattr(connection, "tenant_scope", _fake_tenant_scope)
    monkeypatch.setattr(connection, "get_engine", _fake_get_engine)

    with connection.scoped_connection("tenant-a") as conn:
        assert conn == "tenant-conn"

    assert calls == [("tenant_scope", "tenant-a")]


def test_scoped_connection_falls_back_to_bypass_engine_when_tenant_id_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeConnection:
        pass

    fake_conn = _FakeConnection()

    class _FakeTransactionCtx:
        def __enter__(self):
            return fake_conn

        def __exit__(self, *exc_info):
            return False

    class _FakeEngine:
        def begin(self):
            return _FakeTransactionCtx()

    def _fake_tenant_scope(tenant_id):  # noqa: ANN001
        raise AssertionError("tenant_scope() should not be called when tenant_id is None")

    monkeypatch.setattr(connection, "tenant_scope", _fake_tenant_scope)
    monkeypatch.setattr(connection, "get_engine", lambda: _FakeEngine())

    with connection.scoped_connection(None) as conn:
        assert conn is fake_conn
