"""REST API source connector.

Sprint 11 (ADR-012) adds an optional `auth` argument on top of the original
no-auth, public-endpoint-only connector (e.g. the Open-Meteo call in
scenario 3). `auth` never carries a literal secret — it references an
*environment variable name* the secret is read from at call time (same
convention as `postgres_source.py`'s `POSTGRES_URL` lookup), so a
`pipeline_plan` — whether LLM-produced by `agents/orchestrator.py` or
supplied directly — never embeds credentials.

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

    Supported shapes:
    - `{"type": "api_key", "header": "X-API-Key", "env_var": "NAME"}`
      (`header` defaults to `"X-API-Key"` if omitted)
    - `{"type": "bearer", "env_var": "NAME"}`
    - `{"type": "basic", "username_env_var": "NAME", "password_env_var": "NAME"}`
    - `{"type": "oauth2_client_credentials", "token_url": "...", \
"client_id_env_var": "NAME", "client_secret_env_var": "NAME", "scope": "..." (optional)}`
    """
    if not auth:
        return {}, None

    auth_type = auth.get("type")
    if auth_type == "api_key":
        header = auth.get("header", "X-API-Key")
        value = _read_env(auth["env_var"])
        return {header: value}, None
    if auth_type == "bearer":
        token = _read_env(auth["env_var"])
        return {"Authorization": f"Bearer {token}"}, None
    if auth_type == "basic":
        username = _read_env(auth["username_env_var"])
        password = _read_env(auth["password_env_var"])
        return {}, httpx.BasicAuth(username, password)
    if auth_type == "oauth2_client_credentials":
        client_id = _read_env(auth["client_id_env_var"])
        client_secret = _read_env(auth["client_secret_env_var"])
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
