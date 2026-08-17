"""Tests for `api/deps.py::get_current_tenant_id` (ADR-011).

Reuses the fake-JWKS-via-local-RSA-keypair pattern already established in
`tests/unit/test_auth_service.py` — no network call to Clerk, tokens signed
and verified entirely in-process.
"""

import time
from typing import Any

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


def _make_token(private_key: RSAPrivateKey, *, sub: str = "user_abc123") -> str:
    now = int(time.time())
    payload = {"sub": sub, "iss": _ISSUER, "iat": now, "exp": now + 3600}
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": _KID})


def test_missing_header_raises_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        deps.get_current_tenant_id(authorization=None)
    assert exc_info.value.status_code == 401


def test_malformed_header_raises_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        deps.get_current_tenant_id(authorization="Basic abc123")
    assert exc_info.value.status_code == 401


def test_invalid_token_raises_401(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HTTPException) as exc_info:
        deps.get_current_tenant_id(authorization="Bearer not-a-real-jwt")
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
    tenant_id = deps.get_current_tenant_id(authorization=f"Bearer {token}")

    assert tenant_id == "user_xyz789"
    mock_ensure_user.assert_called_once_with("user_xyz789")
