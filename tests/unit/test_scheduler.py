"""Unit tests for services/scheduler.py's Celery beat task (Sprint 13, ADR-016).

Mocks at the import site inside `services/scheduler.py`, same convention
`test_api_runs.py` uses for `api/routers/runs.py` — no real Celery/Redis/DB
touched here (that belongs in integration tests). `claim_due_pipeline`'s
actual compare-and-swap semantics are covered for real (in-memory SQLite)
in `test_saved_pipelines_db.py`; here it's mocked to exercise the task's own
control flow (claim win/loss, release-on-failure, drift-free next-run base).
"""

from datetime import datetime, timedelta, timezone

from ai_etl.services import scheduler
from ai_etl.services.execution_queue import BudgetExceededError, RateLimitExceededError


def _due_pipeline(
    pipeline_id: str = "pl-1", tenant_id: str = "tenant-a", next_run_at: datetime | None = None
) -> dict:
    return {
        "id": pipeline_id,
        "tenant_id": tenant_id,
        "spec": "Read schema.orders from postgres",
        "business_question": "",
        "cron_schedule": "* * * * *",
        "next_run_at": next_run_at or datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc),
    }


def test_fires_every_due_pipeline(mocker) -> None:
    mocker.patch(
        "ai_etl.services.scheduler.list_due_pipelines",
        return_value=[_due_pipeline("pl-1"), _due_pipeline("pl-2")],
    )
    mocker.patch("ai_etl.services.scheduler.claim_due_pipeline", return_value=True)
    mock_enqueue = mocker.patch(
        "ai_etl.services.scheduler.enqueue_analysis", return_value="task-abc"
    )
    mock_record = mocker.patch("ai_etl.services.scheduler.record_pipeline_run")
    mock_release = mocker.patch("ai_etl.services.scheduler.release_pipeline_claim")

    result = scheduler.check_scheduled_pipelines_task()

    assert mock_enqueue.call_count == 2
    assert mock_record.call_count == 2
    mock_release.assert_not_called()
    assert set(result["fired"]) == {"pl-1", "pl-2"}
    assert result["skipped"] == []


def test_no_due_pipelines_is_a_no_op(mocker) -> None:
    mocker.patch("ai_etl.services.scheduler.list_due_pipelines", return_value=[])
    mock_enqueue = mocker.patch("ai_etl.services.scheduler.enqueue_analysis")

    result = scheduler.check_scheduled_pipelines_task()

    mock_enqueue.assert_not_called()
    assert result == {"fired": [], "skipped": []}


def test_losing_the_claim_skips_without_enqueueing(mocker) -> None:
    """Concurrency guard (Sprint 13 code review fix): an overlapping tick
    that lost the `claim_due_pipeline` race must never call
    `enqueue_analysis` — that's exactly the duplicate-fire this claim
    exists to prevent."""
    mocker.patch(
        "ai_etl.services.scheduler.list_due_pipelines", return_value=[_due_pipeline("pl-1")]
    )
    mocker.patch("ai_etl.services.scheduler.claim_due_pipeline", return_value=False)
    mock_enqueue = mocker.patch("ai_etl.services.scheduler.enqueue_analysis")

    result = scheduler.check_scheduled_pipelines_task()

    mock_enqueue.assert_not_called()
    assert result == {"fired": [], "skipped": ["pl-1"]}


def test_rate_limited_pipeline_releases_its_claim(mocker) -> None:
    """One tenant over their cap must not stop the tick, and the claim it
    won must be released so the pipeline is retried next tick, not
    silently skipped for a full cron period."""
    mocker.patch(
        "ai_etl.services.scheduler.list_due_pipelines", return_value=[_due_pipeline("pl-1")]
    )
    mocker.patch("ai_etl.services.scheduler.claim_due_pipeline", return_value=True)
    mocker.patch(
        "ai_etl.services.scheduler.enqueue_analysis",
        side_effect=RateLimitExceededError("over cap"),
    )
    mock_release = mocker.patch("ai_etl.services.scheduler.release_pipeline_claim")
    mock_record = mocker.patch("ai_etl.services.scheduler.record_pipeline_run")

    result = scheduler.check_scheduled_pipelines_task()

    mock_release.assert_called_once()
    mock_record.assert_not_called()
    assert result == {"fired": [], "skipped": ["pl-1"]}


def test_budget_exceeded_pipeline_releases_its_claim(mocker) -> None:
    """Sprint 29 (ADR-017): a tenant over their monthly budget cap must not
    stop the tick, and the claim it won must be released so the pipeline is
    retried next tick — same treatment as a rate-limited pipeline above."""
    mocker.patch(
        "ai_etl.services.scheduler.list_due_pipelines", return_value=[_due_pipeline("pl-1")]
    )
    mocker.patch("ai_etl.services.scheduler.claim_due_pipeline", return_value=True)
    mocker.patch(
        "ai_etl.services.scheduler.enqueue_analysis",
        side_effect=BudgetExceededError("over budget"),
    )
    mock_release = mocker.patch("ai_etl.services.scheduler.release_pipeline_claim")
    mock_record = mocker.patch("ai_etl.services.scheduler.record_pipeline_run")

    result = scheduler.check_scheduled_pipelines_task()

    mock_release.assert_called_once()
    mock_record.assert_not_called()
    assert result == {"fired": [], "skipped": ["pl-1"]}


def test_one_pipeline_failing_does_not_block_the_others(mocker) -> None:
    def _enqueue_side_effect(spec, question, run_dir, tenant_id, saved_pipeline_id=None):
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
    mocker.patch("ai_etl.services.scheduler.claim_due_pipeline", return_value=True)
    mocker.patch("ai_etl.services.scheduler.enqueue_analysis", side_effect=_enqueue_side_effect)
    mocker.patch("ai_etl.services.scheduler.release_pipeline_claim")
    mocker.patch("ai_etl.services.scheduler.record_pipeline_run")

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
                "next_run_at": datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc),
            }
        ],
    )
    mocker.patch("ai_etl.services.scheduler.claim_due_pipeline", return_value=True)
    mock_enqueue = mocker.patch(
        "ai_etl.services.scheduler.enqueue_analysis", return_value="task-abc"
    )
    mocker.patch("ai_etl.services.scheduler.record_pipeline_run")

    scheduler.check_scheduled_pipelines_task()

    mock_enqueue.assert_called_once_with(
        "Read schema.orders from postgres",
        "Top products?",
        run_dir=str(scheduler.RUNS_DIR),
        tenant_id="tenant-a",
        saved_pipeline_id="pl-1",
    )


def test_next_run_at_is_computed_from_scheduled_time_not_now(mocker) -> None:
    """Schedule-drift fix (Sprint 13 code review): the replacement
    `next_run_at` passed to `claim_due_pipeline` must be derived from the
    pipeline's own (past) `next_run_at`, not `datetime.now()` — otherwise a
    tick running late inherits and compounds that delay on every fire."""
    scheduled_time = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    # Simulate a tick running late: "now" is well past the scheduled time.
    mocker.patch(
        "ai_etl.services.scheduler.list_due_pipelines",
        return_value=[_due_pipeline("pl-1", next_run_at=scheduled_time)],
    )
    mock_claim = mocker.patch("ai_etl.services.scheduler.claim_due_pipeline", return_value=True)
    mocker.patch("ai_etl.services.scheduler.enqueue_analysis", return_value="task-abc")
    mocker.patch("ai_etl.services.scheduler.record_pipeline_run")

    scheduler.check_scheduled_pipelines_task()

    args = mock_claim.call_args.args
    assert args[0] == "pl-1"
    assert args[1] == scheduled_time
    # "* * * * *" (every minute) from a 12:00:00 base is exactly 12:01:00 —
    # never anything derived from wall-clock "now".
    assert args[2] == scheduled_time + timedelta(minutes=1)
