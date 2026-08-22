"""Unit tests for services/health_alerts.py (Sprint 15, ADR-020).

Mocks at the import site inside `services/health_alerts.py`, same
convention `test_alerting.py` uses — no real HTTP touched here.
"""

from __future__ import annotations

from ai_etl.services import health_alerts


def _pipeline(consecutive_failures: int = 3, last_error: str | None = "connection refused") -> dict:
    return {
        "id": "pl-1",
        "tenant_id": "tenant-a",
        "name": "Nightly Postgres sync",
        "consecutive_failures": consecutive_failures,
        "last_error": last_error,
    }


def test_build_health_alert_includes_name_and_failure_count() -> None:
    alert = health_alerts.build_health_alert(_pipeline(consecutive_failures=3))

    assert "Nightly Postgres sync" in alert["subject"]
    assert "3" in alert["subject"]
    assert "connection refused" in alert["text"]
    assert "connection refused" in alert["html"]
    assert alert["slack_blocks"][0]["type"] == "section"


def test_build_health_alert_handles_missing_last_error() -> None:
    alert = health_alerts.build_health_alert(_pipeline(last_error=None))
    assert "nenhum detalhe de erro registrado" in alert["text"]


def test_check_and_alert_pipeline_health_calls_every_channel(mocker) -> None:
    mock_email = mocker.patch("ai_etl.services.health_alerts.send_email_digest", return_value=True)
    mock_slack = mocker.patch("ai_etl.services.health_alerts.send_slack_digest", return_value=False)
    mock_teams = mocker.patch("ai_etl.services.health_alerts.send_teams_digest", return_value=False)
    mock_chat = mocker.patch(
        "ai_etl.services.health_alerts.send_google_chat_digest", return_value=False
    )

    result = health_alerts.check_and_alert_pipeline_health(_pipeline())

    mock_email.assert_called_once()
    mock_slack.assert_called_once()
    mock_teams.assert_called_once()
    mock_chat.assert_called_once()
    assert result == {
        "email_sent": True,
        "slack_sent": False,
        "teams_sent": False,
        "google_chat_sent": False,
    }
