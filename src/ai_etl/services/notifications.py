"""Digest delivery — email (Resend) and Slack (incoming webhook), Sprint 14 / ADR-018.

Both functions are opt-in via env vars and never raise: a saved pipeline with
drift detection enabled but no delivery channel configured still runs the
comparison, it just has nowhere to send the result — that is a configuration
gap for the operator to fix, not a reason to fail the underlying pipeline run
(`services/alerting.py` calls these from inside a best-effort block for
exactly this reason). Built on `httpx`, already a base dependency (see
`sources/rest_source.py`) — no new dependency for either channel.

**Not verified against a real provider in this environment** — no
`RESEND_API_KEY` and no real Slack webhook URL available here (see ADR-018's
Decision 5). The request shape below matches each provider's own published
API contract; a real send needs zero code changes once real credentials
exist.
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
