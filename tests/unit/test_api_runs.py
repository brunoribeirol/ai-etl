"""Tests for `/runs` endpoints (ADR-011).

Mocks at the import site inside `api/routers/runs.py`, same convention
`test_extractor.py` etc. already use — and overrides the auth dependency via
FastAPI's `dependency_overrides` rather than minting real JWTs here (that's
already covered by `test_api_deps.py`).
"""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ai_etl.api.deps import get_current_auth_context
from ai_etl.api.main import app
from ai_etl.services.execution_queue import BudgetExceededError, RateLimitExceededError


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


def test_health_needs_no_auth(client: TestClient) -> None:
    app.dependency_overrides.clear()  # health must work with zero auth wired up
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_runs_returns_history_rows(client: TestClient, mocker) -> None:
    df = pd.DataFrame({"run_id": ["run-1"], "status": ["completed"], "cost_usd": [0.001]})
    mock_load_history = mocker.patch("ai_etl.api.routers.runs.load_history", return_value=df)

    response = client.get("/runs")

    assert response.status_code == 200
    assert response.json() == [{"run_id": "run-1", "status": "completed", "cost_usd": 0.001}]
    mock_load_history.assert_called_once_with(limit=20, tenant_id="tenant-a")


def test_list_runs_serializes_nan_as_null(client: TestClient, mocker) -> None:
    df = pd.DataFrame({"run_id": ["run-1"], "cost_usd": [float("nan")]})
    mocker.patch("ai_etl.api.routers.runs.load_history", return_value=df)

    response = client.get("/runs")

    assert response.json()[0]["cost_usd"] is None


def test_get_run_returns_serialized_result(client: TestClient, mocker) -> None:
    result = {
        "bronze": None,
        "state": {"status": "completed", "transformed_data": pd.DataFrame({"a": [1, 2]})},
        "gold": [],
        "science": [],
        "advisor": {},
        "question": "",
        "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }
    mock_load = mocker.patch("ai_etl.api.routers.runs.load_full_result", return_value=result)

    response = client.get("/runs/run-1")

    assert response.status_code == 200
    body = response.json()
    assert body["state"]["status"] == "completed"
    assert body["state"]["transformed_data"] == [{"a": 1}, {"a": 2}]
    mock_load.assert_called_once_with("run-1", log_dir=mocker.ANY, tenant_id="tenant-a")


def test_get_run_404_when_unknown(client: TestClient, mocker) -> None:
    mocker.patch("ai_etl.api.routers.runs.load_full_result", return_value=None)

    response = client.get("/runs/no-such-run")

    assert response.status_code == 404


def test_get_status_wraps_execution_queue(client: TestClient, mocker) -> None:
    mocker.patch(
        "ai_etl.api.routers.runs.get_task_status",
        return_value={"state": "SUCCESS", "ready": True, "result": {"status": "completed"}},
    )

    response = client.get("/runs/task-123/status")

    assert response.status_code == 200
    assert response.json()["state"] == "SUCCESS"


def test_create_run_with_manual_spec(client: TestClient, mocker) -> None:
    mock_enqueue = mocker.patch("ai_etl.api.routers.runs.enqueue_analysis", return_value="task-abc")

    response = client.post("/runs", data={"manual_spec": "load sales.csv"})

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-abc"}
    mock_enqueue.assert_called_once()
    assert mock_enqueue.call_args.args[0] == "load sales.csv"


def test_create_run_requires_file_or_spec(client: TestClient) -> None:
    response = client.post("/runs", data={})
    assert response.status_code == 400


def test_create_run_with_llm_override(client: TestClient, mocker) -> None:
    """Gap-closing fix (2026-08-25 audit, Wave 4) — the avulso `POST /runs`
    flow's `ModelPicker` selection (`llm_provider_override`/
    `llm_model_override` form fields) must reach `enqueue_analysis`."""
    mock_enqueue = mocker.patch("ai_etl.api.routers.runs.enqueue_analysis", return_value="task-abc")

    response = client.post(
        "/runs",
        data={
            "manual_spec": "load sales.csv",
            "llm_provider_override": "anthropic",
            "llm_model_override": "claude-sonnet-5",
        },
    )

    assert response.status_code == 200
    mock_enqueue.assert_called_once()
    assert mock_enqueue.call_args.kwargs["llm_provider_override"] == "anthropic"
    assert mock_enqueue.call_args.kwargs["llm_model_override"] == "claude-sonnet-5"


def test_create_run_llm_override_must_be_set_together(client: TestClient, mocker) -> None:
    mocker.patch("ai_etl.api.routers.runs.enqueue_analysis", return_value="task-abc")

    response = client.post(
        "/runs",
        data={"manual_spec": "load sales.csv", "llm_provider_override": "anthropic"},
    )

    assert response.status_code == 400


def test_create_run_rejects_unsupported_llm_override(client: TestClient, mocker) -> None:
    mocker.patch("ai_etl.api.routers.runs.enqueue_analysis", return_value="task-abc")

    response = client.post(
        "/runs",
        data={
            "manual_spec": "load sales.csv",
            "llm_provider_override": "anthropic",
            "llm_model_override": "not-a-real-model",
        },
    )

    assert response.status_code == 400


def test_create_run_rate_limited(client: TestClient, mocker) -> None:
    mocker.patch(
        "ai_etl.api.routers.runs.enqueue_analysis",
        side_effect=RateLimitExceededError("too many runs"),
    )

    response = client.post("/runs", data={"manual_spec": "load sales.csv"})

    assert response.status_code == 429


def test_create_run_over_budget(client: TestClient, mocker) -> None:
    """Sprint 29 (ADR-019): a tenant over their monthly budget cap gets a 402,
    distinct from the 429 rate-limit case above."""
    mocker.patch(
        "ai_etl.api.routers.runs.enqueue_analysis",
        side_effect=BudgetExceededError("budget exceeded"),
    )

    response = client.post("/runs", data={"manual_spec": "load sales.csv"})

    assert response.status_code == 402


def test_create_run_with_uploaded_csv(client: TestClient, mocker) -> None:
    mock_enqueue = mocker.patch("ai_etl.api.routers.runs.enqueue_analysis", return_value="task-xyz")
    csv_bytes = b"name,price\nA,1.0\n"

    response = client.post(
        "/runs",
        data={"business_question": "Quais produtos vendem mais?"},
        files={"file": ("sales.csv", csv_bytes, "text/csv")},
    )

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-xyz"}
    mock_enqueue.assert_called_once()
    spec = mock_enqueue.call_args.args[0]
    assert "name, price" in spec


# ---------------------------------------------------------------------------
# Sprint 27 (ADR-028) — write-approval gate
# ---------------------------------------------------------------------------


def test_get_pending_approvals_returns_queue(client: TestClient, mocker) -> None:
    mock_list = mocker.patch(
        "ai_etl.api.routers.runs.list_pending_approvals",
        return_value=[{"run_id": "run-1", "pipeline_name": "A"}],
    )

    response = client.get("/runs/pending-approval")

    assert response.status_code == 200
    assert response.json() == [{"run_id": "run-1", "pipeline_name": "A"}]
    mock_list.assert_called_once_with("tenant-a")


def test_approve_run_writes_and_returns_summary(client: TestClient, mocker) -> None:
    mock_resume = mocker.patch(
        "ai_etl.api.routers.runs.resume_pending_load",
        return_value={"run_id": "run-1", "status": "completed", "error": None, "load_result": {}},
    )

    response = client.post("/runs/run-1/approve")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    mock_resume.assert_called_once_with("run-1", "tenant-a", run_dir=mocker.ANY)


def test_approve_run_unknown_returns_404(client: TestClient, mocker) -> None:
    mocker.patch(
        "ai_etl.api.routers.runs.resume_pending_load",
        side_effect=ValueError("Run 'run-1' not found."),
    )

    response = client.post("/runs/run-1/approve")

    assert response.status_code == 404


def test_approve_run_wrong_status_returns_409(client: TestClient, mocker) -> None:
    mocker.patch(
        "ai_etl.api.routers.runs.resume_pending_load",
        side_effect=ValueError(
            "Run 'run-1' is not awaiting approval (current status: 'completed')."
        ),
    )

    response = client.post("/runs/run-1/approve")

    assert response.status_code == 409


def test_reject_run_marks_failed(client: TestClient, mocker) -> None:
    mock_reject = mocker.patch(
        "ai_etl.api.routers.runs.reject_pending_load",
        return_value={
            "run_id": "run-1",
            "status": "failed",
            "error": "Rejected by operator: too risky",
            "load_result": None,
        },
    )

    response = client.post("/runs/run-1/reject", json={"reason": "too risky"})

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    mock_reject.assert_called_once_with("run-1", "tenant-a", run_dir=mocker.ANY, reason="too risky")


def test_reject_run_defaults_reason_to_empty_string(client: TestClient, mocker) -> None:
    mock_reject = mocker.patch(
        "ai_etl.api.routers.runs.reject_pending_load",
        return_value={"run_id": "run-1", "status": "failed", "error": "Rejected by operator."},
    )

    response = client.post("/runs/run-1/reject", json={})

    assert response.status_code == 200
    mock_reject.assert_called_once_with("run-1", "tenant-a", run_dir=mocker.ANY, reason="")
