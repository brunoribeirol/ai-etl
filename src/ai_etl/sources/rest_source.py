"""REST API source connector.

Sprint 11 (ADR-012) adds an optional `auth` argument on top of the original
no-auth, public-endpoint-only connector (e.g. the Open-Meteo call in
scenario 3). `auth` never carries a literal secret — it references an
*environment variable name* the secret is read from at call time (same
convention as `postgres_source.py`'s `POSTGRES_URL` lookup), so a
`pipeline_plan` — whether LLM-produced by `agents/orchestrator.py` or
supplied directly — never embeds credentials.
"""

import os
from typing import Any

import httpx
import pandas as pd

_SUPPORTED_AUTH_TYPES = {"api_key", "bearer", "basic"}


def _read_env(var_name: str) -> str:
    value = os.getenv(var_name)
    if not value:
        raise EnvironmentError(  # noqa: UP024 — matches postgres_source.py's EnvironmentError convention
            f"Environment variable '{var_name}' is not set (required for REST auth)."
        )
    return value


def _build_auth(
    auth: dict[str, Any] | None,
) -> tuple[dict[str, str], httpx.BasicAuth | None]:
    """Turn an `auth` config dict into httpx headers/auth kwargs.

    Supported shapes:
    - `{"type": "api_key", "header": "X-API-Key", "env_var": "NAME"}`
      (`header` defaults to `"X-API-Key"` if omitted)
    - `{"type": "bearer", "env_var": "NAME"}`
    - `{"type": "basic", "username_env_var": "NAME", "password_env_var": "NAME"}`
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
