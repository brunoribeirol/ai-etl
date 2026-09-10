"""Audit logger — appends structured action entries to PipelineState.audit_log.

All agents must call log_action() for every significant decision or step.
The audit log is the primary record of what happened in a pipeline run.
"""

import re
from datetime import datetime, timezone
from typing import Any

from ai_etl.core.state import PipelineState


def log_action(
    state: PipelineState,
    agent: str,
    action: str,
    details: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Create a new audit entry and return the updated audit_log list.

    Usage in an agent node:
        new_log = log_action(state, "extractor", "csv_loaded", {"path": path, "rows": len(df)})
        return {**state, "audit_log": new_log}

    Args:
        state: Current pipeline state.
        agent: Name of the agent creating the entry (e.g., "orchestrator").
        action: Short description of the action (e.g., "plan_created", "csv_loaded").
        details: Optional dict with action-specific metadata.

    Returns:
        New list with the appended entry (does not mutate state).
    """
    entry: dict[str, Any] = {
        "agent": agent,
        "action": action,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "run_id": state["run_id"],
    }
    if details:
        entry["details"] = _sanitize(details)

    return [*state["audit_log"], entry]


_SENSITIVE_KEYWORDS = {
    "key",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "bearer",
    # Audit finding M4/BT-1 (2026-09-09): a DSN with an embedded password lives
    # in fields named `connection_string`/`postgres_connection_string`/`dsn`,
    # none of which matched the keywords above — the value passed through
    # unredacted. These names are not plausible legitimate data-column names in
    # an audit payload, so redacting on them is safe.
    "connection",
    "dsn",
}

# A URL/DSN carrying inline credentials (scheme://user:password@host). Used to
# scrub free-text values (exception messages, connection errors) where a secret
# is embedded in a value, not in a field NAME — `_sanitize`'s key-name check
# can't catch that. See `scrub_secrets_in_text`.
_DSN_CREDENTIALS_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:)[^\s@/]+@")


def scrub_secrets_in_text(text: str) -> str:
    """Redact credentials embedded *inside* a free-text string (not a dict key).

    Audit finding M4/BT-2 (2026-09-09): a source-connection failure surfaces the
    raw driver exception into `state["error"]`, which is persisted verbatim
    (`audit/db/runs.py`) with no key-name redaction to catch it. If that message
    contains a DSN with an inline password (`postgresql://u:pw@host/db`), the
    password lands in the run's JSON snapshot and the `error` column. This scrubs
    the password out (`postgresql://u:***@host/db`) while keeping the rest of the
    message useful for debugging. Best-effort and non-throwing: any non-str input
    is returned unchanged.
    """
    if not isinstance(text, str):
        return text
    return _DSN_CREDENTIALS_RE.sub(r"\g<scheme>***@", text)


def _sanitize_value(value: Any) -> Any:
    """Recurse into nested dicts/lists so sensitive keys are redacted at any depth."""
    if isinstance(value, dict):
        return _sanitize(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return scrub_secrets_in_text(value)
    return value


def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive values from audit details before persisting.

    Recurses into nested dicts and lists (e.g. request headers, request
    history) so a sensitive key buried below the top level is still redacted.
    """
    return {
        k: "***REDACTED***"
        if any(kw in k.lower() for kw in _SENSITIVE_KEYWORDS)
        else _sanitize_value(v)
        for k, v in data.items()
    }
