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


# ---------------------------------------------------------------------------
# `GET /tenant/export` (Sprint 36, ADR-035)
# ---------------------------------------------------------------------------


def _export() -> dict:
    now = datetime.now(tz=timezone.utc)
    return {
        "tenant_id": "tenant-a",
        "exported_at": now.isoformat(),
        "user": {"id": "tenant-a", "created_at": now.isoformat(), "monthly_budget_usd": None},
        "runs": [],
        "analysis_runs": [],
        "stage_latencies": [],
        "saved_pipelines": [],
        "tenant_secrets": [],
        "storage_artifacts": [],
    }


def test_export_tenant_happy_path(client: TestClient, mocker) -> None:
    mock_export = mocker.patch(
        "ai_etl.api.routers.tenant.export_tenant_data", return_value=_export()
    )

    response = client.get("/tenant/export")

    assert response.status_code == 200
    assert response.json()["tenant_id"] == "tenant-a"
    mock_export.assert_called_once_with("tenant-a")


def test_export_tenant_404_when_unknown(client: TestClient, mocker) -> None:
    mocker.patch(
        "ai_etl.api.routers.tenant.export_tenant_data",
        side_effect=TenantNotFoundError("tenant-a"),
    )

    response = client.get("/tenant/export")

    assert response.status_code == 404


def test_viewer_role_can_export_tenant(client: TestClient, mocker) -> None:
    app.dependency_overrides[get_current_auth_context] = lambda: {
        "tenant_id": "tenant-a",
        "role": "viewer",
    }
    mocker.patch("ai_etl.api.routers.tenant.export_tenant_data", return_value=_export())

    response = client.get("/tenant/export")

    assert response.status_code == 200


def test_export_never_takes_a_tenant_id_from_the_request(client: TestClient, mocker) -> None:
    """Same scope-strict contract as `DELETE /tenant` (ADR-025 Decision 1) —
    there is no way to pass another tenant's id in; it always comes from the
    resolved auth context."""
    mock_export = mocker.patch(
        "ai_etl.api.routers.tenant.export_tenant_data", return_value=_export()
    )

    client.get("/tenant/export", params={"tenant_id": "someone-elses-tenant"})

    mock_export.assert_called_once_with("tenant-a")


# ---------------------------------------------------------------------------
# `GET`/`PATCH /tenant/retention` (Sprint 36, ADR-035)
# ---------------------------------------------------------------------------


def test_get_retention_returns_none_when_unset(client: TestClient, mocker) -> None:
    mocker.patch("ai_etl.api.routers.tenant.get_retention_days", return_value=None)

    response = client.get("/tenant/retention")

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["retention_days"] is None


def test_viewer_role_can_read_retention(client: TestClient, mocker) -> None:
    app.dependency_overrides[get_current_auth_context] = lambda: {
        "tenant_id": "tenant-a",
        "role": "viewer",
    }
    mocker.patch("ai_etl.api.routers.tenant.get_retention_days", return_value=30)

    response = client.get("/tenant/retention")

    assert response.status_code == 200
    assert response.json()["retention_days"] == 30


def test_patch_retention_happy_path(client: TestClient, mocker) -> None:
    mock_set = mocker.patch("ai_etl.api.routers.tenant.set_retention_days")

    response = client.patch("/tenant/retention", json={"retention_days": 30})

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["retention_days"] == 30
    mock_set.assert_called_once_with("tenant-a", 30)


def test_patch_retention_none_clears_the_policy(client: TestClient, mocker) -> None:
    mock_set = mocker.patch("ai_etl.api.routers.tenant.set_retention_days")

    response = client.patch("/tenant/retention", json={"retention_days": None})

    assert response.status_code == 200
    assert response.json()["retention_days"] is None
    mock_set.assert_called_once_with("tenant-a", None)


def test_patch_retention_rejects_zero_or_negative(client: TestClient, mocker) -> None:
    mock_set = mocker.patch("ai_etl.api.routers.tenant.set_retention_days")

    response = client.patch("/tenant/retention", json={"retention_days": 0})

    assert response.status_code == 422
    mock_set.assert_not_called()


def test_patch_retention_requires_the_key_present(client: TestClient, mocker) -> None:
    mock_set = mocker.patch("ai_etl.api.routers.tenant.set_retention_days")

    response = client.patch("/tenant/retention", json={})

    assert response.status_code == 422
    mock_set.assert_not_called()


def test_viewer_role_cannot_patch_retention(client: TestClient, mocker) -> None:
    app.dependency_overrides[get_current_auth_context] = lambda: {
        "tenant_id": "tenant-a",
        "role": "viewer",
    }
    mock_set = mocker.patch("ai_etl.api.routers.tenant.set_retention_days")

    response = client.patch("/tenant/retention", json={"retention_days": 30})

    assert response.status_code == 403
    mock_set.assert_not_called()
