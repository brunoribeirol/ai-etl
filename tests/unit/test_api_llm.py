"""Tests for `/llm/test-connectivity` (Sprint 30, ADR-031).

Same convention as `test_api_secrets.py`: mocks at the import site inside
`api/routers/llm.py`, auth overridden via `dependency_overrides`.
"""

import pytest
from fastapi.testclient import TestClient

from ai_etl.api.deps import get_current_auth_context
from ai_etl.api.main import app


@pytest.fixture(autouse=True)
def _override_auth() -> None:
    app.dependency_overrides[get_current_auth_context] = lambda: {
        "tenant_id": "tenant-a",
        "role": "viewer",
    }
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_test_connectivity_success(client: TestClient, mocker) -> None:
    mock_probe = mocker.patch(
        "ai_etl.api.routers.llm.test_provider_connectivity",
        return_value={
            "ok": True,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "latency_ms": 123.4,
            "error": None,
        },
    )

    response = client.post(
        "/llm/test-connectivity", json={"provider": "openai", "model": "gpt-4o-mini"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["latency_ms"] == 123.4
    mock_probe.assert_called_once_with("openai", "gpt-4o-mini")


def test_test_connectivity_provider_failure_is_still_200(client: TestClient, mocker) -> None:
    """A failed connectivity check is a useful, expected result — not an API error."""
    mocker.patch(
        "ai_etl.api.routers.llm.test_provider_connectivity",
        return_value={
            "ok": False,
            "provider": "anthropic",
            "model": "claude-sonnet-5",
            "latency_ms": 5.0,
            "error": "AI_ETL_LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY to be set.",
        },
    )

    response = client.post(
        "/llm/test-connectivity",
        json={"provider": "anthropic", "model": "claude-sonnet-5"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "ANTHROPIC_API_KEY" in body["error"]


def test_test_connectivity_rejects_model_outside_allowlist(client: TestClient, mocker) -> None:
    mock_probe = mocker.patch("ai_etl.api.routers.llm.test_provider_connectivity")

    response = client.post(
        "/llm/test-connectivity", json={"provider": "openai", "model": "not-a-real-model"}
    )

    assert response.status_code == 400
    mock_probe.assert_not_called()


def test_test_connectivity_rejects_unsupported_provider(client: TestClient, mocker) -> None:
    mock_probe = mocker.patch("ai_etl.api.routers.llm.test_provider_connectivity")

    response = client.post(
        "/llm/test-connectivity", json={"provider": "not-a-provider", "model": "gpt-4o-mini"}
    )

    assert response.status_code == 400
    mock_probe.assert_not_called()
