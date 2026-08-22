"""Structured (JSON) logging setup for production (Sprint 34, ADR-033).

Every agent/service already calls the stdlib `logging` module directly
(`logger = logging.getLogger(__name__)`, `logger.warning(...)`,
`ai_etl.audit.logger.log_action(...)` for pipeline audit entries) — this
module does not touch any of those call sites. It only swaps the *handler/
formatter* attached to the root logger, once, at process bootstrap
(`api/main.py` for the web process, `core/celery_app.py` for the worker),
so every existing log line becomes one JSON object per line instead of
plain text, with no call-site changes anywhere in the codebase.

Deliberately no `python-json-logger` (or any other) dependency — the format
this project needs (`timestamp`, `level`, `logger`, `message`, plus whatever
extra fields a call site passes via `logging.LoggerAdapter`/`extra=`) is a
~30-line `logging.Formatter` subclass, not worth a new dependency for.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

# Attributes every `logging.LogRecord` carries regardless of what a call site
# passes via `extra=` — anything NOT in this set on a given record is a
# caller-supplied extra field and gets folded into the JSON output. Mirrors
# the fields `logging.LogRecord.__init__` sets unconditionally (see the
# stdlib `logging` source) so a legitimate `extra={"foo": ...}` is never
# mistaken for a stdlib attribute, and vice versa.
_STANDARD_RECORD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Renders each `LogRecord` as a single JSON line.

    Always includes `timestamp` (UTC, ISO 8601), `level`, `logger`,
    `message`; includes `exception` (via `Formatter.formatException`) only
    when the record carries `exc_info`; folds in any caller-supplied
    `extra=` field verbatim (e.g. `logger.warning("...", extra={"tenant_id":
    tenant_id})`), same "structured extras" behavior `python-json-logger`
    would give, without the dependency.

    Note: this formatter does not redact sensitive fields — that job stays
    with `ai_etl.audit.logger._sanitize` for audit-log details. Application
    code must keep avoiding `logger.warning(f"... {api_key}")`-shaped calls,
    same rule as before this sprint (see CLAUDE.md's non-negotiables).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and key != "message":
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None) -> None:
    """Attach a single JSON-formatted `StreamHandler` to the root logger.

    Idempotent: safe to call more than once (e.g. if a future entrypoint
    imports both `api/main.py` and `core/celery_app.py` in-process) — it
    clears any handlers this function previously attached before adding a
    fresh one, rather than stacking duplicate handlers that would each emit
    the same line.

    `level` defaults to `AI_ETL_LOG_LEVEL` (env var), falling back to
    `INFO` — matches the "env var, sane default" convention every other
    config knob in this project follows (e.g. `AI_ETL_SANDBOX_TIMEOUT`).
    """
    resolved_level: str = level or os.getenv("AI_ETL_LOG_LEVEL") or "INFO"
    resolved_level = resolved_level.upper()

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, "_ai_etl_json_handler", False):
            root_logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler._ai_etl_json_handler = True  # type: ignore[attr-defined]  # marker for idempotent re-configuration above, not a stdlib LogRecord/Handler attribute
    root_logger.addHandler(handler)
    root_logger.setLevel(resolved_level)
