"""PipelineState — shared state TypedDict passed between all LangGraph nodes.

Every agent reads from and writes to this state. No agent communicates
directly with another — all information flows through the state.
"""

from typing import Any, Optional, TypedDict

import pandas as pd


class PipelineState(TypedDict):
    # --- Input (immutable after Orchestrator) ---
    spec: str  # natural language pipeline specification
    run_id: str  # UUID generated at pipeline start

    # --- Orchestrator output ---
    pipeline_plan: dict[str, Any]
    # pipeline_plan structure:
    # {
    #   "sources": [
    #     {"name": "orders", "type": "csv", "path": "data/orders.csv"},
    #     {"name": "customers", "type": "postgres", "table": "public.customers"},
    #     {"name": "weather", "type": "rest", "url": "https://api.example.com/weather"}
    #   ],
    #   "destination": {"type": "postgres", "table": "public.output"},
    #   "transformations": ["rename column dt to date", "filter where status = active"],
    #   "quality_checks": ["null_check on customer_id", "duplicate_check on order_id"]
    # }

    # --- Extractor output ---
    extracted_data: dict[str, Any]  # {"source_name": pd.DataFrame}
    source_schemas: dict[str, Any]  # {"source_name": {columns, dtypes, shape, sample}}

    # --- Transformer output ---
    transformation_code: str  # generated Python function as string
    transformed_data: Optional[pd.DataFrame]  # None until Transformer runs successfully
    transformation_attempts: int  # retry counter, max 3
    transformation_error: Optional[str]  # last sandbox execution error

    # --- Quality output ---
    quality_report: dict[str, Any]
    # quality_report structure:
    # {
    #   "checks": [
    #     {"check": "null", "column": "customer_id", "null_ratio": 0.1, "severity": "warning"},
    #     {"check": "duplicate", "count": 0, "severity": "ok"},
    #     {"check": "outlier", "column": "amount", "outlier_count": 3, "severity": "warning"}
    #   ],
    #   "severity": "ok" | "warning" | "error",   # max severity across all checks
    #   "summary": "3 checks: 2 warnings, 0 errors"
    # }

    # --- Loader output ---
    load_result: Optional[
        dict[str, Any]
    ]  # {"rows_loaded": int, "destination": str, "timestamp": str}

    # --- Audit (appended by every agent) ---
    audit_log: list[dict[str, Any]]

    # --- Latency instrumentation (ADR-007) ---
    stage_durations: dict[str, float]  # {"orchestrator": 1.2, "extractor": 0.4, ...} — wall-
    # clock seconds per LangGraph node, populated by core/graph.py's `_timed()` wrapper.

    # --- Control flow ---
    error: Optional[str]  # fatal error message; routes to END if set
    status: str  # "running" | "completed" | "failed"


def initial_state(spec: str, run_id: str) -> PipelineState:
    """Create a fresh PipelineState for a new pipeline run."""
    return PipelineState(
        spec=spec,
        run_id=run_id,
        pipeline_plan={},
        extracted_data={},
        source_schemas={},
        transformation_code="",
        transformed_data=None,
        transformation_attempts=0,
        transformation_error=None,
        quality_report={},
        load_result=None,
        audit_log=[],
        stage_durations={},
        error=None,
        status="running",
    )
