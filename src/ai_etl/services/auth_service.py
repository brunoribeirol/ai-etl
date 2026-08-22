"""Clerk session-token verification — local JWT/JWKS check, no Streamlit import.

Per ADR-006, Clerk JWTs are verified in-process against Clerk's published JWKS
endpoint (fetch + cache the signing keys) rather than calling Clerk's backend API
on every request. This keeps Clerk's API latency/availability off the critical
path of every pipeline run, mirroring the pure-function-returning-results style
already used by `pipeline_service.py` — this module never raises into the UI
layer for expected failure modes (expired token, bad signature, unreachable
JWKS); it always returns an `AuthResult` and fails closed (denies access) on
any verification error.

Env vars (read via `os.environ`/`os.getenv`, same pattern as `OPENAI_API_KEY`
in `app.py` — never hardcoded): `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`,
`CLERK_JWKS_URL`, `CLERK_ISSUER`. `CLERK_JWKS_URL` and `CLERK_ISSUER` are
both required by this module (the latter for `iss` claim validation, so a
token signed by a key that happens to resolve via the configured JWKS URL
can't be accepted unless it was also issued by this exact Clerk instance);
the other two vars are read by other parts of the Clerk integration
(client-side sign-in) and are not required here, but are documented in
ADR-006 as the full env var set for the feature.
"""

from __future__ import annotations

import os
from typing import Optional, TypedDict

import jwt
from jwt import PyJWKClient

# Clerk issues RS256-signed JWTs (asymmetric, verified against the published
# JWKS public keys) — never accept HS256/"none" here, that would let a caller
# forge a token using a key this service doesn't control.
_ALGORITHMS = ["RS256"]

# Module-level cache: the JWKS client fetches and caches Clerk's public keys
# internally (PyJWKClient re-fetches only on a cache-miss/unknown `kid`), so it
# must not be re-created per call — that would defeat the caching and add a
# network round-trip to every verification.
_jwks_client: Optional[PyJWKClient] = None
_jwks_client_url: Optional[str] = None


class AuthResult(TypedDict):
    ok: bool
    user_id: Optional[str]  # Clerk user id (JWT `sub` claim) -> becomes tenant_id
    error: Optional[str]
    # Sprint 19 (ADR-022) — Clerk Organizations. Present only when the
    # session has an *active* organization; `None` for a personal session
    # (every account that predates this sprint, and any account that never
    # joins an org). `org_role` is the raw Clerk role claim, unmapped —
    # `api/deps.py` maps it to this app's editor/viewer roles.
    org_id: Optional[str]
    org_role: Optional[str]


def _extract_org_claims(payload: dict[str, object]) -> tuple[Optional[str], Optional[str]]:
    """Pull `(org_id, org_role)` out of a decoded Clerk JWT payload.

    Clerk session token v2 nests organization claims under `"o"`
    (`o.id`/`o.rol`) to keep tokens small; legacy v1 templates use flat
    `org_id`/`org_role` claims instead. Both shapes are handled — a token
    with neither (a personal session, or a JWT template that omits
    organization claims entirely) yields `(None, None)`, the same as today.
    """
    org_claim = payload.get("o")
    if isinstance(org_claim, dict):
        org_id = org_claim.get("id")
        org_role = org_claim.get("rol")
        if isinstance(org_id, str):
            return org_id, org_role if isinstance(org_role, str) else None

    org_id = payload.get("org_id")
    org_role = payload.get("org_role")
    if isinstance(org_id, str):
        return org_id, org_role if isinstance(org_role, str) else None

    return None, None


def _get_jwks_client() -> Optional[PyJWKClient]:
    """Return the cached `PyJWKClient`, (re)creating it if the configured JWKS
    URL is missing or has changed since the last call.

    Returns `None` (rather than raising) if `CLERK_JWKS_URL` is not configured,
    so `verify_session_token` can fail closed with a clear error instead of
    crashing the app on a missing env var.
    """
    global _jwks_client, _jwks_client_url

    jwks_url = os.getenv("CLERK_JWKS_URL")
    if not jwks_url:
        return None

    if _jwks_client is None or _jwks_client_url != jwks_url:
        _jwks_client = PyJWKClient(jwks_url)
        _jwks_client_url = jwks_url

    return _jwks_client


def verify_session_token(token: str) -> AuthResult:
    """Verify a Clerk session JWT against the cached JWKS and return the outcome.

    Never raises for expected failure modes (missing/empty token, expired
    token, bad signature, malformed token, unreachable JWKS) — always returns
    `AuthResult(ok=False, error=...)` so callers (`app.py`) decide how to
    present the failure, matching how `pipeline_service.py` functions return
    error strings/fields instead of raising into the UI.

    Fails closed: any verification error (including infrastructure errors like
    an unreachable JWKS endpoint) results in `ok=False`, never `ok=True`.
    """
    if not token or not token.strip():
        return AuthResult(ok=False, user_id=None, error="Token vazio.", org_id=None, org_role=None)

    jwks_client = _get_jwks_client()
    if jwks_client is None:
        return AuthResult(
            ok=False,
            user_id=None,
            error="CLERK_JWKS_URL não configurada.",
            org_id=None,
            org_role=None,
        )

    # Fail closed the same way as a missing CLERK_JWKS_URL: without an expected
    # issuer to compare against, `iss` can't be validated at all, and a JWT
    # signed by any key the JWKS endpoint happens to serve would otherwise be
    # accepted regardless of which Clerk instance/application issued it.
    issuer = os.getenv("CLERK_ISSUER")
    if not issuer:
        return AuthResult(
            ok=False,
            user_id=None,
            error="CLERK_ISSUER não configurada.",
            org_id=None,
            org_role=None,
        )

    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=_ALGORITHMS,
            issuer=issuer,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        return AuthResult(
            ok=False, user_id=None, error=f"Token inválido: {exc}", org_id=None, org_role=None
        )
    except Exception as exc:  # JWKS endpoint unreachable, network errors, etc.
        return AuthResult(
            ok=False,
            user_id=None,
            error=f"Falha ao verificar token: {exc}",
            org_id=None,
            org_role=None,
        )

    user_id = payload.get("sub")
    if not user_id:
        return AuthResult(
            ok=False, user_id=None, error="Token sem claim 'sub'.", org_id=None, org_role=None
        )

    org_id, org_role = _extract_org_claims(payload)
    return AuthResult(ok=True, user_id=str(user_id), error=None, org_id=org_id, org_role=org_role)
