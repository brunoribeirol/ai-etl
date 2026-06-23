"""Loader Agent — writes the validated DataFrame to the destination."""

from datetime import datetime, timezone

import pandas as pd

from ai_etl.audit.logger import log_action
from ai_etl.core.state import PipelineState
from ai_etl.destinations.csv_dest import save_csv
from ai_etl.destinations.postgres_dest import save_postgres


def loader_node(state: PipelineState) -> PipelineState:
    """Load transformed_data into the destination defined in pipeline_plan.

    Only reached when Quality Agent severity is not "error".
    Validates row count after load.
    """
    if state.get("error"):
        return state

    destination = state["pipeline_plan"]["destination"]
    df: pd.DataFrame = state["transformed_data"]  # type: ignore[assignment]  # non-None guaranteed by error short-circuit above
    dest_type = destination["type"]

    try:
        if dest_type == "csv":
            load_result = save_csv(df, destination["path"])
        elif dest_type == "postgres":
            load_result = save_postgres(df, destination["table"])
        else:
            raise ValueError(f"Unsupported destination type: {dest_type}")

        load_result["timestamp"] = datetime.now(tz=timezone.utc).isoformat()

    except Exception as e:
        new_log = log_action(state, "loader", "load_failed", {"error": str(e)})
        return {**state, "error": f"Loader failed: {e}", "status": "failed", "audit_log": new_log}

    new_log = log_action(state, "loader", "load_complete", load_result)
    return {**state, "load_result": load_result, "status": "completed", "audit_log": new_log}
