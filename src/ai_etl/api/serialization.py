"""JSON-safe serialization of `audit.db.load_full_result()`'s output (ADR-011).

`load_full_result` reconstructs a dict shaped for `app.py`'s
`_render_results()`: meant to be read directly by Python, so it holds real
`pd.DataFrame`/`plotly.graph_objects.Figure` objects (`state["transformed_data"]`,
`gold[i]["gold_df"]`/`science[i]["predictions_df"]`, `gold[i]["fig"]`/
`science[i]["fig"]`), not something a JSON response can carry as-is. This
module converts those in place, everything else in the dict already being
JSON-safe (the `_make_serializable` placeholder strings, plain
strings/numbers/lists/dicts in `advisor`/`tokens`).
"""

import math
from typing import Any, cast

import pandas as pd
from plotly.graph_objects import Figure


def nan_to_none_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """NaN/NaT aren't valid JSON: FastAPI's default encoder would otherwise
    emit the (non-standard, some clients reject it) literal `NaN` token.
    Post-`to_dict()` cleanup rather than `DataFrame.where(..., None)`: pandas-stubs'
    `where()` overloads don't accept a bare `None` for `other` under mypy strict.
    Shared by `_serialize_dataframe` below and `api/routers/runs.py::list_runs`."""
    return [
        {
            key: (None if isinstance(value, float) and math.isnan(value) else value)
            for key, value in row.items()
        }
        for row in records
    ]


def _serialize_dataframe(df: pd.DataFrame) -> list[dict[str, Any]]:
    records = cast("list[dict[str, Any]]", df.to_dict(orient="records"))
    return nan_to_none_records(records)


def _serialize_figure(fig: Figure) -> dict[str, Any]:
    # Plotly.js's `Plotly.newPlot(el, data, layout)` reads this exact shape
    # directly: no transformation needed on the frontend side.
    return fig.to_plotly_json()  # type: ignore[no-any-return]  # plotly has no return-type stub here


def _serialize_analysis_entry(
    entry: dict[str, Any], df_key: str, preview_rows: int | None = None
) -> dict[str, Any]:
    serialized = dict(entry)
    df = serialized.get(df_key)
    if isinstance(df, pd.DataFrame):
        if preview_rows is not None:
            serialized[f"{df_key}_row_count"] = len(df)
            df = df.head(preview_rows)
        serialized[df_key] = _serialize_dataframe(df)
    fig = serialized.get("fig")
    if isinstance(fig, Figure):
        serialized["fig"] = _serialize_figure(fig)
    return serialized


def serialize_full_result(
    result: dict[str, Any], preview_rows: int | None = None
) -> dict[str, Any]:
    """Convert `load_full_result()`'s output into a JSON-safe dict.

    With `preview_rows`, every table (the Silver data and each analysis table) keeps only its
    first rows, and its total is added beside it (`transformed_row_count`,
    `gold_df_row_count`, `predictions_df_row_count`). The run page shows 20 rows of each; the
    full answer for a 7,043-row file was 3.5 MB and took 12.5 s on production (2026-10-16).
    Without it, the answer is unchanged."""
    state = dict(result.get("state") or {})
    transformed_data = state.get("transformed_data")
    if isinstance(transformed_data, pd.DataFrame):
        if preview_rows is not None:
            state["transformed_row_count"] = len(transformed_data)
            transformed_data = transformed_data.head(preview_rows)
        state["transformed_data"] = _serialize_dataframe(transformed_data)

    return {
        "bronze": result.get("bronze"),
        "state": state,
        "gold": [
            _serialize_analysis_entry(g, "gold_df", preview_rows) for g in result.get("gold", [])
        ],
        "science": [
            _serialize_analysis_entry(s, "predictions_df", preview_rows)
            for s in result.get("science", [])
        ],
        "advisor": result.get("advisor", {}),
        "question": result.get("question", ""),
        "tokens": result.get("tokens", {}),
    }
