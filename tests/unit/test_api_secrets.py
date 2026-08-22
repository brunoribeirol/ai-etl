"""Tests for `/secrets` endpoints (Sprint 19, ADR-021).

Same convention as `test_api_pipelines.py`: mocks at the import site inside
`api/routers/secrets.py`, auth overridden via `dependency_overrides`.
"""

import pytest
from fastapi.testclient import TestClient

from ai_etl.api.deps import get_current_auth_context
from ai_etl.api.main import app
from ai_etl.services.secrets_service import SecretsEncryptionKeyMissingError


@pytest.fixture(autouse=True)
def _override_auth() -> None:
    app.dependency_overrides[get_current_auth_context] = lambda: {
        "tenant_id": "tenant-a",
        "role": "editor",
    }
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_list_secrets_returns_names_only(client: TestClient, mocker) -> None:
    mock_list = mocker.patch(
        "ai_etl.api.routers.secrets.list_secret_names", return_value=["api_key", "db_password"]
    )

    response = client.get("/secrets")

    assert response.status_code == 200
    assert response.json() == ["api_key", "db_password"]
    mock_list.assert_called_once_with("tenant-a")


def test_create_secret_stores_it(client: TestClient, mocker) -> None:
    mock_set = mocker.patch("ai_etl.api.routers.secrets.set_secret")

    response = client.post("/secrets", json={"name": "api_key", "value": "sk-super-secret"})

    assert response.status_code == 200
    assert response.json() == {"name": "api_key", "status": "stored"}
    mock_set.assert_called_once_with("tenant-a", "api_key", "sk-super-secret")
    # The stored value never appears anywhere in the response body.
    assert "sk-super-secret" not in response.text


def test_create_secret_fails_closed_without_encryption_key(client: TestClient, mocker) -> None:
    mocker.patch(
        "ai_etl.api.routers.secrets.set_secret",
        side_effect=SecretsEncryptionKeyMissingError("AI_ETL_SECRETS_ENCRYPTION_KEY not set"),
    )

    response = client.post("/secrets", json={"name": "api_key", "value": "sk-abc"})

    assert response.status_code == 503


def test_delete_secret_404_when_unknown(client: TestClient, mocker) -> None:
    mocker.patch("ai_etl.api.routers.secrets.delete_secret", return_value=False)

    response = client.delete("/secrets/does-not-exist")

    assert response.status_code == 404


def test_delete_secret_happy_path(client: TestClient, mocker) -> None:
    mock_delete = mocker.patch("ai_etl.api.routers.secrets.delete_secret", return_value=True)

    response = client.delete("/secrets/api_key")

    assert response.status_code == 200
    mock_delete.assert_called_once_with("tenant-a", "api_key")


def test_viewer_role_cannot_list_secrets(client: TestClient, mocker) -> None:
    """RBAC (ADR-021): secrets management is editor-only end to end — a
    viewer cannot even list secret *names*."""
    app.dependency_overrides[get_current_auth_context] = lambda: {
        "tenant_id": "tenant-a",
        "role": "viewer",
    }
    mock_list = mocker.patch("ai_etl.api.routers.secrets.list_secret_names")

    response = client.get("/secrets")

    assert response.status_code == 403
    mock_list.assert_not_called()


def test_viewer_role_cannot_create_secret(client: TestClient, mocker) -> None:
    app.dependency_overrides[get_current_auth_context] = lambda: {
        "tenant_id": "tenant-a",
        "role": "viewer",
    }
    mock_set = mocker.patch("ai_etl.api.routers.secrets.set_secret")

    response = client.post("/secrets", json={"name": "api_key", "value": "sk-abc"})

    assert response.status_code == 403
    mock_set.assert_not_called()
