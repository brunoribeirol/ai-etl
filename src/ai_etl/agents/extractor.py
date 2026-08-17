"""Extractor Agent — connects to sources and extracts DataFrames."""

from typing import Any

import pandas as pd

from ai_etl.audit.logger import log_action
from ai_etl.core.state import PipelineState
from ai_etl.sources.csv_source import load_csv
from ai_etl.sources.document_source import load_document
from ai_etl.sources.postgres_source import load_postgres
from ai_etl.sources.rest_source import load_rest


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

    for source in sources:
        name = source["name"]
        source_type = source["type"]

        try:
            if source_type == "csv":
                df = load_csv(source["path"])
            elif source_type == "postgres":
                df = load_postgres(source["table"])
            elif source_type == "rest":
                df = load_rest(source["url"], source.get("params", {}))
            elif source_type == "document":
                df = load_document(source["path"])
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
    return {
        **state,
        "extracted_data": extracted_data,
        "source_schemas": source_schemas,
        "audit_log": new_log,
    }


def _extract_schema(df: pd.DataFrame) -> dict[str, Any]:
    return {
        "columns": df.columns.tolist(),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "shape": list(df.shape),
        "sample": df.head(3).to_dict(orient="records"),
        "null_counts": df.isnull().sum().to_dict(),
    }
