"""Tests for `ai_etl.services.auth_service` — Clerk JWT/JWKS verification (ADR-006).

No network calls, no real Clerk account: `PyJWKClient` is replaced with a fake
that resolves any `kid` to a locally generated RSA keypair, so tokens are
minted and verified entirely in-process. This mirrors `tests/unit/test_pipeline_service.py`'s
use of `monkeypatch` to replace collaborators at the module boundary rather than
patching internals of third-party libraries.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

from ai_etl.services import auth_service

_KID = "test-kid-1"
_JWKS_URL = "https://example.invalid/.well-known/jwks.json"
_ISSUER = "https://example.invalid"


@pytest.fixture()
def rsa_keypair() -> tuple[RSAPrivateKey, RSAPublicKey]:
    """A fresh RSA keypair, used to sign and verify tokens with no network call."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(autouse=True)
def _reset_jwks_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """`auth_service` caches its JWKS client at module scope (by design, so it
    isn't re-created/re-fetched per call in production). Reset that cache
    before every test and point `CLERK_JWKS_URL`/`CLERK_ISSUER` at harmless
    placeholders so `_get_jwks_client()`/the issuer check don't short-circuit
    with "not configured" in tests that need to exercise the (mocked) JWKS
    lookup and issuer validation themselves."""
    monkeypatch.setattr(auth_service, "_jwks_client", None)
    monkeypatch.setattr(auth_service, "_jwks_client_url", None)
    monkeypatch.setenv("CLERK_JWKS_URL", _JWKS_URL)
    monkeypatch.setenv("CLERK_ISSUER", _ISSUER)


class _FakeSigningKey:
    """Stand-in for `jwt.PyJWK` — `auth_service` only ever reads `.key`."""

    def __init__(self, key: Any) -> None:
        self.key = key


def _mock_jwks_client(monkeypatch: pytest.MonkeyPatch, public_key: RSAPublicKey) -> None:
    """Replace `PyJWKClient` with a fake that resolves any `kid` to `public_key`
    with no HTTP call — the local-keypair approach this module's tests use
    instead of hitting Clerk's real JWKS endpoint."""

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
    exp_offset_seconds: int = 3600,
    kid: str = _KID,
    iss: str | None = _ISSUER,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {"sub": sub, "exp": now + exp_offset_seconds, "iat": now}
    if iss is not None:
        payload["iss"] = iss
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


# ---------------------------------------------------------------------------
# No token
# ---------------------------------------------------------------------------


def test_rejects_empty_string_token() -> None:
    result = auth_service.verify_session_token("")

    assert result["ok"] is False
    assert result["user_id"] is None
    assert result["error"]


def test_rejects_none_token() -> None:
    result = auth_service.verify_session_token(None)  # type: ignore[arg-type]

    assert result["ok"] is False
    assert result["user_id"] is None
    assert result["error"]


# ---------------------------------------------------------------------------
# Malformed / non-JWT token
# ---------------------------------------------------------------------------


def test_rejects_malformed_non_jwt_string() -> None:
    result = auth_service.verify_session_token("not-a-jwt-at-all")

    assert result["ok"] is False
    assert result["user_id"] is None
    assert result["error"]


# ---------------------------------------------------------------------------
# Expired token
# ---------------------------------------------------------------------------


def test_rejects_expired_token(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> None:
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    token = _make_token(private_key, exp_offset_seconds=-3600)

    result = auth_service.verify_session_token(token)

    assert result["ok"] is False
    assert result["user_id"] is None
    assert result["error"]


# ---------------------------------------------------------------------------
# Valid token
# ---------------------------------------------------------------------------


def test_accepts_valid_well_formed_token(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> None:
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    token = _make_token(private_key, sub="user_xyz789", exp_offset_seconds=3600)

    result = auth_service.verify_session_token(token)

    assert result["ok"] is True
    assert result["user_id"] == "user_xyz789"
    assert result["error"] is None


# ---------------------------------------------------------------------------
# JWKS endpoint unreachable — fails closed
# ---------------------------------------------------------------------------


def test_fails_closed_when_jwks_endpoint_unreachable(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> None:
    private_key, _public_key = rsa_keypair

    class _UnreachableJWKSClient:
        def __init__(self, url: str) -> None:
            self.url = url

        def get_signing_key_from_jwt(self, token: str) -> Any:
            raise OSError("Connection refused")

    monkeypatch.setattr(auth_service, "PyJWKClient", _UnreachableJWKSClient)
    token = _make_token(private_key)

    result = auth_service.verify_session_token(token)  # must not raise

    assert result["ok"] is False
    assert result["user_id"] is None
    assert result["error"]


def test_fails_closed_when_jwks_url_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Companion edge case: no `CLERK_JWKS_URL` at all also fails closed
    instead of raising or defaulting to some other verification path."""
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)

    result = auth_service.verify_session_token("anything")

    assert result["ok"] is False
    assert result["user_id"] is None
    assert result["error"]


# ---------------------------------------------------------------------------
# Issuer (`iss`) validation — rejects tokens from a different Clerk instance
# ---------------------------------------------------------------------------


def test_rejects_token_with_wrong_issuer(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> None:
    """A JWT that is otherwise perfectly valid (correct signature, not expired,
    has `sub`) but was issued by a different Clerk instance/application than
    the one configured via `CLERK_ISSUER` must still be rejected — this is
    the whole point of `iss` validation, not just a well-formedness check."""
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    token = _make_token(private_key, iss="https://a-different-clerk-instance.invalid")

    result = auth_service.verify_session_token(token)

    assert result["ok"] is False
    assert result["user_id"] is None
    assert result["error"]


def test_rejects_token_missing_issuer_claim(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> None:
    """`iss` is in the `require` list alongside `exp`/`sub` — a token that
    omits it entirely must fail closed, not be treated as "no claim to check
    against, so let it through"."""
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    token = _make_token(private_key, iss=None)

    result = auth_service.verify_session_token(token)

    assert result["ok"] is False
    assert result["user_id"] is None
    assert result["error"]


def test_fails_closed_when_issuer_not_configured(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> None:
    """Companion edge case to `test_fails_closed_when_jwks_url_not_configured`:
    no `CLERK_ISSUER` at all also fails closed instead of silently skipping
    issuer validation."""
    private_key, public_key = rsa_keypair
    _mock_jwks_client(monkeypatch, public_key)
    monkeypatch.delenv("CLERK_ISSUER", raising=False)
    token = _make_token(private_key)

    result = auth_service.verify_session_token(token)

    assert result["ok"] is False
    assert result["user_id"] is None
    assert result["error"]
