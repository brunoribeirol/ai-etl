"""Tests for `/budget` endpoints (Sprint 29, ADR-017).

Same convention as `test_api_runs.py`/`test_api_pipelines.py`: mocks at the
import site inside `api/routers/budget.py`, auth overridden via
`dependency_overrides`.
"""

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


def test_get_budget_returns_status_for_the_authenticated_tenant(client: TestClient, mocker) -> None:
    mock_status = mocker.patch(
        "ai_etl.api.routers.budget.get_budget_status",
        return_value={
            "cap_usd": 10.0,
            "spent_usd": 1.0,
            "ratio": 0.1,
            "near_limit": False,
            "exceeded": False,
        },
    )

    response = client.get("/budget")

    assert response.status_code == 200
    assert response.json()["cap_usd"] == 10.0
    mock_status.assert_called_once_with("tenant-a")


def test_get_budget_never_blocks_a_tenant_already_over_cap(client: TestClient, mocker) -> None:
    """Reading status must always succeed, even when `exceeded` is True —
    only `POST /runs` actually enforces the cap."""
    mocker.patch(
        "ai_etl.api.routers.budget.get_budget_status",
        return_value={
            "cap_usd": 5.0,
            "spent_usd": 6.0,
            "ratio": 1.2,
            "near_limit": False,
            "exceeded": True,
        },
    )

    response = client.get("/budget")

    assert response.status_code == 200
    assert response.json()["exceeded"] is True


def test_patch_budget_sets_the_cap(client: TestClient, mocker) -> None:
    mock_set = mocker.patch("ai_etl.api.routers.budget.set_monthly_budget")
    mocker.patch(
        "ai_etl.api.routers.budget.get_budget_status",
        return_value={
            "cap_usd": 25.0,
            "spent_usd": 0.0,
            "ratio": 0.0,
            "near_limit": False,
            "exceeded": False,
        },
    )

    response = client.patch("/budget", json={"monthly_budget_usd": 25.0})

    assert response.status_code == 200
    assert response.json()["cap_usd"] == 25.0
    mock_set.assert_called_once_with("tenant-a", 25.0)


def test_patch_budget_null_clears_the_cap(client: TestClient, mocker) -> None:
    mock_set = mocker.patch("ai_etl.api.routers.budget.set_monthly_budget")
    mocker.patch(
        "ai_etl.api.routers.budget.get_budget_status",
        return_value={
            "cap_usd": None,
            "spent_usd": 0.0,
            "ratio": None,
            "near_limit": False,
            "exceeded": False,
        },
    )

    response = client.patch("/budget", json={"monthly_budget_usd": None})

    assert response.status_code == 200
    mock_set.assert_called_once_with("tenant-a", None)


def test_patch_budget_rejects_a_negative_cap(client: TestClient) -> None:
    response = client.patch("/budget", json={"monthly_budget_usd": -1.0})

    assert response.status_code == 422
