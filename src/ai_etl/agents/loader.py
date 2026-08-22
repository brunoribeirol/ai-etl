"""Loader Agent — writes the validated DataFrame to the destination."""

from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from ai_etl.audit.logger import log_action
from ai_etl.core.state import PipelineState
from ai_etl.destinations.csv_dest import preview_csv, save_csv
from ai_etl.destinations.postgres_dest import preview_postgres, save_postgres
from ai_etl.destinations.s3_parquet_dest import preview_s3_parquet, save_s3_parquet


def _is_write_gated(policy: Optional[dict[str, Any]], rows: int) -> bool:
    """Sprint 27 (ADR-028 Decision 2) — pure gate decision, no I/O.

    `policy` is `PipelineState["approval_policy"]`: `None` for every avulso
    run (never gated). For a saved pipeline with `require_approval=True`:
    the very first write (`last_approved_at is None`) is always gated
    regardless of `threshold_rows`; after at least one approval, only a
    write whose row count is at or above `threshold_rows` is gated again —
    `threshold_rows is None` means "always require approval" (the roadmap's
    own "exigir aprovação sempre" option).
    """
    if not policy or not policy.get("require_approval"):
        return False
    if policy.get("last_approved_at") is None:
        return True
    threshold = policy.get("threshold_rows")
    if threshold is None:
        return True
    return rows >= int(threshold)


def _build_preview(dest_type: str, destination: dict[str, Any], df: pd.DataFrame) -> dict[str, Any]:
    """Dispatch to the destination-specific, write-free preview builder.

    Raises the same `ValueError` `loader_node`'s real-write branch would for
    an unsupported `dest_type` — kept as a distinct call so a preview never
    silently succeeds for a destination type it doesn't actually know how to
    write to either.
    """
    if dest_type == "csv":
        return preview_csv(df, destination["path"])
    if dest_type == "postgres":
        return preview_postgres(df, destination["table"])
    if dest_type == "s3_parquet":
        return preview_s3_parquet(df, destination["bucket"], destination["key"])
    raise ValueError(f"Unsupported destination type: {dest_type}")


def loader_node(state: PipelineState) -> PipelineState:
    """Load transformed_data into the destination defined in pipeline_plan.

    Only reached when Quality Agent severity is not "error".
    Validates row count after load.

    Sprint 27 (ADR-028): if this run's `approval_policy` gates the write
    (`_is_write_gated`) and `approval_granted` isn't already set, this node
    computes a write-free preview instead of writing, and returns
    `status="awaiting_approval"` — a legitimate terminal state, not a
    failure. `services/pipeline_service.py::resume_pending_load` calls this
    same node a second time, directly (not through the graph), with
    `approval_granted=True` once an operator approves — see the ADR for why
    a second direct call, not a LangGraph checkpointer, resumes the write.
    """
    if state.get("error"):
        return state

    destination = state["pipeline_plan"]["destination"]
    df: pd.DataFrame = state["transformed_data"]  # type: ignore[assignment]  # non-None guaranteed by error short-circuit above
    dest_type = destination["type"]

    if _is_write_gated(state.get("approval_policy"), len(df)) and not state.get("approval_granted"):
        try:
            preview = _build_preview(dest_type, destination, df)
        except Exception as e:
            new_log = log_action(state, "loader", "load_preview_failed", {"error": str(e)})
            return {
                **state,
                "error": f"Loader preview failed: {e}",
                "status": "failed",
                "audit_log": new_log,
            }
        new_log = log_action(state, "loader", "load_awaiting_approval", preview)
        return {
            **state,
            "load_preview": preview,
            "load_result": None,
            "status": "awaiting_approval",
            "audit_log": new_log,
        }

    try:
        if dest_type == "csv":
            load_result = save_csv(df, destination["path"])
        elif dest_type == "postgres":
            load_result = save_postgres(df, destination["table"])
        elif dest_type == "s3_parquet":
            load_result = save_s3_parquet(df, destination["bucket"], destination["key"])
        else:
            raise ValueError(f"Unsupported destination type: {dest_type}")

        load_result["timestamp"] = datetime.now(tz=timezone.utc).isoformat()

    except Exception as e:
        new_log = log_action(state, "loader", "load_failed", {"error": str(e)})
        return {**state, "error": f"Loader failed: {e}", "status": "failed", "audit_log": new_log}

    new_log = log_action(state, "loader", "load_complete", load_result)
    return {**state, "load_result": load_result, "status": "completed", "audit_log": new_log}
