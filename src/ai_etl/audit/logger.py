"""Audit logger — appends structured action entries to PipelineState.audit_log.

All agents must call log_action() for every significant decision or step.
The audit log is the primary record of what happened in a pipeline run.
"""

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


def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive values from audit details before persisting."""
    sensitive_keywords = {"key", "token", "secret", "password", "credential"}
    return {
        k: "***REDACTED***" if any(kw in k.lower() for kw in sensitive_keywords) else v
        for k, v in data.items()
    }
