"""Error tracking / APM bootstrap — Sentry (Sprint 34, ADR-033).

Conditional on `SENTRY_DSN`: unset (the default in every environment except
a deployed one with a real Sentry project configured) makes `init_sentry()`
a no-op, same "optional, never breaks if absent" convention as
`services/notifications.py`'s Resend/Slack/Teams/Google Chat integrations
and `services/secrets_service.py`'s `AI_ETL_SECRETS_ENCRYPTION_KEY`.

**Not verified against a real Sentry project in this environment** — no
Sentry account/DSN is available here (same constraint `case_study/results/
sprint8/README.md` documents for `OPENAI_API_KEY`/Ollama). `init_sentry()`
and its no-op path are covered by `tests/unit/test_observability.py`; an
actual event reaching a real Sentry dashboard has not been confirmed and
must not be claimed as verified.
"""

from __future__ import annotations

import logging
import os

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

logger = logging.getLogger(__name__)


def init_sentry(component: str) -> bool:
    """Initialize the Sentry SDK if `SENTRY_DSN` is set; no-op otherwise.

    Args:
        component: which process is initializing Sentry (`"api"` or
            `"worker"`) — set as the `component` tag on every event, so
            errors from the web process and the Celery worker can be told
            apart in the Sentry dashboard without separate projects.

    Returns:
        `True` if Sentry was actually initialized (a DSN was present),
        `False` if this call was a no-op. Callers don't need the return
        value for control flow — `capture`/breadcrumb calls elsewhere stay
        no-ops on the SDK's own side when uninitialized — it exists mainly
        so tests and the bootstrap log line can assert/report which path
        was taken.
    """
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        logger.info("Sentry not configured (SENTRY_DSN unset) — error tracking disabled.")
        return False

    # LoggingIntegration: every `logging.ERROR`+ record (any `logger.error`/
    # `logger.exception` call anywhere in the codebase, unchanged call sites)
    # is forwarded to Sentry as an event automatically; `logging.INFO`+ is
    # attached as a breadcrumb trail leading up to it. No new instrumentation
    # needed at any of the existing `logger = logging.getLogger(__name__)`
    # call sites this sprint's JSON-logging change also leaves untouched.
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("AI_ETL_ENV", "dev"),
        integrations=[
            FastApiIntegration(),
            CeleryIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        # Tier-conscious defaults (free Sentry tier has a monthly event
        # cap) — captures every error but only samples a fraction of
        # performance traces. Overridable per-environment without a code
        # change if traffic volume ever calls for it.
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
    )
    sentry_sdk.set_tag("component", component)
    logger.info("Sentry initialized (component=%s).", component)
    return True
