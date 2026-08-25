"""Tests for `/admin` endpoints (Sprint 31, ADR-032).

Mocks at the import site inside `api/routers/admin.py`, same convention
`test_api_runs.py` already uses — and overrides the auth dependency via
FastAPI's `dependency_overrides` rather than minting real JWTs here (that's
already covered by `test_api_deps.py`).

Gap-closing (2026-08-24 QA audit, Wave 5): this router previously had zero
direct endpoint tests — only the persistence layer (`audit/admin_log.py`)
was exercised. These tests cover the actual FastAPI wiring: successful
admin-role access, the `require_admin()` 403 gate for a non-admin caller,
and that every route logs exactly one admin action.
"""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ai_etl.api.deps import get_current_auth_context
from ai_etl.api.main import app
from ai_etl.services.execution_queue import BudgetStatus

_ADMIN_AUTH = {"tenant_id": "admin-own-tenant", "role": "admin", "user_id": "admin-user-1"}
_EDITOR_AUTH = {"tenant_id": "tenant-a", "role": "editor", "user_id": "editor-user-1"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _as_admin() -> None:
    app.dependency_overrides[get_current_auth_context] = lambda: _ADMIN_AUTH


def _as_editor() -> None:
    app.dependency_overrides[get_current_auth_context] = lambda: _EDITOR_AUTH


@pytest.fixture(autouse=True)
def _clear_overrides() -> None:
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /admin/tenants/{tenant_id}/runs
# ---------------------------------------------------------------------------


def test_admin_list_tenant_runs_returns_history_rows(client: TestClient, mocker) -> None:
    _as_admin()
    df = pd.DataFrame({"run_id": ["run-1"], "status": ["completed"], "cost_usd": [0.001]})
    mock_load_history = mocker.patch("ai_etl.api.routers.admin.load_history", return_value=df)
    mock_log = mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.get("/admin/tenants/tenant-b/runs")

    assert response.status_code == 200
    assert response.json() == [{"run_id": "run-1", "status": "completed", "cost_usd": 0.001}]
    mock_load_history.assert_called_once_with(limit=20, tenant_id="tenant-b")
    mock_log.assert_called_once_with(
        "admin-user-1", "list_tenant_runs", target_tenant_id="tenant-b", detail="limit=20"
    )


def test_admin_list_tenant_runs_serializes_nan_as_null(client: TestClient, mocker) -> None:
    _as_admin()
    df = pd.DataFrame({"run_id": ["run-1"], "cost_usd": [float("nan")]})
    mocker.patch("ai_etl.api.routers.admin.load_history", return_value=df)
    mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.get("/admin/tenants/tenant-b/runs")

    assert response.json()[0]["cost_usd"] is None


def test_admin_list_tenant_runs_unknown_tenant_returns_empty_list(
    client: TestClient, mocker
) -> None:
    _as_admin()
    mocker.patch(
        "ai_etl.api.routers.admin.load_history", return_value=pd.DataFrame(columns=["run_id"])
    )
    mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.get("/admin/tenants/no-such-tenant/runs")

    assert response.status_code == 200
    assert response.json() == []


def test_admin_list_tenant_runs_rejects_non_admin(client: TestClient, mocker) -> None:
    _as_editor()
    mock_load_history = mocker.patch("ai_etl.api.routers.admin.load_history")

    response = client.get("/admin/tenants/tenant-b/runs")

    assert response.status_code == 403
    mock_load_history.assert_not_called()


# ---------------------------------------------------------------------------
# GET /admin/tenants/{tenant_id}/budget
# ---------------------------------------------------------------------------


def test_admin_get_tenant_budget_returns_status(client: TestClient, mocker) -> None:
    _as_admin()
    status: BudgetStatus = {
        "cap_usd": 100.0,
        "spent_usd": 42.5,
        "ratio": 0.425,
        "near_limit": False,
        "exceeded": False,
    }
    mock_get_status = mocker.patch(
        "ai_etl.api.routers.admin.get_budget_status", return_value=status
    )
    mock_log = mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.get("/admin/tenants/tenant-b/budget")

    assert response.status_code == 200
    assert response.json() == status
    mock_get_status.assert_called_once_with("tenant-b")
    mock_log.assert_called_once_with(
        "admin-user-1", "view_tenant_budget", target_tenant_id="tenant-b"
    )


def test_admin_get_tenant_budget_rejects_non_admin(client: TestClient, mocker) -> None:
    _as_editor()
    mock_get_status = mocker.patch("ai_etl.api.routers.admin.get_budget_status")

    response = client.get("/admin/tenants/tenant-b/budget")

    assert response.status_code == 403
    mock_get_status.assert_not_called()


# ---------------------------------------------------------------------------
# GET /admin/audit-log
# ---------------------------------------------------------------------------


def test_admin_get_audit_log_returns_records(client: TestClient, mocker) -> None:
    _as_admin()
    from datetime import datetime, timezone

    records = [
        {
            "id": "log-1",
            "actor_user_id": "admin-user-1",
            "action": "view_tenant_budget",
            "target_tenant_id": "tenant-b",
            "detail": None,
            "created_at": datetime(2026, 8, 24, tzinfo=timezone.utc),
        }
    ]
    mock_list = mocker.patch("ai_etl.api.routers.admin.list_admin_actions", return_value=records)
    mock_log = mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.get("/admin/audit-log")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["id"] == "log-1"
    assert body[0]["action"] == "view_tenant_budget"
    mock_list.assert_called_once_with(limit=100, actor_user_id=None, target_tenant_id=None)
    mock_log.assert_called_once_with(
        "admin-user-1",
        "view_admin_audit_log",
        target_tenant_id=None,
        detail="limit=100, actor_user_id=None",
    )


def test_admin_get_audit_log_passes_filters_through(client: TestClient, mocker) -> None:
    _as_admin()
    mock_list = mocker.patch("ai_etl.api.routers.admin.list_admin_actions", return_value=[])
    mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.get(
        "/admin/audit-log", params={"limit": 5, "actor_user_id": "u1", "target_tenant_id": "t1"}
    )

    assert response.status_code == 200
    mock_list.assert_called_once_with(limit=5, actor_user_id="u1", target_tenant_id="t1")


def test_admin_get_audit_log_rejects_non_admin(client: TestClient, mocker) -> None:
    _as_editor()
    mock_list = mocker.patch("ai_etl.api.routers.admin.list_admin_actions")

    response = client.get("/admin/audit-log")

    assert response.status_code == 403
    mock_list.assert_not_called()
