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

from ai_etl.agents._llm_codegen import build_column_stats as _build_column_stats
from ai_etl.agents._llm_codegen import strip_code_fences as _strip_fences
from ai_etl.core.analysis_types import ScienceResult, TokenUsage
from ai_etl.core.llm import extract_token_usage, get_llm, sum_token_usage
from ai_etl.core.sandbox import execute_in_sandbox, scale_timeout_for_rows

# Higher than Analyst's (15s) but still lower than Transformer's default (30s,
# core/sandbox.py) — Science's sandboxed code can legitimately do model fitting
# (RandomForest, KMeans, ExponentialSmoothing/ARIMA) on top of the aggregation
# work Analyst does, so it needs more headroom per attempt. Still bounded well
# below Transformer's 30s given the same 3x retry compounding ADR-007 flagged
# (`for attempt in range(1, 4)` below): worst case is 3 x 20s = 60s of sandbox
# time for one Science sub-task, before LLM latency. Revisit if real usage on
# this project's dataset sizes shows 20s is too tight for legitimate fits.
SCIENCE_TIMEOUT_SECONDS = 20

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

Analyze the data and question to determine the BEST analysis. Choose ONE:

- DIAGNOSTIC ANALYSIS: if the question asks WHY something happened, changed, dropped,
  increased, or differs across groups/time (e.g. "why did X fall in 2020", "what explains
  the difference between A and B"). Decompose the change by comparing segments, categories,
  or periods (before/after). Do NOT forecast future values for this case — a "why" question
  is explanatory, not predictive.
- TIME SERIES FORECAST: if question explicitly asks about FUTURE values or trends going
  forward (look for date/datetime columns; forecast the main numeric metric)
- REGRESSION: if question asks to predict a numeric value from features
- CLASSIFICATION: if question asks to predict a category/label
- CLUSTERING: if question asks about groups, segments, or profiles of customers/products

Then write Python code that defines EXACTLY four variables:

1. `predictions_df` — a pandas DataFrame with the analysis results.
   - For DIAGNOSTIC: a breakdown table comparing the metric across the relevant segments/
     periods (e.g. columns = [segment, 'metric_before', 'metric_after', 'delta']), so the
     decomposition is visible in the data, not just asserted in the narrative.
   - For FORECAST: columns = [date_col, 'actual', 'forecast'] or similar
   - For REGRESSION: columns = [feature, 'actual', 'predicted', 'residual']
   - For CLASSIFICATION: columns = [id_col, 'predicted_class', 'probability'] or a confusion summary
   - For CLUSTERING: original df with a new 'cluster' column + cluster summary

2. `fig` — a Plotly figure visualizing the model output clearly.
   - DIAGNOSTIC: grouped/side-by-side bar chart comparing the metric across segments/periods
   - FORECAST: line chart with actual vs. forecast (use different colors/dashes)
   - REGRESSION: scatter actual vs. predicted + a reference line
   - CLASSIFICATION: bar chart of class distribution or feature importance
   - CLUSTERING: scatter or bar chart colored by cluster
   - Set a descriptive title in Portuguese. Use professional color palettes.

3. `narrative` — a 2–4 sentence string in Portuguese for a non-technical business user.
   Include: what type of analysis was used, the main finding, and a business implication.
   Be specific with numbers, and the DIRECTION you state (increase/decrease) MUST match
   the actual numbers in `predictions_df` — never claim a trend the data doesn't show.

4. `model_info` — a Python dict with:
   {{"model_type": str, "task": str, "metrics": dict, "features": list, "target": str}}
   - task: "diagnostic" | "forecast" | "regression" | "classification" | "clustering"
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


_INCREASE_WORDS = (
    "aumento",
    "aumenta",
    "crescimento",
    "cresce",
    "crescer",
    "subir",
    "sobe",
    "alta",
    "melhora",
    "melhoria",
)
_DECREASE_WORDS = (
    "queda",
    "cai ",
    "caiu",
    "cair",
    "diminui",
    "diminuição",
    "redução",
    "reduz",
    "baixa",
    "piora",
    "declínio",
)


def _validate_narrative_consistency(
    narrative: str, predictions_df: pd.DataFrame, model_info: dict[str, Any]
) -> None:
    """Reject a narrative whose trend claim contradicts the numeric trend it describes.

    Only applies to diagnostic/forecast tasks, where the narrative makes a directional
    claim (increase/decrease) that should be derivable from `predictions_df` itself.
    Raising here feeds into the existing retry loop, so a contradictory narrative is
    treated the same as any other execution failure and triggers a fresh attempt.
    """
    if model_info.get("task") not in ("diagnostic", "forecast"):
        return

    text = narrative.lower()
    claims_increase = any(w in text for w in _INCREASE_WORDS)
    claims_decrease = any(w in text for w in _DECREASE_WORDS)
    if claims_increase == claims_decrease:
        return  # no clear single-direction claim to check

    numeric_cols = predictions_df.select_dtypes(include="number").columns
    if len(numeric_cols) == 0:
        return
    series = predictions_df[numeric_cols[-1]].dropna()
    if len(series) < 2:
        return

    delta = series.iloc[-1] - series.iloc[0]
    col = numeric_cols[-1]
    if claims_increase and delta < 0:
        raise ValueError(
            f"Narrative claims an increase but '{col}' decreased "
            f"({series.iloc[0]:.4g} → {series.iloc[-1]:.4g})."
        )
    if claims_decrease and delta > 0:
        raise ValueError(
            f"Narrative claims a decrease but '{col}' increased "
            f"({series.iloc[0]:.4g} → {series.iloc[-1]:.4g})."
        )


def run_science(
    df: pd.DataFrame,
    business_question: str,
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
) -> ScienceResult:
    """Run predictive analytics on the Silver DataFrame.

    Returns a ScienceResult dict (see ai_etl.core.analysis_types) with `task_question`
    left blank — callers running this per Planner sub-task fill it in themselves.

    Args:
        llm_provider_override / llm_model_override: Sprint 30/gap-closing (ADR-031
            §5) — see `agents/analysis/planner.py::plan_analysis_tasks`'s identical
            parameters for rationale. `None` (the default) — unchanged behavior.
    """
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

    # Modules (px/go/math) go through extra_modules, imported by dotted path
    # inside the sandboxed child — whole module objects cannot be pickled
    # across multiprocessing's process boundary (TypeError: cannot pickle
    # 'module' object), unlike the classes/functions below, which pickle by
    # reference just fine. See core/sandbox.py's execute_in_sandbox() docstring.
    extra_modules = {"px": "plotly.express", "go": "plotly.graph_objects", "math": "math"}

    extra_globals: dict[str, Any] = {
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

    llm = get_llm(provider=llm_provider_override, model=llm_model_override)
    last_error = ""
    last_code = ""
    attempt_usages: list[TokenUsage] = []
    # ADR-012: scaled once per call (not per attempt) — the input size doesn't
    # change across retries, only the generated code does.
    timeout_seconds = scale_timeout_for_rows(SCIENCE_TIMEOUT_SECONDS, len(df))

    for attempt in range(1, 4):
        if attempt == 1:
            prompt = base_prompt
        else:
            prompt = _RETRY_PREFIX.format(error=last_error, columns_list=columns_list) + base_prompt

        response = llm.invoke(prompt)
        attempt_usages.append(extract_token_usage(response))
        code = _strip_fences(str(response.content).strip())
        last_code = code

        sandbox_result = execute_in_sandbox(
            code,
            {"df": df.copy()},
            mode="script",
            result_vars=["predictions_df", "fig", "narrative", "model_info"],
            extra_globals=extra_globals,
            extra_modules=extra_modules,
            timeout_seconds=timeout_seconds,
        )

        if sandbox_result["timed_out"]:
            # Distinct failure mode from an exception (ADR-007) — feeds the same
            # last_error/retry-prompt channel, but with a message that hints the
            # LLM toward cheaper code on the next attempt instead of an opaque trace.
            last_error = f"Execution exceeded {timeout_seconds}s — simplify the computation"
            continue

        if sandbox_result["error"] is not None:
            last_error = textwrap.shorten(sandbox_result["error"], width=400, placeholder="...")
            continue

        predictions_df = sandbox_result["values"].get("predictions_df")
        fig = sandbox_result["values"].get("fig")
        narrative = sandbox_result["values"].get("narrative", "")
        model_info = sandbox_result["values"].get("model_info", {})

        try:
            if not isinstance(predictions_df, pd.DataFrame):
                raise TypeError(
                    f"predictions_df must be a pd.DataFrame, got {type(predictions_df).__name__}"
                )
            if fig is None:
                raise ValueError("fig must be defined (a Plotly Figure object)")
            if not isinstance(model_info, dict):
                raise TypeError(f"model_info must be a dict, got {type(model_info).__name__}")
            _validate_narrative_consistency(str(narrative), predictions_df, model_info)

            return {
                "task_question": "",
                "predictions_df": predictions_df,
                "fig": fig,
                "narrative": str(narrative),
                "model_info": model_info,
                "code": code,
                "attempts": attempt,
                "error": None,
                "tokens": sum_token_usage(*attempt_usages),
            }

        except Exception as exc:  # noqa: BLE001
            last_error = textwrap.shorten(str(exc), width=400, placeholder="...")

    return {
        "task_question": "",
        "predictions_df": pd.DataFrame(),
        "fig": None,
        "narrative": "Não foi possível executar a análise preditiva. Tente reformular a pergunta ou verifique se os dados têm estrutura adequada para modelagem.",
        "model_info": {},
        "code": last_code,
        "attempts": 3,
        "error": last_error,
        "tokens": sum_token_usage(*attempt_usages),
    }
