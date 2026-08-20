"""Digest delivery — email (Resend), Slack, Microsoft Teams, and Google Chat
(all incoming webhooks), Sprint 14 / ADR-018 + addendum.

All four functions are opt-in via env vars and never raise: a saved pipeline
with drift detection enabled but no delivery channel configured still runs
the comparison, it just has nowhere to send the result — that is a
configuration gap for the operator to fix, not a reason to fail the
underlying pipeline run (`services/alerting.py` calls these from inside a
best-effort block for exactly this reason). Built on `httpx`, already a base
dependency (see `sources/rest_source.py`) — no new dependency for any
channel.

**Not verified against a real provider in this environment** — no real
credentials for any of the four available here (see ADR-018's Decision 5 and
its addendum). The request shape for each matches that provider's own
published API contract; a real send needs zero code changes once real
credentials exist.

Teams specifically: Microsoft retired the legacy Office 365 Connector
webhook format — the payload below targets the current mechanism (a
Power Automate "Workflows" webhook posting an Adaptive Card), which is
what Teams' own "Incoming Webhook"/"Workflows" connector setup produces
today.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

RESEND_API_URL = "https://api.resend.com/emails"
_HTTP_TIMEOUT_SECONDS = 15


def send_email_digest(subject: str, html_body: str, text_body: str) -> bool:
    """POST one email to Resend's `/emails` endpoint.

    Requires `RESEND_API_KEY`, `AI_ETL_ALERT_EMAIL_FROM`, and
    `AI_ETL_ALERT_EMAIL_TO` (comma-separated recipients) — returns `False`
    without making a request if any is unset or `AI_ETL_ALERT_EMAIL_TO`
    resolves to zero recipients after trimming. Returns `True` only on a
    2xx response; any `httpx` error (network failure, non-2xx status) is
    caught and returns `False` — a failed alert delivery must never raise
    into the caller's execution flow.
    """
    api_key = os.getenv("RESEND_API_KEY")
    from_address = os.getenv("AI_ETL_ALERT_EMAIL_FROM")
    to_raw = os.getenv("AI_ETL_ALERT_EMAIL_TO", "")
    recipients = [addr.strip() for addr in to_raw.split(",") if addr.strip()]

    if not api_key or not from_address or not recipients:
        return False

    payload: dict[str, Any] = {
        "from": from_address,
        "to": recipients,
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    try:
        response = httpx.post(
            RESEND_API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False


def send_slack_digest(blocks: list[dict[str, Any]], fallback_text: str) -> bool:
    """POST one message to a Slack incoming webhook URL.

    Requires `SLACK_WEBHOOK_URL` — returns `False` without making a request
    if unset. `fallback_text` is Slack's own required plain-text fallback
    for notifications/accessibility when `blocks` can't be rendered.
    Returns `True` only on a 2xx response; any `httpx` error is caught and
    returns `False`, same contract as `send_email_digest`.
    """
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return False

    payload = {"text": fallback_text, "blocks": blocks}
    try:
        response = httpx.post(webhook_url, json=payload, timeout=_HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False


def send_teams_digest(subject: str, text_body: str) -> bool:
    """POST one Adaptive Card to a Microsoft Teams "Workflows" incoming
    webhook URL (Power Automate — the mechanism Teams' own channel
    "Workflows" connector setup produces; the older Office 365 Connector
    webhook format is retired and not supported here).

    Requires `TEAMS_WEBHOOK_URL` — returns `False` without making a request
    if unset. `text_body` is rendered as a single Adaptive Card TextBlock
    with `wrap: true`; Teams renders a limited Markdown subset inside it
    (bold/italic/line breaks), which is why `services/digest.py`'s plain
    `text` field — not the Slack-specific `slack_blocks` — is the right
    input here, same content, different envelope. Returns `True` only on a
    2xx response; any `httpx` error is caught and returns `False`, same
    contract as `send_email_digest`/`send_slack_digest`.
    """
    webhook_url = os.getenv("TEAMS_WEBHOOK_URL")
    if not webhook_url:
        return False

    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": subject,
                            "weight": "bolder",
                            "size": "medium",
                            "wrap": True,
                        },
                        {"type": "TextBlock", "text": text_body, "wrap": True},
                    ],
                },
            }
        ],
    }
    try:
        response = httpx.post(webhook_url, json=payload, timeout=_HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False


def send_google_chat_digest(text_body: str) -> bool:
    """POST one text message to a Google Chat space's incoming webhook URL.

    Requires `GOOGLE_CHAT_WEBHOOK_URL` (the full URL Google Chat generates
    per space, including its `key`/`token` query parameters — treat the
    whole URL as the secret, same as `SLACK_WEBHOOK_URL`/`TEAMS_WEBHOOK_URL`)
    — returns `False` without making a request if unset. Google Chat's
    `text` field supports a small Markdown-like subset natively (`*bold*`,
    `_italic_`), so `services/digest.py`'s plain `text` field is sent as-is,
    same as the Teams channel above. Returns `True` only on a 2xx response;
    any `httpx` error is caught and returns `False`, same contract as the
    other three channels.
    """
    webhook_url = os.getenv("GOOGLE_CHAT_WEBHOOK_URL")
    if not webhook_url:
        return False

    payload = {"text": text_body}
    try:
        response = httpx.post(webhook_url, json=payload, timeout=_HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False
