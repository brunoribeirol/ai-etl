"""Unit tests for ADR-038's backend-selection dispatch in
`execute_in_sandbox()` and the Docker backend's fail-closed behavior.

These do not require a real Docker daemon (`test_sandbox_docker.py`, an
integration test, covers the real container path) — they exercise:
(1) that `execute_in_sandbox()`'s signature/behavior is unchanged for every
existing caller (default backend stays "process"), and (2) that requesting
`backend="docker"` without Docker available fails loudly rather than
silently downgrading to the weaker "process" backend.
"""

import pandas as pd
import pytest

from ai_etl.core.sandbox import execute_in_sandbox
from ai_etl.core.sandbox_docker import DockerSandboxUnavailableError


def test_default_backend_is_process_when_unspecified() -> None:
    """No existing caller (Transformer/Analyst/Science) passes `backend` —
    confirms the default keeps using the multiprocessing "process" backend,
    not something that would require Docker in every deployment."""
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
    production-safe default this project's Railway deployment relies on."""
    monkeypatch.delenv("AI_ETL_SANDBOX_BACKEND", raising=False)
    code = "result = 1 + 1"
    result = execute_in_sandbox(code, {}, mode="script", result_vars=["result"])

    assert result["error"] is None
    assert result["values"]["result"] == 2


def test_docker_backend_fails_closed_when_docker_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`backend="docker"` must raise, never silently execute via the weaker
    "process" backend, when Docker isn't reachable."""
    monkeypatch.setattr("ai_etl.core.sandbox_docker._docker_available", lambda: False)

    with pytest.raises(DockerSandboxUnavailableError):
        execute_in_sandbox(
            "result = 1", {}, mode="script", result_vars=["result"], backend="docker"
        )


def test_env_var_docker_backend_fails_closed_when_docker_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same fail-closed guarantee when selected via AI_ETL_SANDBOX_BACKEND
    instead of the explicit `backend` argument."""
    monkeypatch.setenv("AI_ETL_SANDBOX_BACKEND", "docker")
    monkeypatch.setattr("ai_etl.core.sandbox_docker._docker_available", lambda: False)

    with pytest.raises(DockerSandboxUnavailableError):
        execute_in_sandbox("result = 1", {}, mode="script", result_vars=["result"])
