"""Extractor Agent — connects to sources and extracts DataFrames."""

from typing import Any

import pandas as pd

from ai_etl.audit.logger import log_action
from ai_etl.core.state import PipelineState
from ai_etl.sources.csv_source import load_csv
from ai_etl.sources.document_source import load_document
from ai_etl.sources.mongodb_source import load_mongodb
from ai_etl.sources.mysql_source import load_mysql
from ai_etl.sources.postgres_source import load_postgres
from ai_etl.sources.rest_source import load_rest
from ai_etl.sources.sqlite_source import load_sqlite

# Sprint 12 (ADR-013): the raw per-row sample used to scale with column count with no
# cap — `df.head(3).to_dict(orient="records")` over ALL columns is 3 x n_cols values per
# source. Real profiling against a 200k x 300 benchmark confirmed this as the dominant
# contributor to source_schemas' serialized size, growing linearly and unbounded with
# source width. Capping the sample to the first MAX_SAMPLE_COLUMNS columns keeps the
# sample's job (help the LLM infer formats/units from a few concrete values) without
# paying full-width cost for every source, at every source-count multiplier. 20 is chosen
# as comfortably enough columns to infer format conventions from, well above every
# existing case-study source (<=8 columns), so this is a no-op for current scenarios.
MAX_SAMPLE_COLUMNS = 20


def extractor_node(state: PipelineState) -> PipelineState:
    """Load each source defined in pipeline_plan into a DataFrame.

    Builds extracted_data and source_schemas.
    Sets state["error"] on first connection failure.
    """
    if state.get("error"):
        return state

    sources = state["pipeline_plan"].get("sources", [])
    extracted_data: dict[str, Any] = {}
    source_schemas: dict[str, Any] = {}

    provider_override = state.get("llm_provider_override")
    model_override = state.get("llm_model_override")
    document_source_used = False

    for source in sources:
        name = source["name"]
        source_type = source["type"]

        try:
            if source_type == "csv":
                df = load_csv(
                    source["path"],
                    sheet_name=source.get("sheet_name"),
                    header_row=source.get("header_row"),
                )
            elif source_type == "postgres":
                df = load_postgres(source["table"])
            elif source_type == "sqlite":
                df = load_sqlite(source["path"], source["table"], source.get("query"))
            elif source_type == "mysql":
                df = load_mysql(source["table"], source.get("query"))
            elif source_type == "mongodb":
                df = load_mongodb(
                    source["database"],
                    source["collection"],
                    source.get("query"),
                    source.get("limit"),
                )
            elif source_type == "rest":
                df = load_rest(source["url"], source.get("params", {}), source.get("auth"))
            elif source_type == "document":
                df = load_document(
                    source["path"],
                    llm_provider_override=provider_override,
                    llm_model_override=model_override,
                )
                document_source_used = True
            else:
                raise ValueError(f"Unsupported source type: {source_type}")

            extracted_data[name] = df
            source_schemas[name] = _extract_schema(df)

        except Exception as e:
            new_log = log_action(
                state, "extractor", "source_failed", {"source": name, "error": str(e)}
            )
            return {
                **state,
                "error": f"Extractor failed on source '{name}': {e}",
                "status": "failed",
                "audit_log": new_log,
            }

    new_log = log_action(
        state,
        "extractor",
        "extraction_complete",
        {
            "sources": list(extracted_data.keys()),
            "total_rows": {k: len(v) for k, v in extracted_data.items()},
        },
    )
    if document_source_used and (provider_override or model_override):
        # Sprint 30/gap-closing (ADR-031 §5) — visibility into the override actually
        # used by document_source.py::load_document's own get_llm() call, distinct
        # from the Orchestrator/Transformer entries since this node's LLM usage is
        # conditional (only "document"-typed sources reach it).
        new_log = log_action(
            {**state, "audit_log": new_log},
            "extractor",
            "llm_override_used",
            {"provider": provider_override, "model": model_override},
        )
    return {
        **state,
        "extracted_data": extracted_data,
        "source_schemas": source_schemas,
        "audit_log": new_log,
    }


def _extract_schema(df: pd.DataFrame) -> dict[str, Any]:
    """Build the schema summary fed into the Orchestrator/Transformer prompts.

    `dtypes`/`null_counts`/`columns` stay full-width — one small scalar per column,
    not per-row sampled values, so they don't scale the way the raw sample does.
    `sample` is capped to the first MAX_SAMPLE_COLUMNS columns (ADR-013, Sprint 12
    scale profiling) — see module-level constant docstring for why.
    """
    null_counts = df.isnull().sum().to_dict()
    n_rows = len(df)
    sample_columns = df.columns[:MAX_SAMPLE_COLUMNS]
    return {
        "columns": df.columns.tolist(),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "shape": list(df.shape),
        "sample": df[sample_columns].head(3).to_dict(orient="records"),
        "sample_truncated": len(df.columns) > MAX_SAMPLE_COLUMNS,
        "null_counts": null_counts,
        "null_ratio": {
            col: round(count / n_rows, 4) if n_rows else 0.0 for col, count in null_counts.items()
        },
    }
