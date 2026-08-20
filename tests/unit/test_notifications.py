"""Unit tests for services/notifications.py (Sprint 14, ADR-018).

`httpx.post` is monkeypatched here — no real network call, no real Resend/
Slack credentials in this environment (see ADR-018 Decision 5's "not tested
against a real provider" note). These tests verify the request shape and the
not-configured/failure control flow, not a real delivery.
"""

from __future__ import annotations

import pytest

from ai_etl.services import notifications


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("boom", request=None, response=self)  # type: ignore[arg-type]


def test_send_email_digest_returns_false_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("AI_ETL_ALERT_EMAIL_FROM", raising=False)
    monkeypatch.delenv("AI_ETL_ALERT_EMAIL_TO", raising=False)

    assert notifications.send_email_digest("subj", "<p>html</p>", "text") is False


def test_send_email_digest_returns_false_when_recipients_list_is_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_123")
    monkeypatch.setenv("AI_ETL_ALERT_EMAIL_FROM", "alerts@example.com")
    monkeypatch.setenv("AI_ETL_ALERT_EMAIL_TO", "   ,  ,")

    assert notifications.send_email_digest("subj", "<p>html</p>", "text") is False


def test_send_email_digest_posts_correct_payload_and_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_123")
    monkeypatch.setenv("AI_ETL_ALERT_EMAIL_FROM", "alerts@example.com")
    monkeypatch.setenv("AI_ETL_ALERT_EMAIL_TO", "a@example.com, b@example.com")

    captured: dict = {}

    def _fake_post(url, json=None, headers=None, timeout=None):  # noqa: ANN001
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse(200)

    monkeypatch.setattr(notifications.httpx, "post", _fake_post)

    result = notifications.send_email_digest("Subject", "<p>html</p>", "plain text")

    assert result is True
    assert captured["url"] == notifications.RESEND_API_URL
    assert captured["json"]["from"] == "alerts@example.com"
    assert captured["json"]["to"] == ["a@example.com", "b@example.com"]
    assert captured["json"]["subject"] == "Subject"
    assert captured["headers"]["Authorization"] == "Bearer re_123"


def test_send_email_digest_returns_false_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_123")
    monkeypatch.setenv("AI_ETL_ALERT_EMAIL_FROM", "alerts@example.com")
    monkeypatch.setenv("AI_ETL_ALERT_EMAIL_TO", "a@example.com")

    def _fake_post(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return _FakeResponse(500)

    monkeypatch.setattr(notifications.httpx, "post", _fake_post)

    assert notifications.send_email_digest("s", "h", "t") is False


def test_send_slack_digest_returns_false_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert notifications.send_slack_digest([], "fallback") is False


def test_send_slack_digest_posts_blocks_and_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X")
    captured: dict = {}

    def _fake_post(url, json=None, timeout=None):  # noqa: ANN001
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200)

    monkeypatch.setattr(notifications.httpx, "post", _fake_post)

    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]
    result = notifications.send_slack_digest(blocks, "fallback text")

    assert result is True
    assert captured["url"] == "https://hooks.slack.com/services/T/B/X"
    assert captured["json"]["blocks"] == blocks
    assert captured["json"]["text"] == "fallback text"


def test_send_slack_digest_returns_false_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X")

    def _fake_post(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return _FakeResponse(404)

    monkeypatch.setattr(notifications.httpx, "post", _fake_post)

    assert notifications.send_slack_digest([], "fallback") is False
