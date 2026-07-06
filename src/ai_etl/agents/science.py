"""Science Agent — predictive analytics layer.

Receives the Silver DataFrame + a business question and produces:
- predictions_df : DataFrame with model results / predictions
- fig            : Plotly figure visualizing the model output
- narrative      : plain-language insight in Portuguese
- model_info     : dict with model type, metrics, and description
"""

import json
import textwrap
from typing import Any

import pandas as pd

from ai_etl.core.llm import get_llm

_PROMPT_TEMPLATE = """\
You are a senior data scientist. You have access to a cleaned pandas DataFrame called `df`.

## Exact column names (use ONLY these):
{columns_list}

## Column schema (column → dtype):
{schema}

## Column statistics:
{stats}

## Sample data (first 5 rows):
{sample}

## Business question (in Portuguese):
"{question}"

## Your task

Analyze the data and question to determine the BEST predictive analysis. Choose ONE:

- TIME SERIES FORECAST: if question asks about future values, trends over time
  (look for date/datetime columns; forecast the main numeric metric)
- REGRESSION: if question asks to predict a numeric value from features
- CLASSIFICATION: if question asks to predict a category/label
- CLUSTERING: if question asks about groups, segments, or profiles of customers/products

Then write Python code that defines EXACTLY four variables:

1. `predictions_df` — a pandas DataFrame with the model's results.
   - For FORECAST: columns = [date_col, 'actual', 'forecast'] or similar
   - For REGRESSION: columns = [feature, 'actual', 'predicted', 'residual']
   - For CLASSIFICATION: columns = [id_col, 'predicted_class', 'probability'] or a confusion summary
   - For CLUSTERING: original df with a new 'cluster' column + cluster summary

2. `fig` — a Plotly figure visualizing the model output clearly.
   - FORECAST: line chart with actual vs. forecast (use different colors/dashes)
   - REGRESSION: scatter actual vs. predicted + a reference line
   - CLASSIFICATION: bar chart of class distribution or feature importance
   - CLUSTERING: scatter or bar chart colored by cluster
   - Set a descriptive title in Portuguese. Use professional color palettes.

3. `narrative` — a 2–4 sentence string in Portuguese for a non-technical business user.
   Include: what type of model was used, the main finding, and a business implication.
   Be specific with numbers.

4. `model_info` — a Python dict with:
   {{"model_type": str, "task": str, "metrics": dict, "features": list, "target": str}}
   - task: "forecast" | "regression" | "classification" | "clustering"
   - metrics: relevant metrics (e.g. {{"rmse": 12.3, "r2": 0.87}} or {{"accuracy": 0.91}})
   - features: list of column names used as input
   - target: column name being predicted (or "segments" for clustering)

## Available libraries
- `pd` (pandas), `np` (numpy)
- `px` (plotly.express), `go` (plotly.graph_objects)
- sklearn: `LinearRegression`, `Ridge`, `RandomForestRegressor`, `RandomForestClassifier`,
  `LogisticRegression`, `KMeans`, `StandardScaler`, `MinMaxScaler`, `LabelEncoder`,
  `train_test_split`, `mean_squared_error`, `mean_absolute_error`, `r2_score`,
  `accuracy_score`, `silhouette_score`
- statsmodels: `ExponentialSmoothing`, `ARIMA`
- `math` (sqrt, log, etc.)

## Critical rules
- `df` is already loaded — do NOT read any files.
- Do NOT import anything — all libraries are already injected.
- Use ONLY the exact column names listed above.
- Always call `.reset_index()` on grouped results to get a proper DataFrame.
- For time series: sort by date first with `df.sort_values(date_col)`.
- For sklearn: handle NaN with `.dropna()` before fitting.
- `predictions_df` must be a pd.DataFrame. `fig` must be a Plotly Figure.
- `model_info` must be a plain Python dict (no numpy types: cast with float(), int()).
- Do NOT call `fig.show()`.
- Respond ONLY with valid Python code. No markdown fences. No explanations.
"""

_RETRY_PREFIX = """\
The previous attempt failed with this error:

  {error}

Rewrite the code from scratch. Do NOT repeat the same mistake.
Ensure all four variables are defined: `predictions_df` (DataFrame), `fig` (Plotly Figure),
`narrative` (str), `model_info` (dict).
Use ONLY the exact column names: {columns_list}

"""

_SAFE_GLOBALS: dict[str, Any] = {
    "__builtins__": {
        "len": len,
        "range": range,
        "print": print,
        "int": int,
        "float": float,
        "str": str,
        "list": list,
        "dict": dict,
        "bool": bool,
        "tuple": tuple,
        "set": set,
        "None": None,
        "True": True,
        "False": False,
        "enumerate": enumerate,
        "zip": zip,
        "map": map,
        "filter": filter,
        "sorted": sorted,
        "reversed": reversed,
        "min": min,
        "max": max,
        "sum": sum,
        "round": round,
        "abs": abs,
        "pow": pow,
        "divmod": divmod,
        "isinstance": isinstance,
        "issubclass": issubclass,
        "hasattr": hasattr,
        "getattr": getattr,
        "setattr": setattr,
        "all": all,
        "any": any,
        "type": type,
        "repr": repr,
        "format": format,
        "iter": iter,
        "next": next,
        "vars": vars,
        "slice": slice,
    },
}


def _build_column_stats(df: pd.DataFrame) -> str:
    parts: list[str] = []
    for col in df.columns:
        try:
            if pd.api.types.is_numeric_dtype(df[col]):
                s = df[col].dropna()
                if len(s) == 0:
                    parts.append(f"  {col}: numeric, all null")
                else:
                    parts.append(
                        f"  {col}: numeric, min={s.min():.4g}, max={s.max():.4g},"
                        f" mean={s.mean():.4g}, nulls={df[col].isna().sum()}"
                    )
            elif pd.api.types.is_datetime64_any_dtype(df[col]):
                parts.append(f"  {col}: datetime, range={df[col].min()} → {df[col].max()}")
            else:
                n_unique = df[col].nunique()
                nulls = df[col].isna().sum()
                if n_unique <= 20:
                    top = df[col].value_counts().head(5).to_dict()
                    top_str = ", ".join(f"'{k}': {v}" for k, v in top.items())
                    parts.append(
                        f"  {col}: categorical, {n_unique} unique, top=[{top_str}], nulls={nulls}"
                    )
                else:
                    parts.append(f"  {col}: high-cardinality ({n_unique} unique), nulls={nulls}")
        except Exception:  # noqa: BLE001
            parts.append(f"  {col}: (stats unavailable)")
    return "\n".join(parts) if parts else "(no stats)"


def _strip_fences(code: str) -> str:
    code = code.strip()
    if code.startswith("```"):
        lines = code.splitlines()
        start = 1
        end = len(lines)
        if lines[-1].strip() == "```":
            end -= 1
        code = "\n".join(lines[start:end])
    return code.strip()


def run_science(df: pd.DataFrame, business_question: str) -> dict[str, Any]:
    """Run predictive analytics on the Silver DataFrame.

    Returns a dict with keys:
        predictions_df : pd.DataFrame
        fig            : plotly Figure or None
        narrative      : str
        model_info     : dict
        code           : str
        attempts       : int
        error          : str or None
    """
    import math

    import numpy as np
    import plotly.express as px
    import plotly.graph_objects as go
    from sklearn.cluster import KMeans
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge
    from sklearn.metrics import (
        accuracy_score,
        mean_absolute_error,
        mean_squared_error,
        r2_score,
        silhouette_score,
    )
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import LabelEncoder, MinMaxScaler, StandardScaler
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    columns_list = str(df.columns.tolist())
    schema = {col: str(dtype) for col, dtype in df.dtypes.items()}
    sample = df.head(5).to_dict(orient="records")
    stats = _build_column_stats(df)

    base_prompt = _PROMPT_TEMPLATE.format(
        columns_list=columns_list,
        schema=json.dumps(schema, ensure_ascii=False, indent=2),
        stats=stats,
        sample=json.dumps(sample, default=str, ensure_ascii=False, indent=2),
        question=business_question,
    )

    safe_globals: dict[str, Any] = {
        **_SAFE_GLOBALS,
        "pd": pd,
        "np": np,
        "px": px,
        "go": go,
        "math": math,
        # sklearn
        "LinearRegression": LinearRegression,
        "Ridge": Ridge,
        "RandomForestRegressor": RandomForestRegressor,
        "RandomForestClassifier": RandomForestClassifier,
        "LogisticRegression": LogisticRegression,
        "KMeans": KMeans,
        "StandardScaler": StandardScaler,
        "MinMaxScaler": MinMaxScaler,
        "LabelEncoder": LabelEncoder,
        "train_test_split": train_test_split,
        "mean_squared_error": mean_squared_error,
        "mean_absolute_error": mean_absolute_error,
        "r2_score": r2_score,
        "accuracy_score": accuracy_score,
        "silhouette_score": silhouette_score,
        # statsmodels
        "ExponentialSmoothing": ExponentialSmoothing,
    }

    llm = get_llm()
    last_error = ""
    last_code = ""

    for attempt in range(1, 4):
        if attempt == 1:
            prompt = base_prompt
        else:
            prompt = _RETRY_PREFIX.format(error=last_error, columns_list=columns_list) + base_prompt

        response = llm.invoke(prompt)
        code = _strip_fences(str(response.content).strip())
        last_code = code

        local_env: dict[str, Any] = {"df": df.copy()}

        try:
            exec(code, safe_globals, local_env)  # noqa: S102

            predictions_df = local_env.get("predictions_df")
            fig = local_env.get("fig")
            narrative = local_env.get("narrative", "")
            model_info = local_env.get("model_info", {})

            if not isinstance(predictions_df, pd.DataFrame):
                raise TypeError(
                    f"predictions_df must be a pd.DataFrame, got {type(predictions_df).__name__}"
                )
            if fig is None:
                raise ValueError("fig must be defined (a Plotly Figure object)")
            if not isinstance(model_info, dict):
                raise TypeError(f"model_info must be a dict, got {type(model_info).__name__}")

            return {
                "predictions_df": predictions_df,
                "fig": fig,
                "narrative": str(narrative),
                "model_info": model_info,
                "code": code,
                "attempts": attempt,
                "error": None,
            }

        except Exception as exc:  # noqa: BLE001
            last_error = textwrap.shorten(str(exc), width=400, placeholder="...")

    return {
        "predictions_df": pd.DataFrame(),
        "fig": None,
        "narrative": "Não foi possível executar a análise preditiva. Tente reformular a pergunta ou verifique se os dados têm estrutura adequada para modelagem.",
        "model_info": {},
        "code": last_code,
        "attempts": 3,
        "error": last_error,
    }
