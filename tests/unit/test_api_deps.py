"""Tests for `api/deps.py` — auth (ADR-011) and RBAC (ADR-022).

Reuses the fake-JWKS-via-local-RSA-keypair pattern already established in
`tests/unit/test_auth_service.py` — no network call to Clerk, tokens signed
and verified entirely in-process.
"""

import time
from typing import Any, Optional

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from fastapi import HTTPException

from ai_etl.api import deps
from ai_etl.services import auth_service

_KID = "test-kid-1"
_JWKS_URL = "https://example.invalid/.well-known/jwks.json"
_ISSUER = "https://example.invalid"


@pytest.fixture()
def rsa_keypair() -> tuple[RSAPrivateKey, RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(autouse=True)
def _reset_jwks_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_service, "_jwks_client", None)
    monkeypatch.setattr(auth_service, "_jwks_client_url", None)
    monkeypatch.setenv("CLERK_JWKS_URL", _JWKS_URL)
    monkeypatch.setenv("CLERK_ISSUER", _ISSUER)


class _FakeSigningKey:
    def __init__(self, key: Any) -> None:
        self.key = key


def _mock_jwks_client(monkeypatch: pytest.MonkeyPatch, public_key: RSAPublicKey) -> None:
    class _FakeJWKSClient:
        def __init__(self, url: str) -> None:
            self.url = url

        def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
            return _FakeSigningKey(public_key)

    monkeypatch.setattr(auth_service, "PyJWKClient", _FakeJWKSClient)


def _make_token(
    private_key: RSAPrivateKey,
    *,
    sub: str = "user_abc123",
    org_id: Optional[str] = None,
    org_role: Optional[str] = None,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {"sub": sub, "iss": _ISSUER, "iat": now, "exp": now + 3600}
    if org_id is not None:
        payload["o"] = {"id": org_id, "rol": org_role}
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": _KID})


def test_missing_header_raises_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        deps.get_current_auth_context(authorization=None)
    assert exc_info.value.status_code == 401


def test_malformed_header_raises_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        deps.get_current_auth_context(authorization="Basic abc123")
    assert exc_info.value.status_code == 401


def test_invalid_token_raises_401(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HTTPException) as exc_info:
        deps.get_current_auth_context(authorization="Bearer not-a-real-jwt")
    assert exc_info.value.status_code == 401


def test_valid_token_returns_tenant_id_and_ensures_user(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey],
    mocker,
) -> None:
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    mock_ensure_user = mocker.patch("ai_etl.api.deps.ensure_user")

    token = _make_token(private_key, sub="user_xyz789")
    auth = deps.get_current_auth_context(authorization=f"Bearer {token}")

    assert auth["tenant_id"] == "user_xyz789"
    assert auth["role"] == "editor"  # solo tenant, no org -> full access
    mock_ensure_user.assert_called_once_with("user_xyz789")

    # get_current_tenant_id is a thin wrapper — still returns just the id.
    assert deps.get_current_tenant_id(auth) == "user_xyz789"


def test_org_admin_resolves_to_editor(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey],
    mocker,
) -> None:
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    mocker.patch("ai_etl.api.deps.ensure_user")

    token = _make_token(private_key, sub="user_1", org_id="org_acme", org_role="org:admin")
    auth = deps.get_current_auth_context(authorization=f"Bearer {token}")

    assert auth["tenant_id"] == "org_acme"
    assert auth["role"] == "editor"


def test_org_member_resolves_to_viewer(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey],
    mocker,
) -> None:
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    mocker.patch("ai_etl.api.deps.ensure_user")

    token = _make_token(private_key, sub="user_2", org_id="org_acme", org_role="org:member")
    auth = deps.get_current_auth_context(authorization=f"Bearer {token}")

    assert auth["tenant_id"] == "org_acme"
    assert auth["role"] == "viewer"


def test_two_users_same_org_different_roles_share_tenant_but_differ_in_access(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey],
    mocker,
) -> None:
    """RBAC's own DoD, as a test: two Clerk accounts, same organization,
    different roles -> same tenant_id, different resolved role."""
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    mocker.patch("ai_etl.api.deps.ensure_user")

    admin_token = _make_token(private_key, sub="user_admin", org_id="org_shared", org_role="admin")
    member_token = _make_token(
        private_key, sub="user_member", org_id="org_shared", org_role="basic_member"
    )

    admin_auth = deps.get_current_auth_context(authorization=f"Bearer {admin_token}")
    member_auth = deps.get_current_auth_context(authorization=f"Bearer {member_token}")

    assert admin_auth["tenant_id"] == member_auth["tenant_id"] == "org_shared"
    assert admin_auth["role"] == "editor"
    assert member_auth["role"] == "viewer"

    editor_dep = deps.require_role("editor")
    assert editor_dep(admin_auth) == "org_shared"
    with pytest.raises(HTTPException) as exc_info:
        editor_dep(member_auth)
    assert exc_info.value.status_code == 403


def test_require_role_viewer_allows_both_roles(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey],
    mocker,
) -> None:
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    mocker.patch("ai_etl.api.deps.ensure_user")

    token = _make_token(private_key, sub="user_3", org_id="org_x", org_role="org:member")
    auth = deps.get_current_auth_context(authorization=f"Bearer {token}")

    viewer_dep = deps.require_role("viewer")
    assert viewer_dep(auth) == "org_x"


# --- Sprint 31 (ADR-032) — platform `admin` role ---------------------------


def test_platform_admin_user_id_resolves_to_admin_regardless_of_org(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey],
    mocker,
) -> None:
    monkeypatch.setenv("AI_ETL_PLATFORM_ADMINS", "user_platform_admin")
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    mocker.patch("ai_etl.api.deps.ensure_user")

    # No org at all.
    token = _make_token(private_key, sub="user_platform_admin")
    auth = deps.get_current_auth_context(authorization=f"Bearer {token}")
    assert auth["role"] == "admin"
    assert auth["user_id"] == "user_platform_admin"

    # Also admin even inside an org where their own org role is a plain member.
    token_in_org = _make_token(
        private_key, sub="user_platform_admin", org_id="org_acme", org_role="org:member"
    )
    auth_in_org = deps.get_current_auth_context(authorization=f"Bearer {token_in_org}")
    assert auth_in_org["role"] == "admin"


def test_non_platform_admin_org_admin_stays_editor_not_admin(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey],
    mocker,
) -> None:
    """A Clerk org's own admin role must never grant the platform `admin`
    role — that would let any tenant self-promote to cross-tenant access."""
    monkeypatch.setenv("AI_ETL_PLATFORM_ADMINS", "someone_else")
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    mocker.patch("ai_etl.api.deps.ensure_user")

    token = _make_token(private_key, sub="user_org_admin", org_id="org_acme", org_role="org:admin")
    auth = deps.get_current_auth_context(authorization=f"Bearer {token}")
    assert auth["role"] == "editor"


def test_platform_admins_unset_means_admin_role_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey],
    mocker,
) -> None:
    monkeypatch.delenv("AI_ETL_PLATFORM_ADMINS", raising=False)
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    mocker.patch("ai_etl.api.deps.ensure_user")

    token = _make_token(private_key, sub="user_anyone")
    auth = deps.get_current_auth_context(authorization=f"Bearer {token}")
    assert auth["role"] != "admin"


def test_require_admin_allows_platform_admin(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey],
    mocker,
) -> None:
    monkeypatch.setenv("AI_ETL_PLATFORM_ADMINS", "user_platform_admin")
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    mocker.patch("ai_etl.api.deps.ensure_user")

    token = _make_token(private_key, sub="user_platform_admin")
    auth = deps.get_current_auth_context(authorization=f"Bearer {token}")

    result = deps.require_admin(auth)
    assert result["role"] == "admin"
    assert result["user_id"] == "user_platform_admin"


def test_require_admin_rejects_editor(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey],
    mocker,
) -> None:
    monkeypatch.delenv("AI_ETL_PLATFORM_ADMINS", raising=False)
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    mocker.patch("ai_etl.api.deps.ensure_user")

    token = _make_token(private_key, sub="user_regular")
    auth = deps.get_current_auth_context(authorization=f"Bearer {token}")
    assert auth["role"] == "editor"  # solo tenant, no org

    with pytest.raises(HTTPException) as exc_info:
        deps.require_admin(auth)
    assert exc_info.value.status_code == 403
