"""Unit tests for the application database connection module."""

import pytest

from ai_etl.audit import connection


@pytest.fixture(autouse=True)
def _clear_engine_cache() -> None:
    connection.get_engine.cache_clear()
    yield
    connection.get_engine.cache_clear()


def test_get_engine_raises_when_app_database_url_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)

    with pytest.raises(EnvironmentError, match="APP_DATABASE_URL"):
        connection.get_engine()


def test_get_engine_uses_app_database_url_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://user:pass@localhost:5434/db")

    engine = connection.get_engine()

    assert str(engine.url) == "postgresql://user:***@localhost:5434/db"
