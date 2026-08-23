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
    source_schemas: dict[str, Any]  # {"source_name": {columns, dtypes, shape, sample,
    # sample_truncated, null_counts, null_ratio}} — sample is capped to
    # extractor.MAX_SAMPLE_COLUMNS columns for wide sources (ADR-013).

    # --- Transformer output ---
    transformation_code: str  # generated Python function as string
    transformed_data: Optional[pd.DataFrame]  # None until Transformer runs successfully
    transformation_attempts: int  # retry counter, max 3
    transformation_error: Optional[str]  # last sandbox execution error

    # --- Quality input (Sprint 16, ADR-023) ---
    custom_quality_rules: list[dict[str, Any]]  # operator-defined rules for the saved
    # pipeline this run belongs to (empty for every avulso run). Fetched by
    # `services/pipeline_service.py::run_silver_pipeline` from
    # `saved_pipelines.quality_rules` — never read from the DB by `quality_node`
    # itself, keeping the node a pure function of `PipelineState`. Each entry:
    # {"column": str, "operator": "not_null"|"gte"|"lte"|"gt"|"lt"|"eq"|"ne",
    #  "value": Any (omitted for "not_null"), "severity": "ok"|"warning"|"error"
    #  (default "error"), "name": str (optional, defaults to "{operator} {column}")}.
    # See `agents/pipeline/quality.py::_CUSTOM_RULE_OPERATORS` for the whitelisted dispatch.

    # --- Quality output ---
    quality_report: dict[str, Any]
    # quality_report structure:
    # {
    #   "checks": [
    #     {"check": "null", "column": "customer_id", "null_ratio": 0.1, "severity": "warning"},
    #     {"check": "duplicate", "count": 0, "severity": "ok"},
    #     {"check": "outlier", "column": "amount", "outlier_count": 3, "severity": "warning"},
    #     {"check": "custom_rule", "rule_name": "amount não pode ser negativo",
    #      "column": "amount", "operator": "gte", "value": 0, "violation_count": 2,
    #      "severity": "error"}   # Sprint 16 (ADR-023) — one entry per custom rule
    #   ],
    #   "severity": "ok" | "warning" | "error",   # max severity across all checks
    #   "summary": "3 checks: 2 warnings, 0 errors"
    # }

    # --- LLM provider/model override (Sprint 30/gap-closing, ADR-031 §5) ---
    llm_provider_override: Optional[str]  # e.g. "anthropic" — resolved once by
    # `services/pipeline_service.py::run_silver_pipeline` from the saved pipeline's
    # own `llm_provider` column (via `audit/db.py::get_saved_pipeline_llm_config`),
    # mirroring `custom_quality_rules`'/`approval_policy`'s resolve-in-service-then-
    # carry-in-state pattern. `None` for every avulso run and every saved pipeline
    # that never called `PUT /pipelines/{id}/llm-config` — falls back to this
    # deployment's global `AI_ETL_LLM_PROVIDER`. Read by every `agents/pipeline/*.py`
    # node that calls `core/llm.py::get_llm()`; never queried from the DB inside a
    # node itself.
    llm_model_override: Optional[str]  # paired with llm_provider_override above —
    # always resolved (and `None`) together, since `saved_pipelines.llm_provider`/
    # `llm_model` are always written or cleared together (ADR-031 §3).

    # --- Loader input (Sprint 27, ADR-028) ---
    approval_policy: Optional[dict[str, Any]]  # {"require_approval": bool,
    # "threshold_rows": int | None, "last_approved_at": str | None (isoformat)} —
    # resolved by `services/pipeline_service.py::run_silver_pipeline` from the
    # saved pipeline this run belongs to (mirrors `custom_quality_rules`'
    # resolution above). `None` for every avulso run — an avulso execution is
    # never gated, see ADR-028 Decision 2. Read by `agents/pipeline/loader.py::loader_node`
    # only; never queried from the DB inside the node itself.
    approval_granted: bool  # Sprint 27 (ADR-028) — set `True` only by
    # `pipeline_service.resume_pending_load` on the second, direct `loader_node`
    # call that performs the real write after an operator approves a gated run.
    # `False` for every normal graph run (the default `initial_state` sets).

    # --- Loader output ---
    load_result: Optional[
        dict[str, Any]
    ]  # {"rows_loaded": int, "destination": str, "timestamp": str}
    load_preview: Optional[dict[str, Any]]  # Sprint 27 (ADR-028) — set instead of
    # `load_result` when a write is gated: what *would* be written, computed by
    # `destinations/*.py::preview_*` without ever writing. `None` otherwise.

    # --- Audit (appended by every agent) ---
    audit_log: list[dict[str, Any]]

    # --- Latency instrumentation (ADR-007) ---
    stage_durations: dict[str, float]  # {"orchestrator": 1.2, "extractor": 0.4, ...} — wall-
    # clock seconds per LangGraph node, populated by core/graph.py's `_timed()` wrapper.

    # --- Control flow ---
    error: Optional[str]  # fatal error message; routes to END if set
    status: str  # "running" | "completed" | "failed" | "awaiting_approval" (Sprint 27, ADR-028
    # — a gated write that computed a preview but did not write; not a failure, see the ADR)


def initial_state(
    spec: str,
    run_id: str,
    custom_quality_rules: Optional[list[dict[str, Any]]] = None,
    approval_policy: Optional[dict[str, Any]] = None,
    llm_provider_override: Optional[str] = None,
    llm_model_override: Optional[str] = None,
) -> PipelineState:
    """Create a fresh PipelineState for a new pipeline run.

    Args:
        custom_quality_rules: Sprint 16 (ADR-023) — operator-defined quality rules for
            the saved pipeline this run belongs to, already resolved by the caller
            (`services/pipeline_service.py::run_silver_pipeline`). Defaults to `[]` for
            every avulso run and every existing caller that doesn't pass it.
        approval_policy: Sprint 27 (ADR-028) — this run's write-approval policy,
            already resolved by the caller from the saved pipeline it belongs to.
            `None` (the default) for every avulso run — never gated.
        llm_provider_override: Sprint 30/gap-closing (ADR-031 §5) — this run's
            saved-pipeline LLM provider override, already resolved by the caller
            (`services/pipeline_service.py::run_silver_pipeline` via
            `audit/db.py::get_saved_pipeline_llm_config`). `None` (the default) for
            every avulso run and every saved pipeline without an override — falls
            back to the deployment-global `AI_ETL_LLM_PROVIDER` at each `get_llm()`
            call site.
        llm_model_override: paired with `llm_provider_override` above.
    """
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
        custom_quality_rules=custom_quality_rules or [],
        quality_report={},
        llm_provider_override=llm_provider_override,
        llm_model_override=llm_model_override,
        approval_policy=approval_policy,
        approval_granted=False,
        load_result=None,
        load_preview=None,
        audit_log=[],
        stage_durations={},
        error=None,
        status="running",
    )
