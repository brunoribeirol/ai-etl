"""Tests for `DELETE /tenant` (Sprint 24, ADR-025).

Same convention as `test_api_secrets.py`: mocks at the import site inside
`api/routers/tenant.py`, auth overridden via `dependency_overrides`.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ai_etl.api.deps import get_current_auth_context
from ai_etl.api.main import app
from ai_etl.services.tenant_deletion_service import TenantDeletionSummary, TenantNotFoundError


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


def _summary() -> TenantDeletionSummary:
    now = datetime.now(tz=timezone.utc)
    return TenantDeletionSummary(
        tenant_id="tenant-a",
        requested_at=now,
        completed_at=now,
        runs_deleted=3,
        analysis_runs_deleted=2,
        stage_latencies_deleted=5,
        saved_pipelines_deleted=1,
        secrets_deleted=1,
        storage_keys_deleted=9,
        status="completed",
        error=None,
    )


def test_delete_tenant_requires_confirmation_body(client: TestClient, mocker) -> None:
    mock_delete = mocker.patch("ai_etl.api.routers.tenant.delete_tenant_data")

    response = client.request("DELETE", "/tenant", json={})

    assert response.status_code == 422
    mock_delete.assert_not_called()


def test_delete_tenant_rejects_wrong_confirmation_value(client: TestClient, mocker) -> None:
    mock_delete = mocker.patch("ai_etl.api.routers.tenant.delete_tenant_data")

    response = client.request("DELETE", "/tenant", json={"confirm": "delete"})

    assert response.status_code == 422
    mock_delete.assert_not_called()


def test_delete_tenant_happy_path(client: TestClient, mocker) -> None:
    mock_delete = mocker.patch(
        "ai_etl.api.routers.tenant.delete_tenant_data", return_value=_summary()
    )

    response = client.request("DELETE", "/tenant", json={"confirm": "DELETE"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["runs_deleted"] == 3
    mock_delete.assert_called_once_with("tenant-a", requested_by="tenant-a")


def test_delete_tenant_404_when_unknown(client: TestClient, mocker) -> None:
    mocker.patch(
        "ai_etl.api.routers.tenant.delete_tenant_data",
        side_effect=TenantNotFoundError("tenant-a"),
    )

    response = client.request("DELETE", "/tenant", json={"confirm": "DELETE"})

    assert response.status_code == 404


def test_viewer_role_cannot_delete_tenant(client: TestClient, mocker) -> None:
    app.dependency_overrides[get_current_auth_context] = lambda: {
        "tenant_id": "tenant-a",
        "role": "viewer",
    }
    mock_delete = mocker.patch("ai_etl.api.routers.tenant.delete_tenant_data")

    response = client.request("DELETE", "/tenant", json={"confirm": "DELETE"})

    assert response.status_code == 403
    mock_delete.assert_not_called()
