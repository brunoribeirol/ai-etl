"""Tests for `/pipelines` endpoints (Sprint 13, ADR-016).

Same convention as `test_api_runs.py`: mocks at the import site inside
`api/routers/pipelines.py`, auth overridden via `dependency_overrides`.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ai_etl.api.deps import get_current_tenant_id
from ai_etl.api.main import app


@pytest.fixture(autouse=True)
def _override_auth() -> None:
    app.dependency_overrides[get_current_tenant_id] = lambda: "tenant-a"
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
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    row.update(overrides)
    return row


def test_list_pipelines_scoped_to_tenant(client: TestClient, mocker) -> None:
    mock_list = mocker.patch(
        "ai_etl.api.routers.pipelines.list_saved_pipelines",
        return_value=[_saved_pipeline_row()],
    )

    response = client.get("/pipelines")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "pl-1"
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

    response = client.get("/pipelines/pl-1")

    assert response.status_code == 200
    assert response.json()["name"] == "Nightly sync"


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
    mock_history.assert_called_once_with("pl-1", "tenant-a")


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
    )


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
