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
# GET /admin/tenants
# ---------------------------------------------------------------------------


def test_admin_list_tenants_returns_directory(client: TestClient, mocker) -> None:
    from datetime import datetime, timezone

    _as_admin()
    tenants = [
        {"tenant_id": "tenant-a", "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"tenant_id": "tenant-b", "created_at": datetime(2026, 2, 1, tzinfo=timezone.utc)},
    ]
    mocker.patch("ai_etl.api.routers.admin.list_all_tenants", return_value=tenants)
    mock_log = mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.get("/admin/tenants", params={"reason": "quarterly review"})

    assert response.status_code == 200
    body = response.json()
    assert [t["tenant_id"] for t in body] == ["tenant-a", "tenant-b"]
    mock_log.assert_called_once_with(
        "admin-user-1", "list_tenants", reason="quarterly review", detail="count=2"
    )


def test_admin_list_tenants_rejects_non_admin(client: TestClient, mocker) -> None:
    _as_editor()
    mock_list = mocker.patch("ai_etl.api.routers.admin.list_all_tenants")

    response = client.get("/admin/tenants", params={"reason": "quarterly review"})

    assert response.status_code == 403
    mock_list.assert_not_called()


def test_admin_list_tenants_requires_reason(client: TestClient, mocker) -> None:
    _as_admin()
    mock_list = mocker.patch("ai_etl.api.routers.admin.list_all_tenants")

    response = client.get("/admin/tenants")

    assert response.status_code == 422
    mock_list.assert_not_called()


# ---------------------------------------------------------------------------
# GET /admin/tenants/{tenant_id}/runs
# ---------------------------------------------------------------------------


def test_admin_list_tenant_runs_returns_history_rows(client: TestClient, mocker) -> None:
    _as_admin()
    df = pd.DataFrame({"run_id": ["run-1"], "status": ["completed"], "cost_usd": [0.001]})
    mock_load_history = mocker.patch("ai_etl.api.routers.admin.load_history", return_value=df)
    mock_log = mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.get("/admin/tenants/tenant-b/runs", params={"reason": "support ticket #42"})

    assert response.status_code == 200
    assert response.json() == [{"run_id": "run-1", "status": "completed", "cost_usd": 0.001}]
    mock_load_history.assert_called_once_with(limit=20, tenant_id="tenant-b")
    mock_log.assert_called_once_with(
        "admin-user-1",
        "list_tenant_runs",
        target_tenant_id="tenant-b",
        reason="support ticket #42",
        detail="limit=20",
    )


def test_admin_list_tenant_runs_serializes_nan_as_null(client: TestClient, mocker) -> None:
    _as_admin()
    df = pd.DataFrame({"run_id": ["run-1"], "cost_usd": [float("nan")]})
    mocker.patch("ai_etl.api.routers.admin.load_history", return_value=df)
    mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.get("/admin/tenants/tenant-b/runs", params={"reason": "support ticket"})

    assert response.json()[0]["cost_usd"] is None


def test_admin_list_tenant_runs_unknown_tenant_returns_empty_list(
    client: TestClient, mocker
) -> None:
    _as_admin()
    mocker.patch(
        "ai_etl.api.routers.admin.load_history", return_value=pd.DataFrame(columns=["run_id"])
    )
    mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.get("/admin/tenants/no-such-tenant/runs", params={"reason": "support ticket"})

    assert response.status_code == 200
    assert response.json() == []


def test_admin_list_tenant_runs_rejects_non_admin(client: TestClient, mocker) -> None:
    _as_editor()
    mock_load_history = mocker.patch("ai_etl.api.routers.admin.load_history")

    response = client.get("/admin/tenants/tenant-b/runs", params={"reason": "support ticket"})

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

    response = client.get("/admin/tenants/tenant-b/budget", params={"reason": "billing triage"})

    assert response.status_code == 200
    assert response.json() == status
    mock_get_status.assert_called_once_with("tenant-b")
    mock_log.assert_called_once_with(
        "admin-user-1",
        "view_tenant_budget",
        target_tenant_id="tenant-b",
        reason="billing triage",
    )


def test_admin_get_tenant_budget_rejects_non_admin(client: TestClient, mocker) -> None:
    _as_editor()
    mock_get_status = mocker.patch("ai_etl.api.routers.admin.get_budget_status")

    response = client.get("/admin/tenants/tenant-b/budget", params={"reason": "billing triage"})

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
            "reason": "billing triage",
            "created_at": datetime(2026, 8, 24, tzinfo=timezone.utc),
        }
    ]
    mock_list = mocker.patch("ai_etl.api.routers.admin.list_admin_actions", return_value=records)
    mock_log = mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.get("/admin/audit-log", params={"reason": "compliance review"})

    assert response.status_code == 200
    body = response.json()
    assert body[0]["id"] == "log-1"
    assert body[0]["action"] == "view_tenant_budget"
    mock_list.assert_called_once_with(limit=100, actor_user_id=None, target_tenant_id=None)
    mock_log.assert_called_once_with(
        "admin-user-1",
        "view_admin_audit_log",
        target_tenant_id=None,
        reason="compliance review",
        detail="limit=100, actor_user_id=None",
    )


def test_admin_get_audit_log_passes_filters_through(client: TestClient, mocker) -> None:
    _as_admin()
    mock_list = mocker.patch("ai_etl.api.routers.admin.list_admin_actions", return_value=[])
    mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.get(
        "/admin/audit-log",
        params={
            "limit": 5,
            "actor_user_id": "u1",
            "target_tenant_id": "t1",
            "reason": "compliance review",
        },
    )

    assert response.status_code == 200
    mock_list.assert_called_once_with(limit=5, actor_user_id="u1", target_tenant_id="t1")


def test_admin_get_audit_log_rejects_non_admin(client: TestClient, mocker) -> None:
    _as_editor()
    mock_list = mocker.patch("ai_etl.api.routers.admin.list_admin_actions")

    response = client.get("/admin/audit-log", params={"reason": "compliance review"})

    assert response.status_code == 403
    mock_list.assert_not_called()


# ---------------------------------------------------------------------------
# POST /admin/tenants/{tenant_id}/pipelines/{pipeline_id}/retry
# ---------------------------------------------------------------------------


def test_admin_retry_tenant_pipeline_enqueues_and_logs(client: TestClient, mocker) -> None:
    _as_admin()
    pipeline = {
        "id": "pipeline-1",
        "tenant_id": "tenant-b",
        "spec": "load orders.csv",
        "business_question": "What were last month's top products?",
    }
    mocker.patch("ai_etl.api.routers.admin.get_saved_pipeline", return_value=pipeline)
    mock_enqueue = mocker.patch(
        "ai_etl.api.routers.admin.enqueue_analysis", return_value="task-123"
    )
    mock_log = mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.post(
        "/admin/tenants/tenant-b/pipelines/pipeline-1/retry",
        json={"reason": "tenant emailed support, run stuck queued for 2h"},
    )

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-123"}
    mock_enqueue.assert_called_once_with(
        "load orders.csv",
        "What were last month's top products?",
        run_dir=mocker.ANY,
        tenant_id="tenant-b",
        saved_pipeline_id="pipeline-1",
    )
    mock_log.assert_called_once_with(
        "admin-user-1",
        "retry_pipeline",
        target_tenant_id="tenant-b",
        reason="tenant emailed support, run stuck queued for 2h",
        detail="pipeline_id=pipeline-1, task_id=task-123",
    )


def test_admin_retry_tenant_pipeline_unknown_pipeline_returns_404(
    client: TestClient, mocker
) -> None:
    _as_admin()
    mocker.patch("ai_etl.api.routers.admin.get_saved_pipeline", return_value=None)
    mock_enqueue = mocker.patch("ai_etl.api.routers.admin.enqueue_analysis")
    mock_log = mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.post(
        "/admin/tenants/tenant-b/pipelines/no-such-pipeline/retry",
        json={"reason": "support ticket"},
    )

    assert response.status_code == 404
    mock_enqueue.assert_not_called()
    mock_log.assert_called_once_with(
        "admin-user-1",
        "retry_pipeline_not_found",
        target_tenant_id="tenant-b",
        reason="support ticket",
        detail="pipeline_id=no-such-pipeline",
    )


def test_admin_retry_tenant_pipeline_budget_exceeded_returns_402(
    client: TestClient, mocker
) -> None:
    _as_admin()
    from ai_etl.services.execution_queue import BudgetExceededError

    pipeline = {
        "id": "pipeline-1",
        "tenant_id": "tenant-b",
        "spec": "load orders.csv",
        "business_question": "q",
    }
    mocker.patch("ai_etl.api.routers.admin.get_saved_pipeline", return_value=pipeline)
    mocker.patch(
        "ai_etl.api.routers.admin.enqueue_analysis",
        side_effect=BudgetExceededError("over cap"),
    )
    mock_log = mocker.patch("ai_etl.api.routers.admin.log_admin_action")

    response = client.post(
        "/admin/tenants/tenant-b/pipelines/pipeline-1/retry",
        json={"reason": "support ticket"},
    )

    assert response.status_code == 402
    mock_log.assert_called_once_with(
        "admin-user-1",
        "retry_pipeline_rejected",
        target_tenant_id="tenant-b",
        reason="support ticket",
        detail="pipeline_id=pipeline-1, error=over cap",
    )


def test_admin_retry_tenant_pipeline_requires_reason(client: TestClient, mocker) -> None:
    _as_admin()
    mock_enqueue = mocker.patch("ai_etl.api.routers.admin.enqueue_analysis")

    response = client.post(
        "/admin/tenants/tenant-b/pipelines/pipeline-1/retry",
        json={"reason": ""},
    )

    assert response.status_code == 422
    mock_enqueue.assert_not_called()


def test_admin_retry_tenant_pipeline_rejects_non_admin(client: TestClient, mocker) -> None:
    _as_editor()
    mock_enqueue = mocker.patch("ai_etl.api.routers.admin.enqueue_analysis")

    response = client.post(
        "/admin/tenants/tenant-b/pipelines/pipeline-1/retry",
        json={"reason": "support ticket"},
    )

    assert response.status_code == 403
    mock_enqueue.assert_not_called()
