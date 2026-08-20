"""Unit tests for services/scheduler.py's Celery beat task (Sprint 13, ADR-016).

Mocks at the import site inside `services/scheduler.py`, same convention
`test_api_runs.py` uses for `api/routers/runs.py` — no real Celery/Redis/DB
touched here (that belongs in integration tests).
"""

from datetime import datetime, timezone

from ai_etl.services import scheduler
from ai_etl.services.execution_queue import RateLimitExceededError


def _due_pipeline(pipeline_id: str = "pl-1", tenant_id: str = "tenant-a") -> dict:
    return {
        "id": pipeline_id,
        "tenant_id": tenant_id,
        "spec": "Read schema.orders from postgres",
        "business_question": "",
        "cron_schedule": "* * * * *",
        "next_run_at": datetime.now(tz=timezone.utc),
    }


def test_fires_every_due_pipeline(mocker) -> None:
    mocker.patch(
        "ai_etl.services.scheduler.list_due_pipelines",
        return_value=[_due_pipeline("pl-1"), _due_pipeline("pl-2")],
    )
    mock_enqueue = mocker.patch(
        "ai_etl.services.scheduler.enqueue_analysis", return_value="task-abc"
    )
    mock_mark = mocker.patch("ai_etl.services.scheduler.mark_pipeline_fired")

    result = scheduler.check_scheduled_pipelines_task()

    assert mock_enqueue.call_count == 2
    assert mock_mark.call_count == 2
    assert set(result["fired"]) == {"pl-1", "pl-2"}
    assert result["skipped"] == []


def test_no_due_pipelines_is_a_no_op(mocker) -> None:
    mocker.patch("ai_etl.services.scheduler.list_due_pipelines", return_value=[])
    mock_enqueue = mocker.patch("ai_etl.services.scheduler.enqueue_analysis")

    result = scheduler.check_scheduled_pipelines_task()

    mock_enqueue.assert_not_called()
    assert result == {"fired": [], "skipped": []}


def test_rate_limited_pipeline_is_skipped_not_raised(mocker) -> None:
    """One tenant over their cap must not stop the tick — the exception is
    caught, not propagated, and next_run_at is left untouched (no
    mark_pipeline_fired call) so it's retried on the next tick."""
    mocker.patch(
        "ai_etl.services.scheduler.list_due_pipelines", return_value=[_due_pipeline("pl-1")]
    )
    mocker.patch(
        "ai_etl.services.scheduler.enqueue_analysis",
        side_effect=RateLimitExceededError("over cap"),
    )
    mock_mark = mocker.patch("ai_etl.services.scheduler.mark_pipeline_fired")

    result = scheduler.check_scheduled_pipelines_task()

    mock_mark.assert_not_called()
    assert result == {"fired": [], "skipped": ["pl-1"]}


def test_one_pipeline_failing_does_not_block_the_others(mocker) -> None:
    def _enqueue_side_effect(spec, question, run_dir, tenant_id):
        if tenant_id == "tenant-broken":
            raise RuntimeError("boom")
        return "task-ok"

    mocker.patch(
        "ai_etl.services.scheduler.list_due_pipelines",
        return_value=[
            _due_pipeline("pl-broken", tenant_id="tenant-broken"),
            _due_pipeline("pl-ok", tenant_id="tenant-a"),
        ],
    )
    mocker.patch("ai_etl.services.scheduler.enqueue_analysis", side_effect=_enqueue_side_effect)
    mocker.patch("ai_etl.services.scheduler.mark_pipeline_fired")

    result = scheduler.check_scheduled_pipelines_task()

    assert result["fired"] == ["pl-ok"]
    assert result["skipped"] == ["pl-broken"]


def test_enqueue_analysis_called_with_pipeline_spec_and_question(mocker) -> None:
    mocker.patch(
        "ai_etl.services.scheduler.list_due_pipelines",
        return_value=[
            {
                "id": "pl-1",
                "tenant_id": "tenant-a",
                "spec": "Read schema.orders from postgres",
                "business_question": "Top products?",
                "cron_schedule": "* * * * *",
                "next_run_at": datetime.now(tz=timezone.utc),
            }
        ],
    )
    mock_enqueue = mocker.patch(
        "ai_etl.services.scheduler.enqueue_analysis", return_value="task-abc"
    )
    mocker.patch("ai_etl.services.scheduler.mark_pipeline_fired")

    scheduler.check_scheduled_pipelines_task()

    mock_enqueue.assert_called_once_with(
        "Read schema.orders from postgres",
        "Top products?",
        run_dir=scheduler.RUNS_DIR,
        tenant_id="tenant-a",
    )
