"""REST API source connector.

Sprint 11 (ADR-012) adds an optional `auth` argument on top of the original
no-auth, public-endpoint-only connector (e.g. the Open-Meteo call in
scenario 3). `auth` never carries a literal secret — each field references
either an *environment variable name* the secret is read from at call time
(same convention as `postgres_source.py`'s `POSTGRES_URL` lookup), or —
since ADR-045 — a tenant's own stored secret *name*, resolved lazily via
`core/tenant_context.py::get_rest_secret` at call time and tried first. So a
`pipeline_plan` — whether LLM-produced by `agents/pipeline/orchestrator.py` or
supplied directly — never embeds credentials either way.

`oauth2_client_credentials` (added in this same sprint, on top of the
original `api_key`/`bearer`/`basic`) fetches a real access token from a
token endpoint using the client-credentials grant and caches it in-process
until shortly before it expires, re-fetching only then — the caching lives
at module scope (`_TOKEN_CACHE`) so repeated `load_rest` calls within one
process (e.g. paginated pulls, or multiple sources sharing one OAuth2 app)
don't hit the token endpoint on every call.
"""

import os
import time
from typing import Any

import httpx
import pandas as pd

from ai_etl.core.tenant_context import get_rest_secret

_SUPPORTED_AUTH_TYPES = {"api_key", "bearer", "basic", "oauth2_client_credentials"}

# Cache of fetched OAuth2 access tokens, keyed by (token_url, client_id) so
# distinct REST sources using different client credentials against the same
# token endpoint don't collide. Value is (access_token, expires_at) where
# expires_at is a `time.monotonic()` timestamp — monotonic rather than
# wall-clock so a system clock adjustment can't make a cached token look
# valid (or expired) incorrectly.
_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}

# Re-fetch a token this many seconds before its reported expiry, so a
# request that starts just before expiry doesn't race a token that goes
# stale mid-flight.
_TOKEN_EXPIRY_LEEWAY_SECONDS = 30
_DEFAULT_TOKEN_TTL_SECONDS = 3600


def _read_env(var_name: str) -> str:
    value = os.getenv(var_name)
    if not value:
        raise EnvironmentError(  # noqa: UP024 — matches postgres_source.py's EnvironmentError convention
            f"Environment variable '{var_name}' is not set (required for REST auth)."
        )
    return value


def _read_secret(auth: dict[str, Any], env_key: str, ref_key: str) -> str:
    """Resolve one auth field: `auth[ref_key]` (ADR-045, a tenant's own
    stored secret, looked up by name at call time) if present, else
    `auth[env_key]` (the original, still-default shared-env-var behavior).

    A `ref_key` that doesn't resolve for the current tenant (no active
    tenant context, or no secret saved under that name) falls back to
    `env_key` if the plan also set one, same "the tenant just hasn't
    configured this yet" posture `core/tenant_context.py`'s DB overrides
    already have — but raises a clear error rather than a `KeyError` if
    there's no `env_key` to fall back to at all, so a plan that deliberately
    only sets `ref_key` fails with an actionable message, not a stack trace.
    A plan with no `ref_key` at all preserves the exact original behavior:
    `auth[env_key]` must exist.
    """
    secret_ref = auth.get(ref_key)
    if secret_ref is not None:
        value = get_rest_secret(secret_ref)
        if value is not None:
            return value
        if env_key not in auth:
            raise EnvironmentError(  # noqa: UP024
                f"REST auth secret_ref {secret_ref!r} ({ref_key}) has no value for this "
                f"tenant, and no {env_key!r} fallback was configured."
            )
    return _read_env(auth[env_key])


def _fetch_oauth2_token(
    token_url: str, client_id: str, client_secret: str, scope: str | None
) -> str:
    """Fetch (or return a cached) OAuth2 client-credentials access token.

    POSTs `grant_type=client_credentials` to `token_url` — the standard
    OAuth2 client-credentials grant (RFC 6749 §4.4) — and caches the
    resulting `access_token` until `expires_in` (default 3600s if the
    token endpoint omits it) minus `_TOKEN_EXPIRY_LEEWAY_SECONDS`.
    """
    cache_key = (token_url, client_id)
    cached = _TOKEN_CACHE.get(cache_key)
    if cached is not None:
        token, expires_at = cached
        if time.monotonic() < expires_at:
            return token

    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if scope:
        data["scope"] = scope
    response = httpx.post(token_url, data=data, timeout=30)
    response.raise_for_status()
    payload = response.json()
    token = str(payload["access_token"])
    expires_in = int(payload.get("expires_in", _DEFAULT_TOKEN_TTL_SECONDS))
    expires_at = time.monotonic() + max(expires_in - _TOKEN_EXPIRY_LEEWAY_SECONDS, 0)
    _TOKEN_CACHE[cache_key] = (token, expires_at)
    return token


def _build_auth(
    auth: dict[str, Any] | None,
) -> tuple[dict[str, str], httpx.BasicAuth | None]:
    """Turn an `auth` config dict into httpx headers/auth kwargs.

    Supported shapes (each `*_env_var` field has an ADR-045 `*_secret_ref`
    counterpart — a tenant's own stored secret, resolved by name at call
    time, tried first and falling back to the env var):
    - `{"type": "api_key", "header": "X-API-Key", "env_var": "NAME"}`
      (`header` defaults to `"X-API-Key"` if omitted; secret ref: `secret_ref`)
    - `{"type": "bearer", "env_var": "NAME"}` (secret ref: `secret_ref`)
    - `{"type": "basic", "username_env_var": "NAME", "password_env_var": "NAME"}`
      (secret refs: `username_secret_ref`, `password_secret_ref`)
    - `{"type": "oauth2_client_credentials", "token_url": "...", \
"client_id_env_var": "NAME", "client_secret_env_var": "NAME", "scope": "..." (optional)}`
      (secret refs: `client_id_secret_ref`, `client_secret_ref`)
    """
    if not auth:
        return {}, None

    auth_type = auth.get("type")
    if auth_type == "api_key":
        header = auth.get("header", "X-API-Key")
        value = _read_secret(auth, "env_var", "secret_ref")
        return {header: value}, None
    if auth_type == "bearer":
        token = _read_secret(auth, "env_var", "secret_ref")
        return {"Authorization": f"Bearer {token}"}, None
    if auth_type == "basic":
        username = _read_secret(auth, "username_env_var", "username_secret_ref")
        password = _read_secret(auth, "password_env_var", "password_secret_ref")
        return {}, httpx.BasicAuth(username, password)
    if auth_type == "oauth2_client_credentials":
        client_id = _read_secret(auth, "client_id_env_var", "client_id_secret_ref")
        client_secret = _read_secret(auth, "client_secret_env_var", "client_secret_ref")
        token = _fetch_oauth2_token(auth["token_url"], client_id, client_secret, auth.get("scope"))
        return {"Authorization": f"Bearer {token}"}, None

    raise ValueError(
        f"Unsupported REST auth type: {auth_type!r}. Supported: {sorted(_SUPPORTED_AUTH_TYPES)}"
    )


def load_rest(
    url: str,
    params: dict[str, Any] | None = None,
    auth: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Fetch JSON from a REST endpoint and normalize into a DataFrame.

    `auth` is optional and backward compatible — omitting it (or passing
    `None`) preserves the original public-endpoint behavior.
    """
    headers, httpx_auth = _build_auth(auth)
    response = httpx.get(url, params=params or {}, headers=headers, auth=httpx_auth, timeout=30)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, list):
        return pd.json_normalize(data)
    if isinstance(data, dict):
        for key in ("data", "results", "items", "records"):
            if key in data and isinstance(data[key], list):
                return pd.json_normalize(data[key])
        # Handle time-series dicts where a sub-key maps to parallel lists
        # (e.g. Open-Meteo: {"daily": {"time": [...], "temperature_2m_max": [...]}})
        for val in data.values():
            if isinstance(val, dict) and val and all(isinstance(v, list) for v in val.values()):
                lengths = {len(v) for v in val.values()}
                if len(lengths) == 1:
                    return pd.DataFrame(val)
        return pd.json_normalize([data])
    raise ValueError(f"Unexpected JSON structure from {url}: {type(data)}")
