"""Unit tests for ADR-039's backend-selection dispatch in
`execute_in_sandbox()` and the Vercel Sandbox backend's fail-closed behavior.

These do not require the real `vercel` Python package, a real Vercel
project, or network access (`tests/integration/test_sandbox_vercel.py`
covers the real Sandbox path when live credentials are available) — they
exercise: (1) that `execute_in_sandbox()`'s default stays "process" (2)
requesting `backend="vercel"` fails loudly, not silently downgrading to the
weaker "process" backend, when the SDK package or the required credential
env vars are missing.
"""

import pandas as pd
import pytest

from ai_etl.core.sandbox import execute_in_sandbox
from ai_etl.core.sandbox_vercel import (
    VercelSandboxUnavailableError,
    _missing_credential_env_vars,
)


def test_default_backend_is_process_when_unspecified() -> None:
    """No existing caller (Transformer/Analyst/Science) passes `backend` —
    confirms the default keeps using the multiprocessing "process" backend,
    not something that would require Vercel credentials in every deployment.
    """
    code = """
def transform(dfs):
    return dfs["orders"]
"""
    dfs = {"orders": pd.DataFrame({"a": [1, 2]})}
    result = execute_in_sandbox(code, dfs)

    assert result["error"] is None
    assert list(result["values"]["result"]["a"]) == [1, 2]


def test_env_var_backend_selection_falls_back_to_process_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicitly unset AI_ETL_SANDBOX_BACKEND resolves to "process" — the
    production-safe default until Railway's env vars opt into "vercel"."""
    monkeypatch.delenv("AI_ETL_SANDBOX_BACKEND", raising=False)
    code = "result = 1 + 1"
    result = execute_in_sandbox(code, {}, mode="script", result_vars=["result"])

    assert result["error"] is None
    assert result["values"]["result"] == 2


def test_vercel_backend_fails_closed_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`backend="vercel"` must raise, never silently execute via the weaker
    "process" backend, when VERCEL_TOKEN/VERCEL_TEAM_ID/VERCEL_PROJECT_ID
    aren't all set (this project runs on Railway, so OIDC never applies)."""
    monkeypatch.setattr("ai_etl.core.sandbox_vercel._VERCEL_SDK_AVAILABLE", True)
    monkeypatch.delenv("VERCEL_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL_TEAM_ID", raising=False)
    monkeypatch.delenv("VERCEL_PROJECT_ID", raising=False)

    with pytest.raises(VercelSandboxUnavailableError):
        execute_in_sandbox(
            "result = 1", {}, mode="script", result_vars=["result"], backend="vercel"
        )


def test_env_var_vercel_backend_fails_closed_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same fail-closed guarantee when selected via AI_ETL_SANDBOX_BACKEND
    instead of the explicit `backend` argument."""
    monkeypatch.setattr("ai_etl.core.sandbox_vercel._VERCEL_SDK_AVAILABLE", True)
    monkeypatch.setenv("AI_ETL_SANDBOX_BACKEND", "vercel")
    monkeypatch.delenv("VERCEL_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL_TEAM_ID", raising=False)
    monkeypatch.delenv("VERCEL_PROJECT_ID", raising=False)

    with pytest.raises(VercelSandboxUnavailableError):
        execute_in_sandbox("result = 1", {}, mode="script", result_vars=["result"])


def test_vercel_backend_fails_closed_when_sdk_package_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with all three credential env vars set, a missing `vercel`
    package must still fail closed rather than silently downgrading."""
    monkeypatch.setattr("ai_etl.core.sandbox_vercel._VERCEL_SDK_AVAILABLE", False)
    monkeypatch.setenv("VERCEL_TOKEN", "token")
    monkeypatch.setenv("VERCEL_TEAM_ID", "team")
    monkeypatch.setenv("VERCEL_PROJECT_ID", "project")

    with pytest.raises(VercelSandboxUnavailableError):
        execute_in_sandbox(
            "result = 1", {}, mode="script", result_vars=["result"], backend="vercel"
        )


def test_missing_credential_env_vars_reports_only_the_missing_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VERCEL_TOKEN", "token")
    monkeypatch.delenv("VERCEL_TEAM_ID", raising=False)
    monkeypatch.delenv("VERCEL_PROJECT_ID", raising=False)

    assert _missing_credential_env_vars() == ["VERCEL_TEAM_ID", "VERCEL_PROJECT_ID"]
