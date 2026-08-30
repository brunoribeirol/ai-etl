"""Unit tests for `services/secrets_service.py` (Sprint 19, ADR-022).

Same convention as `test_saved_pipelines_db.py`: a real in-memory SQLite
engine (not a mocked `get_engine`) so the actual insert/select/delete
statements execute for real.

ADR-040 follow-up: this module now reads/writes through `tenant_scope()`
(the restricted, RLS-backed role) instead of `get_engine()` (bypass role) —
`tenant_scope` is faked the same way `test_audit_db.py` already established
for every other ADR-040-migrated module, since its real
`SELECT set_config(...)` GUC call is Postgres-only. Real GUC-scoping/RLS
behavior is covered against a live Postgres in
`tests/integration/test_tenant_isolation_rls.py`.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

import pytest
import sqlalchemy
from cryptography.fernet import Fernet
from sqlalchemy import Connection, Engine

from ai_etl.audit.models import metadata as audit_metadata
from ai_etl.audit.models import users as users_table
from ai_etl.services import secrets_service


def _make_sqlite_engine() -> Engine:
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    audit_metadata.create_all(engine)
    return engine


def _fake_scope(engine: Engine):  # noqa: ANN201
    @contextmanager
    def _scope(tenant_id: str) -> Iterator[Connection]:
        with engine.begin() as conn:
            yield conn

    return _scope


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    eng = _make_sqlite_engine()
    monkeypatch.setattr(secrets_service, "tenant_scope", _fake_scope(eng))
    with eng.begin() as conn:
        conn.execute(
            sqlalchemy.insert(users_table).values(
                id="tenant-a", created_at=datetime.now(tz=timezone.utc)
            )
        )
        conn.execute(
            sqlalchemy.insert(users_table).values(
                id="tenant-b", created_at=datetime.now(tz=timezone.utc)
            )
        )
    return eng


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_ETL_SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))


def test_set_then_get_secret_round_trips(engine: Engine) -> None:
    secrets_service.set_secret("tenant-a", "weather_api_key", "sk-super-secret")

    assert secrets_service.get_secret("tenant-a", "weather_api_key") == "sk-super-secret"


def test_get_secret_raises_for_unknown_name(engine: Engine) -> None:
    with pytest.raises(secrets_service.SecretNotFoundError):
        secrets_service.get_secret("tenant-a", "does-not-exist")


def test_secret_is_isolated_per_tenant(engine: Engine) -> None:
    """The core RBAC/secrets DoD: one tenant's credential must not be
    readable from another tenant's lookup, even with the same secret name."""
    secrets_service.set_secret("tenant-a", "shared_name", "tenant-a-value")
    secrets_service.set_secret("tenant-b", "shared_name", "tenant-b-value")

    assert secrets_service.get_secret("tenant-a", "shared_name") == "tenant-a-value"
    assert secrets_service.get_secret("tenant-b", "shared_name") == "tenant-b-value"


def test_stored_ciphertext_never_contains_the_plaintext_value(engine: Engine) -> None:
    """Confirms the value is actually encrypted at rest, not stored as-is —
    a raw DB read must never reveal the secret."""
    secrets_service.set_secret("tenant-a", "db_password", "hunter2-plaintext")

    with engine.connect() as conn:
        row = conn.execute(
            sqlalchemy.text("SELECT ciphertext FROM tenant_secrets WHERE tenant_id = :t"),
            {"t": "tenant-a"},
        ).fetchone()

    assert row is not None
    assert "hunter2-plaintext" not in row[0]


def test_set_secret_upserts_rotating_the_value(engine: Engine) -> None:
    secrets_service.set_secret("tenant-a", "api_key", "v1")
    secrets_service.set_secret("tenant-a", "api_key", "v2")

    assert secrets_service.get_secret("tenant-a", "api_key") == "v2"
    assert secrets_service.list_secret_names("tenant-a") == ["api_key"]  # not duplicated


def test_list_secret_names_returns_names_only_never_values(engine: Engine) -> None:
    secrets_service.set_secret("tenant-a", "api_key", "sk-abc")
    secrets_service.set_secret("tenant-a", "db_password", "hunter2")

    names = secrets_service.list_secret_names("tenant-a")

    assert names == ["api_key", "db_password"]


def test_delete_secret_removes_it(engine: Engine) -> None:
    secrets_service.set_secret("tenant-a", "api_key", "sk-abc")

    deleted = secrets_service.delete_secret("tenant-a", "api_key")

    assert deleted is True
    assert secrets_service.list_secret_names("tenant-a") == []


def test_delete_secret_returns_false_when_nothing_deleted(engine: Engine) -> None:
    assert secrets_service.delete_secret("tenant-a", "does-not-exist") is False


def test_set_secret_fails_closed_without_encryption_key(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AI_ETL_SECRETS_ENCRYPTION_KEY", raising=False)

    with pytest.raises(secrets_service.SecretsEncryptionKeyMissingError):
        secrets_service.set_secret("tenant-a", "api_key", "sk-abc")
