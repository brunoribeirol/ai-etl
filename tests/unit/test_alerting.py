"""Unit tests for services/alerting.py (Sprint 14, ADR-018).

Mocks at the import site inside `services/alerting.py`, same convention
`test_scheduler.py` uses — no real DB/HTTP touched here.
"""

from __future__ import annotations

from ai_etl.services import alerting

_ZERO_TOKENS = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _pipeline(drift_threshold_pct: float = 20.0) -> dict:
    return {
        "id": "pl-1",
        "tenant_id": "tenant-a",
        "name": "Nightly Postgres sync",
        "drift_threshold_pct": drift_threshold_pct,
    }


def _advisor_result() -> dict:
    return {
        "recommendations": [],
        "summary": "resumo",
        "error": None,
        "tokens": dict(_ZERO_TOKENS),
    }


def test_returns_none_when_no_previous_run(mocker) -> None:
    mocker.patch("ai_etl.services.alerting.get_previous_completed_run", return_value=None)
    mock_notify = mocker.patch("ai_etl.services.alerting.send_email_digest")

    result = alerting.check_drift_and_notify(
        pipeline=_pipeline(),
        run_id="run-2",
        rows_loaded=1000,
        tokens={"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
        science_results=[],
        advisor_result=_advisor_result(),
        business_question="",
    )

    assert result is None
    mock_notify.assert_not_called()


def test_no_drift_returns_triggered_false_and_sends_nothing(mocker) -> None:
    mocker.patch(
        "ai_etl.services.alerting.get_previous_completed_run",
        return_value={
            "run_id": "run-1",
            "rows_loaded": 1000,
            "cost_usd": 0.01,
            "total_tokens": 500,
            "timestamp": None,
        },
    )
    mocker.patch("ai_etl.services.alerting.load_full_result", return_value=None)
    mocker.patch("ai_etl.services.alerting.get_model_name", return_value="gpt-4o-mini")
    mocker.patch("ai_etl.services.alerting.compute_cost_usd", return_value=0.0102)
    mock_email = mocker.patch("ai_etl.services.alerting.send_email_digest")
    mock_slack = mocker.patch("ai_etl.services.alerting.send_slack_digest")
    mock_teams = mocker.patch("ai_etl.services.alerting.send_teams_digest")
    mock_google_chat = mocker.patch("ai_etl.services.alerting.send_google_chat_digest")

    result = alerting.check_drift_and_notify(
        pipeline=_pipeline(),
        run_id="run-2",
        rows_loaded=1010,  # 1% change, under the 20% threshold
        tokens={"input_tokens": 250, "output_tokens": 260, "total_tokens": 510},
        science_results=[],
        advisor_result=_advisor_result(),
        business_question="",
    )

    assert result is not None
    assert result["triggered"] is False
    assert result["email_sent"] is False
    assert result["slack_sent"] is False
    assert result["teams_sent"] is False
    assert result["google_chat_sent"] is False
    mock_email.assert_not_called()
    mock_slack.assert_not_called()
    mock_teams.assert_not_called()
    mock_google_chat.assert_not_called()


def test_drift_over_threshold_triggers_digest_delivery(mocker) -> None:
    mocker.patch(
        "ai_etl.services.alerting.get_previous_completed_run",
        return_value={
            "run_id": "run-1",
            "rows_loaded": 1000,
            "cost_usd": 0.01,
            "total_tokens": 500,
            "timestamp": None,
        },
    )
    mocker.patch("ai_etl.services.alerting.load_full_result", return_value=None)
    mocker.patch("ai_etl.services.alerting.get_model_name", return_value="gpt-4o-mini")
    mocker.patch("ai_etl.services.alerting.compute_cost_usd", return_value=0.02)
    mocker.patch(
        "ai_etl.services.alerting.resolve_pipeline_notification_override",
        return_value=(None, None),
    )
    mock_email = mocker.patch("ai_etl.services.alerting.send_email_digest", return_value=True)
    mock_slack = mocker.patch("ai_etl.services.alerting.send_slack_digest", return_value=True)
    mock_teams = mocker.patch("ai_etl.services.alerting.send_teams_digest", return_value=True)
    mock_google_chat = mocker.patch(
        "ai_etl.services.alerting.send_google_chat_digest", return_value=True
    )

    result = alerting.check_drift_and_notify(
        pipeline=_pipeline(drift_threshold_pct=20.0),
        run_id="run-2",
        rows_loaded=2000,  # 100% change over 1000 — well over threshold
        tokens={"input_tokens": 250, "output_tokens": 260, "total_tokens": 510},
        science_results=[],
        advisor_result=_advisor_result(),
        business_question="Quais produtos mais vendem?",
    )

    assert result is not None
    assert result["triggered"] is True
    assert result["email_sent"] is True
    assert result["slack_sent"] is True
    assert result["teams_sent"] is True
    assert result["google_chat_sent"] is True
    assert any(f["name"] == "rows_loaded" and f["triggered"] for f in result["findings"])
    mock_email.assert_called_once()
    mock_slack.assert_called_once()
    mock_teams.assert_called_once()
    mock_google_chat.assert_called_once()


def test_uses_default_threshold_when_pipeline_value_missing(mocker) -> None:
    mocker.patch(
        "ai_etl.services.alerting.get_previous_completed_run",
        return_value={
            "run_id": "run-1",
            "rows_loaded": 1000,
            "cost_usd": None,
            "total_tokens": None,
            "timestamp": None,
        },
    )
    mocker.patch("ai_etl.services.alerting.load_full_result", return_value=None)
    mocker.patch("ai_etl.services.alerting.get_model_name", return_value="gpt-4o-mini")
    mocker.patch("ai_etl.services.alerting.compute_cost_usd", return_value=None)
    mocker.patch("ai_etl.services.alerting.send_email_digest", return_value=False)
    mocker.patch("ai_etl.services.alerting.send_slack_digest", return_value=False)

    pipeline = _pipeline()
    del pipeline["drift_threshold_pct"]

    result = alerting.check_drift_and_notify(
        pipeline=pipeline,
        run_id="run-2",
        rows_loaded=1005,  # 0.5% change — under the 20.0 default
        tokens={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        science_results=[],
        advisor_result=_advisor_result(),
        business_question="",
    )

    assert result is not None
    assert result["triggered"] is False


def test_science_metrics_are_compared_when_present(mocker) -> None:
    mocker.patch(
        "ai_etl.services.alerting.get_previous_completed_run",
        return_value={
            "run_id": "run-1",
            "rows_loaded": 1000,
            "cost_usd": 0.01,
            "total_tokens": 500,
            "timestamp": None,
        },
    )
    mocker.patch(
        "ai_etl.services.alerting.load_full_result",
        return_value={
            "science": [
                {
                    "task_question": "Prever churn",
                    "model_info": {"metrics": {"rmse": 10.0, "model_name": "LinearRegression"}},
                }
            ]
        },
    )
    mocker.patch("ai_etl.services.alerting.get_model_name", return_value="gpt-4o-mini")
    mocker.patch("ai_etl.services.alerting.compute_cost_usd", return_value=0.01)
    mocker.patch(
        "ai_etl.services.alerting.resolve_pipeline_notification_override",
        return_value=(None, None),
    )
    mocker.patch("ai_etl.services.alerting.send_email_digest", return_value=True)
    mocker.patch("ai_etl.services.alerting.send_slack_digest", return_value=True)
    mocker.patch("ai_etl.services.alerting.send_teams_digest", return_value=True)
    mocker.patch("ai_etl.services.alerting.send_google_chat_digest", return_value=True)

    current_science = [
        {
            "task_question": "Prever churn",
            "model_info": {"metrics": {"rmse": 30.0}},  # 200% worse
        }
    ]

    result = alerting.check_drift_and_notify(
        pipeline=_pipeline(),
        run_id="run-2",
        rows_loaded=1000,
        tokens={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        science_results=current_science,  # type: ignore[arg-type]
        advisor_result=_advisor_result(),
        business_question="",
    )

    assert result is not None
    assert result["triggered"] is True
    names = {f["name"] for f in result["findings"] if f["triggered"]}
    assert any("rmse" in name for name in names)


def test_drift_notify_forwards_override_only_to_matching_channel(mocker) -> None:
    """Sprint 37 (ADR-034) — a configured `slack` override reaches
    `send_slack_digest` as `override_webhook_url`; the other three channels get
    `None` and fall back to their own global env var, unaffected."""
    mocker.patch(
        "ai_etl.services.alerting.get_previous_completed_run",
        return_value={
            "run_id": "run-1",
            "rows_loaded": 1000,
            "cost_usd": 0.01,
            "total_tokens": 500,
            "timestamp": None,
        },
    )
    mocker.patch("ai_etl.services.alerting.load_full_result", return_value=None)
    mocker.patch("ai_etl.services.alerting.get_model_name", return_value="gpt-4o-mini")
    mocker.patch("ai_etl.services.alerting.compute_cost_usd", return_value=0.02)
    mocker.patch(
        "ai_etl.services.alerting.resolve_pipeline_notification_override",
        return_value=("slack", "https://hooks.slack.com/services/TENANT-B"),
    )
    mock_email = mocker.patch("ai_etl.services.alerting.send_email_digest", return_value=True)
    mock_slack = mocker.patch("ai_etl.services.alerting.send_slack_digest", return_value=True)
    mock_teams = mocker.patch("ai_etl.services.alerting.send_teams_digest", return_value=True)
    mock_google_chat = mocker.patch(
        "ai_etl.services.alerting.send_google_chat_digest", return_value=True
    )

    alerting.check_drift_and_notify(
        pipeline=_pipeline(drift_threshold_pct=20.0),
        run_id="run-2",
        rows_loaded=2000,
        tokens={"input_tokens": 250, "output_tokens": 260, "total_tokens": 510},
        science_results=[],
        advisor_result=_advisor_result(),
        business_question="",
    )

    assert mock_email.call_args.kwargs["override_recipients"] is None
    assert (
        mock_slack.call_args.kwargs["override_webhook_url"]
        == "https://hooks.slack.com/services/TENANT-B"
    )
    assert mock_teams.call_args.kwargs["override_webhook_url"] is None
    assert mock_google_chat.call_args.kwargs["override_webhook_url"] is None
