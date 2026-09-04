"""Unit tests for `core/tenant_context.py` (ADR-044).

Covers the three properties the module exists to guarantee:
1. With no active `tenant_connections` block, every connector falls back to
   `None` (its shared env var) — zero behavior change for every existing
   deployment/tenant that never configured a DB secret.
2. Inside a `tenant_connections` block, `get_connection_override` returns
   exactly what was set, and only for the source types actually provided.
3. `resolve_tenant_overrides` only returns secrets that actually exist for
   that tenant (`SecretNotFoundError` is swallowed, not raised), and returns
   `{}` outright for `tenant_id=None` without ever calling `get_secret`.
"""

from __future__ import annotations

from ai_etl.core.tenant_context import (
    MONGODB_SECRET_NAME,
    MYSQL_SECRET_NAME,
    POSTGRES_SECRET_NAME,
    get_connection_override,
    get_rest_secret,
    resolve_tenant_overrides,
    tenant_connections,
)
from ai_etl.services.secrets_service import SecretNotFoundError


def test_no_active_context_returns_none_for_every_source_type() -> None:
    assert get_connection_override("postgres") is None
    assert get_connection_override("mysql") is None
    assert get_connection_override("mongodb") is None


def test_get_rest_secret_returns_none_with_no_active_tenant(mocker) -> None:
    get_secret_mock = mocker.patch("ai_etl.core.tenant_context.get_secret")
    assert get_rest_secret("shop_api_key") is None
    get_secret_mock.assert_not_called()


def test_get_rest_secret_resolves_for_the_active_tenant(mocker) -> None:
    mocker.patch("ai_etl.core.tenant_context.get_secret", return_value="tenant-secret-value")

    with tenant_connections({}, tenant_id="tenant-a"):
        assert get_rest_secret("shop_api_key") == "tenant-secret-value"

    # Restored after the block — no leakage into an unscoped call.
    assert get_rest_secret("shop_api_key") is None


def test_get_rest_secret_returns_none_when_tenant_never_saved_it(mocker) -> None:
    mocker.patch(
        "ai_etl.core.tenant_context.get_secret", side_effect=SecretNotFoundError("shop_api_key")
    )

    with tenant_connections({}, tenant_id="tenant-a"):
        assert get_rest_secret("shop_api_key") is None


def test_get_rest_secret_returns_none_and_does_not_raise_on_lookup_failure(mocker) -> None:
    mocker.patch("ai_etl.core.tenant_context.get_secret", side_effect=RuntimeError("db down"))

    with tenant_connections({}, tenant_id="tenant-a"):
        assert get_rest_secret("shop_api_key") is None


def test_tenant_connections_makes_overrides_visible_inside_the_block() -> None:
    with tenant_connections({"postgres": "postgresql://tenant/db"}):
        assert get_connection_override("postgres") == "postgresql://tenant/db"
        # Not configured for this tenant -> still falls back to None, not KeyError.
        assert get_connection_override("mysql") is None


def test_tenant_connections_restores_previous_value_on_exit() -> None:
    with tenant_connections({"postgres": "postgresql://tenant/db"}):
        pass
    assert get_connection_override("postgres") is None


def test_tenant_connections_restores_previous_value_even_on_exception() -> None:
    class _BoomError(Exception):
        pass

    try:
        with tenant_connections({"postgres": "postgresql://tenant/db"}):
            raise _BoomError
    except _BoomError:
        pass
    assert get_connection_override("postgres") is None


def test_nested_tenant_connections_restores_outer_scope() -> None:
    with tenant_connections({"postgres": "postgresql://outer/db"}):
        with tenant_connections({"postgres": "postgresql://inner/db"}):
            assert get_connection_override("postgres") == "postgresql://inner/db"
        assert get_connection_override("postgres") == "postgresql://outer/db"
    assert get_connection_override("postgres") is None


def test_resolve_tenant_overrides_returns_empty_for_no_tenant_id(mocker) -> None:
    get_secret_mock = mocker.patch("ai_etl.core.tenant_context.get_secret")
    assert resolve_tenant_overrides(None) == {}
    get_secret_mock.assert_not_called()


def test_resolve_tenant_overrides_collects_only_configured_secrets(mocker) -> None:
    def fake_get_secret(tenant_id: str, name: str) -> str:
        if name == POSTGRES_SECRET_NAME:
            return "postgresql://tenant/db"
        raise SecretNotFoundError(name)

    mocker.patch("ai_etl.core.tenant_context.get_secret", side_effect=fake_get_secret)

    overrides = resolve_tenant_overrides("tenant-a")

    assert overrides == {"postgres": "postgresql://tenant/db"}


def test_resolve_tenant_overrides_can_return_all_three_source_types(mocker) -> None:
    values = {
        POSTGRES_SECRET_NAME: "postgresql://tenant/db",
        MYSQL_SECRET_NAME: "mysql+pymysql://tenant/db",
        MONGODB_SECRET_NAME: "mongodb://tenant/db",
    }
    mocker.patch(
        "ai_etl.core.tenant_context.get_secret",
        side_effect=lambda tenant_id, name: values[name],
    )

    overrides = resolve_tenant_overrides("tenant-a")

    assert overrides == {
        "postgres": "postgresql://tenant/db",
        "mysql": "mysql+pymysql://tenant/db",
        "mongodb": "mongodb://tenant/db",
    }


def test_resolve_tenant_overrides_returns_empty_when_tenant_configured_nothing(
    mocker,
) -> None:
    mocker.patch(
        "ai_etl.core.tenant_context.get_secret",
        side_effect=SecretNotFoundError("missing"),
    )
    assert resolve_tenant_overrides("tenant-a") == {}
