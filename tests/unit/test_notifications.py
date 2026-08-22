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


def test_send_teams_digest_returns_false_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)
    assert notifications.send_teams_digest("subject", "body") is False


def test_send_teams_digest_posts_adaptive_card_and_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://prod-00.eastus.logic.azure.com/workflows/abc")
    captured: dict = {}

    def _fake_post(url, json=None, timeout=None):  # noqa: ANN001
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200)

    monkeypatch.setattr(notifications.httpx, "post", _fake_post)

    result = notifications.send_teams_digest("Subject", "Body text")

    assert result is True
    assert captured["url"] == "https://prod-00.eastus.logic.azure.com/workflows/abc"
    card = captured["json"]["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard"
    body_texts = [block["text"] for block in card["body"]]
    assert "Subject" in body_texts
    assert "Body text" in body_texts


def test_send_teams_digest_returns_false_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://prod-00.eastus.logic.azure.com/workflows/abc")

    def _fake_post(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return _FakeResponse(500)

    monkeypatch.setattr(notifications.httpx, "post", _fake_post)

    assert notifications.send_teams_digest("s", "b") is False


def test_send_google_chat_digest_returns_false_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_CHAT_WEBHOOK_URL", raising=False)
    assert notifications.send_google_chat_digest("body") is False


def test_send_google_chat_digest_posts_text_and_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "GOOGLE_CHAT_WEBHOOK_URL",
        "https://chat.googleapis.com/v1/spaces/AAA/messages?key=k&token=t",
    )
    captured: dict = {}

    def _fake_post(url, json=None, timeout=None):  # noqa: ANN001
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200)

    monkeypatch.setattr(notifications.httpx, "post", _fake_post)

    result = notifications.send_google_chat_digest("Body text")

    assert result is True
    assert captured["url"] == "https://chat.googleapis.com/v1/spaces/AAA/messages?key=k&token=t"
    assert captured["json"] == {"text": "Body text"}


def test_send_google_chat_digest_returns_false_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "GOOGLE_CHAT_WEBHOOK_URL",
        "https://chat.googleapis.com/v1/spaces/AAA/messages?key=k&token=t",
    )

    def _fake_post(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return _FakeResponse(403)

    monkeypatch.setattr(notifications.httpx, "post", _fake_post)

    assert notifications.send_google_chat_digest("body") is False


# Sprint 37 (ADR-034) — per-pipeline notification destination override.


def test_send_email_digest_override_recipients_takes_precedence_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_123")
    monkeypatch.setenv("AI_ETL_ALERT_EMAIL_FROM", "alerts@example.com")
    monkeypatch.setenv("AI_ETL_ALERT_EMAIL_TO", "global@example.com")
    captured: dict = {}

    def _fake_post(url, json=None, headers=None, timeout=None):  # noqa: ANN001
        captured["json"] = json
        return _FakeResponse(200)

    monkeypatch.setattr(notifications.httpx, "post", _fake_post)

    result = notifications.send_email_digest(
        "s", "h", "t", override_recipients="tenant-b@example.com"
    )

    assert result is True
    assert captured["json"]["to"] == ["tenant-b@example.com"]


def test_send_email_digest_override_blank_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_123")
    monkeypatch.setenv("AI_ETL_ALERT_EMAIL_FROM", "alerts@example.com")
    monkeypatch.setenv("AI_ETL_ALERT_EMAIL_TO", "global@example.com")

    assert notifications.send_email_digest("s", "h", "t", override_recipients="   ,  ") is False


def test_send_slack_digest_override_webhook_takes_precedence_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/GLOBAL")
    captured: dict = {}

    def _fake_post(url, json=None, timeout=None):  # noqa: ANN001
        captured["url"] = url
        return _FakeResponse(200)

    monkeypatch.setattr(notifications.httpx, "post", _fake_post)

    result = notifications.send_slack_digest(
        [], "fallback", override_webhook_url="https://hooks.slack.com/services/TENANT-B"
    )

    assert result is True
    assert captured["url"] == "https://hooks.slack.com/services/TENANT-B"


def test_send_teams_digest_override_webhook_takes_precedence_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://global.example.com/workflows/global")
    captured: dict = {}

    def _fake_post(url, json=None, timeout=None):  # noqa: ANN001
        captured["url"] = url
        return _FakeResponse(200)

    monkeypatch.setattr(notifications.httpx, "post", _fake_post)

    result = notifications.send_teams_digest(
        "s", "b", override_webhook_url="https://tenant-b.example.com/workflows/x"
    )

    assert result is True
    assert captured["url"] == "https://tenant-b.example.com/workflows/x"


def test_send_google_chat_digest_override_webhook_takes_precedence_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "GOOGLE_CHAT_WEBHOOK_URL", "https://chat.googleapis.com/v1/spaces/GLOBAL/messages"
    )
    captured: dict = {}

    def _fake_post(url, json=None, timeout=None):  # noqa: ANN001
        captured["url"] = url
        return _FakeResponse(200)

    monkeypatch.setattr(notifications.httpx, "post", _fake_post)

    result = notifications.send_google_chat_digest(
        "b", override_webhook_url="https://chat.googleapis.com/v1/spaces/TENANT-B/messages"
    )

    assert result is True
    assert captured["url"] == "https://chat.googleapis.com/v1/spaces/TENANT-B/messages"


def test_resolve_pipeline_notification_override_returns_none_none_when_no_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ai_etl.audit.db.get_saved_pipeline_notification_target", lambda pid, tid: None
    )

    assert notifications.resolve_pipeline_notification_override("pl-1", "tenant-a") == (None, None)


def test_resolve_pipeline_notification_override_returns_none_none_when_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ai_etl.audit.db.get_saved_pipeline_notification_target",
        lambda pid, tid: {
            "notification_channel": "slack",
            "notification_target_ciphertext": "cipher",
            "notification_active": False,
        },
    )

    assert notifications.resolve_pipeline_notification_override("pl-1", "tenant-a") == (None, None)


def test_resolve_pipeline_notification_override_decrypts_configured_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ai_etl.audit.db.get_saved_pipeline_notification_target",
        lambda pid, tid: {
            "notification_channel": "slack",
            "notification_target_ciphertext": "cipher",
            "notification_active": True,
        },
    )
    monkeypatch.setattr(notifications, "decrypt_value", lambda ct: "https://hooks.slack.com/x")

    result = notifications.resolve_pipeline_notification_override("pl-1", "tenant-a")

    assert result == ("slack", "https://hooks.slack.com/x")


def test_resolve_pipeline_notification_override_swallows_decrypt_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryptography.fernet import InvalidToken

    monkeypatch.setattr(
        "ai_etl.audit.db.get_saved_pipeline_notification_target",
        lambda pid, tid: {
            "notification_channel": "slack",
            "notification_target_ciphertext": "corrupted",
            "notification_active": True,
        },
    )

    def _raise(ct):  # noqa: ANN001
        raise InvalidToken()

    monkeypatch.setattr(notifications, "decrypt_value", _raise)

    assert notifications.resolve_pipeline_notification_override("pl-1", "tenant-a") == (None, None)
