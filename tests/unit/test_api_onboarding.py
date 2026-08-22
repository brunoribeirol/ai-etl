"""Tests for `GET /onboarding/status` (Sprint 26, ADR-027).

Same convention as `test_api_budget.py`: mocks at the import site inside
`api/routers/onboarding.py`, auth overridden via `dependency_overrides`.
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


def test_onboarding_status_returns_the_authenticated_tenants_checklist(
    client: TestClient, mocker
) -> None:
    mock_status = mocker.patch(
        "ai_etl.api.routers.onboarding.get_onboarding_status",
        return_value={
            "run_count": 2,
            "completed_run_count": 1,
            "has_completed_run": True,
            "saved_pipeline_count": 0,
            "has_saved_pipeline": False,
        },
    )

    response = client.get("/onboarding/status")

    assert response.status_code == 200
    body = response.json()
    assert body["has_completed_run"] is True
    assert body["has_saved_pipeline"] is False
    mock_status.assert_called_once_with("tenant-a")


def test_onboarding_status_never_requires_editor_role(client: TestClient, mocker) -> None:
    """Read-only, same trust model as `GET /budget` — a `viewer` can read
    its own activation status (only `PATCH /budget`-style mutations need
    `editor`, and this endpoint has none)."""
    mocker.patch(
        "ai_etl.api.routers.onboarding.get_onboarding_status",
        return_value={
            "run_count": 0,
            "completed_run_count": 0,
            "has_completed_run": False,
            "saved_pipeline_count": 0,
            "has_saved_pipeline": False,
        },
    )

    response = client.get("/onboarding/status")

    assert response.status_code == 200
