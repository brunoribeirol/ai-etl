"""JSON-safe serialization of `audit.db.load_full_result()`'s output (ADR-011).

`load_full_result` reconstructs a dict shaped for `app.py`'s
`_render_results()` — meant to be read directly by Python, so it holds real
`pd.DataFrame`/`plotly.graph_objects.Figure` objects (`state["transformed_data"]`,
`gold[i]["gold_df"]`/`science[i]["predictions_df"]`, `gold[i]["fig"]`/
`science[i]["fig"]`), not something a JSON response can carry as-is. This
module converts those in place, everything else in the dict already being
JSON-safe (the `_make_serializable` placeholder strings, plain
strings/numbers/lists/dicts in `advisor`/`tokens`).
"""

from typing import Any

import pandas as pd
from plotly.graph_objects import Figure


def _serialize_dataframe(df: pd.DataFrame) -> list[dict[str, Any]]:
    # NaN/NaT aren't valid JSON — FastAPI's default encoder would otherwise
    # emit the (non-standard, some clients reject it) literal `NaN` token.
    return df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")  # type: ignore[return-value]


def _serialize_figure(fig: Figure) -> dict[str, Any]:
    # Plotly.js's `Plotly.newPlot(el, data, layout)` reads this exact shape
    # directly — no transformation needed on the frontend side.
    return fig.to_plotly_json()  # type: ignore[no-any-return]


def _serialize_analysis_entry(entry: dict[str, Any], df_key: str) -> dict[str, Any]:
    serialized = dict(entry)
    df = serialized.get(df_key)
    if isinstance(df, pd.DataFrame):
        serialized[df_key] = _serialize_dataframe(df)
    fig = serialized.get("fig")
    if isinstance(fig, Figure):
        serialized["fig"] = _serialize_figure(fig)
    return serialized


def serialize_full_result(result: dict[str, Any]) -> dict[str, Any]:
    """Convert `load_full_result()`'s output into a JSON-safe dict."""
    state = dict(result.get("state") or {})
    transformed_data = state.get("transformed_data")
    if isinstance(transformed_data, pd.DataFrame):
        state["transformed_data"] = _serialize_dataframe(transformed_data)

    return {
        "bronze": result.get("bronze"),
        "state": state,
        "gold": [_serialize_analysis_entry(g, "gold_df") for g in result.get("gold", [])],
        "science": [
            _serialize_analysis_entry(s, "predictions_df") for s in result.get("science", [])
        ],
        "advisor": result.get("advisor", {}),
        "question": result.get("question", ""),
        "tokens": result.get("tokens", {}),
    }
