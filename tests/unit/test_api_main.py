"""Tests for top-level `/config` (`api/main.py`).

Same `dependency_overrides` convention as `test_api_runs.py`/`test_api_admin.py`
, overrides `get_current_auth_context` rather than minting real JWTs.
"""

import pytest
from fastapi.testclient import TestClient

from ai_etl.api.deps import get_current_auth_context
from ai_etl.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> None:
    yield
    app.dependency_overrides.clear()


def _override_auth(role: str) -> None:
    app.dependency_overrides[get_current_auth_context] = lambda: {
        "tenant_id": "tenant-a",
        "role": role,
        "user_id": "user-1",
    }


@pytest.mark.parametrize("role", ["viewer", "editor", "admin"])
def test_config_returns_resolved_role(client: TestClient, mocker, role: str) -> None:
    # Wave 6 (2026-08-25 admin panel/approval-gate UI plan) gap-closing fix:
    # the frontend previously had no way to know the caller's role at all;
    # `/config` is the single source of truth now (already fetched once per
    # page load by `(app)/layout.tsx` for the model badge).
    mocker.patch("ai_etl.api.main.get_model_name", return_value="gpt-4o-mini")
    _override_auth(role)

    response = client.get("/config")

    assert response.status_code == 200
    assert response.json() == {"model_name": "gpt-4o-mini", "role": role}


def test_config_requires_auth(client: TestClient) -> None:
    response = client.get("/config")
    assert response.status_code == 401


# --- Audit finding M8 (2026-09-09): /health depth ---


def test_health_is_shallow_and_always_ok(client: TestClient) -> None:
    """Liveness must not fail on a dead dependency: it decides restarts."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready_returns_200_when_dependencies_are_up(client: TestClient, mocker) -> None:
    mocker.patch("ai_etl.api.main.get_engine")  # .connect().__enter__().execute() all MagicMock
    mocker.patch("ai_etl.api.main.redis.Redis.from_url")  # .ping() MagicMock
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"database": "ok", "redis": "ok"}


def test_health_ready_returns_503_when_database_is_down(client: TestClient, mocker) -> None:
    mocker.patch("ai_etl.api.main.get_engine", side_effect=OSError("db unreachable"))
    mocker.patch("ai_etl.api.main.redis.Redis.from_url")
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["checks"]["database"].startswith("error:")
    assert body["checks"]["redis"] == "ok"
