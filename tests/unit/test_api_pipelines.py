"""Tests for `/pipelines` endpoints (Sprint 13, ADR-016).

Same convention as `test_api_runs.py`: mocks at the import site inside
`api/routers/pipelines.py`, auth overridden via `dependency_overrides`.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ai_etl.api.deps import get_current_auth_context
from ai_etl.api.main import app
from ai_etl.audit.db import DEFAULT_PIPELINE_HISTORY_LIMIT


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


def _saved_pipeline_row(**overrides: object) -> dict:
    row = {
        "id": "pl-1",
        "tenant_id": "tenant-a",
        "name": "Nightly sync",
        "source_type": "postgres",
        "spec": "Read schema.orders from postgres",
        "business_question": "",
        "cron_schedule": "0 3 * * *",
        "is_active": True,
        "next_run_at": datetime.now(tz=timezone.utc).isoformat(),
        "last_task_id": None,
        "last_run_at": None,
        "drift_threshold_pct": 20.0,
        # Sprint 15 (ADR-020) — health-snapshot cache columns.
        "consecutive_failures": 0,
        "last_status": None,
        "last_error": None,
        # Sprint 16 (ADR-023) — operator-defined quality rules.
        "quality_rules": [],
        # Sprint 27 (ADR-028) — write-approval gate.
        "require_approval": False,
        "approval_threshold_rows": None,
        "last_approved_at": None,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    row.update(overrides)
    return row


def _health(**overrides: object) -> dict:
    health = {"success_rate": None, "avg_latency_seconds": None, "sample_size": 0}
    health.update(overrides)
    return health


def test_list_pipelines_scoped_to_tenant(client: TestClient, mocker) -> None:
    mock_list = mocker.patch(
        "ai_etl.api.routers.pipelines.list_saved_pipelines",
        return_value=[_saved_pipeline_row()],
    )
    mocker.patch(
        "ai_etl.api.routers.pipelines.get_pipeline_health",
        return_value=_health(success_rate=1.0, sample_size=5),
    )

    response = client.get("/pipelines")

    assert response.status_code == 200
    body = response.json()[0]
    assert body["id"] == "pl-1"
    assert body["success_rate"] == 1.0
    assert body["health_sample_size"] == 5
    mock_list.assert_called_once_with("tenant-a")


def test_get_pipeline_404_when_unknown(client: TestClient, mocker) -> None:
    mocker.patch("ai_etl.api.routers.pipelines.get_saved_pipeline", return_value=None)

    response = client.get("/pipelines/no-such-id")

    assert response.status_code == 404


def test_get_pipeline_returns_row(client: TestClient, mocker) -> None:
    mocker.patch(
        "ai_etl.api.routers.pipelines.get_saved_pipeline",
        return_value=_saved_pipeline_row(),
    )
    mocker.patch("ai_etl.api.routers.pipelines.get_pipeline_health", return_value=_health())

    response = client.get("/pipelines/pl-1")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Nightly sync"
    assert body["success_rate"] is None
    assert body["health_sample_size"] == 0


def test_get_pipeline_history_404_when_pipeline_unknown(client: TestClient, mocker) -> None:
    """Sprint 17 (ADR-017) — 404s before ever calling `list_pipeline_run_history`,
    so an unowned/unknown pipeline id can't be used to probe for run history."""
    mocker.patch("ai_etl.api.routers.pipelines.get_saved_pipeline", return_value=None)
    mock_history = mocker.patch("ai_etl.api.routers.pipelines.list_pipeline_run_history")

    response = client.get("/pipelines/no-such-id/history")

    assert response.status_code == 404
    mock_history.assert_not_called()


def test_get_pipeline_history_returns_time_series(client: TestClient, mocker) -> None:
    mocker.patch(
        "ai_etl.api.routers.pipelines.get_saved_pipeline",
        return_value=_saved_pipeline_row(),
    )
    history_rows = [
        {
            "run_id": "run-1",
            "status": "completed",
            "rows_loaded": 100,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "error": None,
            "cost_usd": 0.001,
            "model_name": "gpt-4o-mini",
            "total_tokens": 500,
            "gold_subtasks": 1,
            "science_subtasks": 0,
        }
    ]
    mock_history = mocker.patch(
        "ai_etl.api.routers.pipelines.list_pipeline_run_history",
        return_value=history_rows,
    )

    response = client.get("/pipelines/pl-1/history")

    assert response.status_code == 200
    assert response.json() == history_rows
    mock_history.assert_called_once_with("pl-1", "tenant-a", limit=DEFAULT_PIPELINE_HISTORY_LIMIT)


def test_get_pipeline_history_forwards_explicit_limit(client: TestClient, mocker) -> None:
    """Sprint 17 code review (PR #64) — `?limit=` must reach
    `list_pipeline_run_history`, not just sit unused on the endpoint."""
    mocker.patch(
        "ai_etl.api.routers.pipelines.get_saved_pipeline",
        return_value=_saved_pipeline_row(),
    )
    mock_history = mocker.patch(
        "ai_etl.api.routers.pipelines.list_pipeline_run_history", return_value=[]
    )

    response = client.get("/pipelines/pl-1/history?limit=50")

    assert response.status_code == 200
    mock_history.assert_called_once_with("pl-1", "tenant-a", limit=50)


def test_get_pipeline_history_rejects_limit_above_cap(client: TestClient, mocker) -> None:
    """1000 is the hard ceiling (FastAPI's `Query(..., le=1000)`) — a caller
    can widen the window but not request an unbounded query."""
    mocker.patch(
        "ai_etl.api.routers.pipelines.get_saved_pipeline",
        return_value=_saved_pipeline_row(),
    )
    mocker.patch("ai_etl.api.routers.pipelines.list_pipeline_run_history", return_value=[])

    response = client.get("/pipelines/pl-1/history?limit=5000")

    assert response.status_code == 422


def test_create_pipeline_rejects_non_live_source_type(client: TestClient, mocker) -> None:
    mock_create = mocker.patch("ai_etl.api.routers.pipelines.create_saved_pipeline")

    response = client.post(
        "/pipelines",
        json={
            "name": "Bad",
            "source_type": "csv",
            "spec": "load sales.csv",
            "cron_schedule": "0 3 * * *",
        },
    )

    assert response.status_code == 400
    assert "csv" in response.json()["detail"]
    mock_create.assert_not_called()


def test_create_pipeline_rejects_invalid_cron(client: TestClient, mocker) -> None:
    mock_create = mocker.patch("ai_etl.api.routers.pipelines.create_saved_pipeline")

    response = client.post(
        "/pipelines",
        json={
            "name": "Bad cron",
            "source_type": "postgres",
            "spec": "Read schema.orders from postgres",
            "cron_schedule": "not a cron",
        },
    )

    assert response.status_code == 400
    mock_create.assert_not_called()


def test_create_pipeline_happy_path(client: TestClient, mocker) -> None:
    mock_create = mocker.patch(
        "ai_etl.api.routers.pipelines.create_saved_pipeline",
        return_value=_saved_pipeline_row(),
    )

    response = client.post(
        "/pipelines",
        json={
            "name": "Nightly sync",
            "source_type": "postgres",
            "spec": "Read schema.orders from postgres",
            "cron_schedule": "0 3 * * *",
            "business_question": "",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "pl-1"
    mock_create.assert_called_once_with(
        tenant_id="tenant-a",
        name="Nightly sync",
        source_type="postgres",
        spec="Read schema.orders from postgres",
        cron_schedule="0 3 * * *",
        business_question="",
        drift_threshold_pct=20.0,
        quality_rules=[],
        require_approval=False,
        approval_threshold_rows=None,
    )


def test_create_pipeline_accepts_custom_drift_threshold(client: TestClient, mocker) -> None:
    mock_create = mocker.patch(
        "ai_etl.api.routers.pipelines.create_saved_pipeline",
        return_value=_saved_pipeline_row(drift_threshold_pct=5.0),
    )

    response = client.post(
        "/pipelines",
        json={
            "name": "Nightly sync",
            "source_type": "postgres",
            "spec": "Read schema.orders from postgres",
            "cron_schedule": "0 3 * * *",
            "drift_threshold_pct": 5.0,
        },
    )

    assert response.status_code == 200
    assert response.json()["drift_threshold_pct"] == 5.0
    mock_create.assert_called_once_with(
        tenant_id="tenant-a",
        name="Nightly sync",
        source_type="postgres",
        spec="Read schema.orders from postgres",
        cron_schedule="0 3 * * *",
        business_question="",
        drift_threshold_pct=5.0,
        quality_rules=[],
        require_approval=False,
        approval_threshold_rows=None,
    )


def test_create_pipeline_rejects_non_positive_drift_threshold(client: TestClient, mocker) -> None:
    mock_create = mocker.patch("ai_etl.api.routers.pipelines.create_saved_pipeline")

    response = client.post(
        "/pipelines",
        json={
            "name": "Bad",
            "source_type": "postgres",
            "spec": "Read schema.orders from postgres",
            "cron_schedule": "0 3 * * *",
            "drift_threshold_pct": 0,
        },
    )

    assert response.status_code == 422
    mock_create.assert_not_called()


def test_patch_pipeline_pause(client: TestClient, mocker) -> None:
    mock_update = mocker.patch(
        "ai_etl.api.routers.pipelines.update_saved_pipeline",
        return_value=_saved_pipeline_row(is_active=False),
    )

    response = client.patch("/pipelines/pl-1", json={"is_active": False})

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    mock_update.assert_called_once_with(
        "pl-1",
        "tenant-a",
        name=None,
        source_type=None,
        spec=None,
        cron_schedule=None,
        business_question=None,
        is_active=False,
        drift_threshold_pct=None,
        quality_rules=None,
        require_approval=None,
        approval_threshold_rows=None,
    )


def test_patch_pipeline_updates_drift_threshold(client: TestClient, mocker) -> None:
    mock_update = mocker.patch(
        "ai_etl.api.routers.pipelines.update_saved_pipeline",
        return_value=_saved_pipeline_row(drift_threshold_pct=50.0),
    )

    response = client.patch("/pipelines/pl-1", json={"drift_threshold_pct": 50.0})

    assert response.status_code == 200
    assert response.json()["drift_threshold_pct"] == 50.0
    mock_update.assert_called_once_with(
        "pl-1",
        "tenant-a",
        name=None,
        source_type=None,
        spec=None,
        cron_schedule=None,
        business_question=None,
        is_active=None,
        drift_threshold_pct=50.0,
        quality_rules=None,
        require_approval=None,
        approval_threshold_rows=None,
    )


def test_create_pipeline_accepts_quality_rules(client: TestClient, mocker) -> None:
    rules = [{"column": "amount", "operator": "gte", "value": 0, "severity": "error"}]
    mock_create = mocker.patch(
        "ai_etl.api.routers.pipelines.create_saved_pipeline",
        return_value=_saved_pipeline_row(quality_rules=rules),
    )

    response = client.post(
        "/pipelines",
        json={
            "name": "Nightly sync",
            "source_type": "postgres",
            "spec": "Read schema.orders from postgres",
            "cron_schedule": "0 3 * * *",
            "quality_rules": rules,
        },
    )

    assert response.status_code == 200
    assert response.json()["quality_rules"] == rules
    mock_create.assert_called_once_with(
        tenant_id="tenant-a",
        name="Nightly sync",
        source_type="postgres",
        spec="Read schema.orders from postgres",
        cron_schedule="0 3 * * *",
        business_question="",
        drift_threshold_pct=20.0,
        quality_rules=[
            {"column": "amount", "operator": "gte", "value": 0, "severity": "error", "name": None}
        ],
        require_approval=False,
        approval_threshold_rows=None,
    )


def test_create_pipeline_rejects_unknown_operator(client: TestClient, mocker) -> None:
    mock_create = mocker.patch("ai_etl.api.routers.pipelines.create_saved_pipeline")

    response = client.post(
        "/pipelines",
        json={
            "name": "Bad rule",
            "source_type": "postgres",
            "spec": "Read schema.orders from postgres",
            "cron_schedule": "0 3 * * *",
            "quality_rules": [{"column": "amount", "operator": "not_a_real_operator", "value": 0}],
        },
    )

    assert response.status_code == 422
    mock_create.assert_not_called()


def test_create_pipeline_rejects_missing_value_for_comparison_operator(
    client: TestClient, mocker
) -> None:
    """Sprint 16 (ADR-023) — every operator except `not_null` requires `value`."""
    mock_create = mocker.patch("ai_etl.api.routers.pipelines.create_saved_pipeline")

    response = client.post(
        "/pipelines",
        json={
            "name": "Bad rule",
            "source_type": "postgres",
            "spec": "Read schema.orders from postgres",
            "cron_schedule": "0 3 * * *",
            "quality_rules": [{"column": "amount", "operator": "gte"}],
        },
    )

    assert response.status_code == 422
    mock_create.assert_not_called()


def test_create_pipeline_not_null_rule_does_not_require_value(client: TestClient, mocker) -> None:
    mock_create = mocker.patch(
        "ai_etl.api.routers.pipelines.create_saved_pipeline",
        return_value=_saved_pipeline_row(),
    )

    response = client.post(
        "/pipelines",
        json={
            "name": "OK rule",
            "source_type": "postgres",
            "spec": "Read schema.orders from postgres",
            "cron_schedule": "0 3 * * *",
            "quality_rules": [{"column": "customer_id", "operator": "not_null"}],
        },
    )

    assert response.status_code == 200
    mock_create.assert_called_once()


def test_patch_pipeline_updates_quality_rules(client: TestClient, mocker) -> None:
    rules = [{"column": "customer_id", "operator": "not_null", "severity": "error"}]
    mock_update = mocker.patch(
        "ai_etl.api.routers.pipelines.update_saved_pipeline",
        return_value=_saved_pipeline_row(quality_rules=rules),
    )

    response = client.patch("/pipelines/pl-1", json={"quality_rules": rules})

    assert response.status_code == 200
    assert response.json()["quality_rules"] == rules
    mock_update.assert_called_once_with(
        "pl-1",
        "tenant-a",
        name=None,
        source_type=None,
        spec=None,
        cron_schedule=None,
        business_question=None,
        is_active=None,
        drift_threshold_pct=None,
        quality_rules=[
            {
                "column": "customer_id",
                "operator": "not_null",
                "value": None,
                "severity": "error",
                "name": None,
            }
        ],
        require_approval=None,
        approval_threshold_rows=None,
    )


def test_patch_pipeline_omitting_quality_rules_leaves_them_untouched(
    client: TestClient, mocker
) -> None:
    mock_update = mocker.patch(
        "ai_etl.api.routers.pipelines.update_saved_pipeline",
        return_value=_saved_pipeline_row(),
    )

    response = client.patch("/pipelines/pl-1", json={"is_active": False})

    assert response.status_code == 200
    mock_update.assert_called_once_with(
        "pl-1",
        "tenant-a",
        name=None,
        source_type=None,
        spec=None,
        cron_schedule=None,
        business_question=None,
        is_active=False,
        drift_threshold_pct=None,
        quality_rules=None,
        require_approval=None,
        approval_threshold_rows=None,
    )


def test_patch_pipeline_404_when_unknown(client: TestClient, mocker) -> None:
    mocker.patch("ai_etl.api.routers.pipelines.update_saved_pipeline", return_value=None)

    response = client.patch("/pipelines/no-such-id", json={"is_active": False})

    assert response.status_code == 404


def test_patch_pipeline_rejects_invalid_cron_before_calling_db(client: TestClient, mocker) -> None:
    mock_update = mocker.patch("ai_etl.api.routers.pipelines.update_saved_pipeline")

    response = client.patch("/pipelines/pl-1", json={"cron_schedule": "garbage"})

    assert response.status_code == 400
    mock_update.assert_not_called()


def test_patch_pipeline_rejects_non_live_source_type(client: TestClient, mocker) -> None:
    mock_update = mocker.patch("ai_etl.api.routers.pipelines.update_saved_pipeline")

    response = client.patch("/pipelines/pl-1", json={"source_type": "document"})

    assert response.status_code == 400
    mock_update.assert_not_called()


def test_viewer_role_cannot_create_pipeline(client: TestClient, mocker) -> None:
    """RBAC (Sprint 19, ADR-022): a `viewer` cannot configure a pipeline —
    only `editor` can, even though both roles share the same tenant_id."""
    app.dependency_overrides[get_current_auth_context] = lambda: {
        "tenant_id": "tenant-a",
        "role": "viewer",
    }
    mock_create = mocker.patch("ai_etl.api.routers.pipelines.create_saved_pipeline")

    response = client.post(
        "/pipelines",
        json={
            "name": "Nightly sync",
            "source_type": "postgres",
            "spec": "Read schema.orders from postgres",
            "cron_schedule": "0 3 * * *",
        },
    )

    assert response.status_code == 403
    mock_create.assert_not_called()


def test_viewer_role_can_still_list_pipelines(client: TestClient, mocker) -> None:
    """Read access stays available to a `viewer` — only mutation is gated."""
    app.dependency_overrides[get_current_auth_context] = lambda: {
        "tenant_id": "tenant-a",
        "role": "viewer",
    }
    mocker.patch("ai_etl.api.routers.pipelines.list_saved_pipelines", return_value=[])

    response = client.get("/pipelines")

    assert response.status_code == 200
