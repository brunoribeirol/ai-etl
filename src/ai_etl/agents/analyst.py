"""Analyst Agent — Gold layer.

Receives the Silver DataFrame + a business question and produces:
- gold_df  : aggregated/processed DataFrame answering the question
- fig      : Plotly figure visualizing the result
- narrative: plain-language insight in Portuguese
"""

import json
import textwrap
from typing import Any

import pandas as pd

from ai_etl.core.llm import get_llm

_PROMPT_TEMPLATE = """\
You are an expert data analyst. You have access to a cleaned pandas DataFrame called `df`.

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

Write Python code that defines EXACTLY three variables:

1. `gold_df` — a pandas DataFrame with aggregated/filtered data that DIRECTLY answers the question.
   - Group, filter, sort, or aggregate as needed.
   - Include only the most relevant columns (2–6 columns max).
   - If the question asks about "top N", limit to that N.

2. `fig` — a Plotly figure that best visualizes `gold_df`.
   - Use `px` (plotly.express) for simple charts or `go` (plotly.graph_objects) for composites.
   - Choose the most appropriate chart type (bar, line, pie, scatter, etc.).
   - Set a descriptive title in Portuguese.
   - Set Portuguese axis labels where applicable.
   - Use a clean color scheme (e.g. `color_discrete_sequence=px.colors.qualitative.Set2`).

3. `narrative` — a 2–3 sentence string in Portuguese explaining the main insight for a
   non-technical business user. Be specific: include actual numbers from `gold_df`.

## Critical rules
- `df` is already loaded — do NOT read any files.
- Use ONLY these libraries: `pd`, `np`, `px`, `go`. Do NOT import anything else.
- Use ONLY the exact column names listed above. Access columns with `df['column_name']`.
- Handle edge cases: if a column is missing, use `df.columns.tolist()` to inspect.
- For numeric aggregations, use `.fillna(0)` to avoid NaN issues.
- `gold_df` must be a pd.DataFrame (not a Series). Use `.reset_index()` if needed.
- `fig` must be a Plotly Figure object. Do NOT call `fig.show()`.
- Respond ONLY with valid Python code. No markdown fences. No explanations. No comments.
"""

_RETRY_PREFIX = """\
The previous attempt failed. Here is the error:

  {error}

Rewrite the code from scratch. Do NOT repeat the same mistake.
Ensure `gold_df` is a pd.DataFrame, `fig` is a Plotly Figure, and `narrative` is a string.
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
    },
}


def _build_column_stats(df: pd.DataFrame) -> str:
    """Build a concise stats summary to help the LLM make better chart decisions."""
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
                    sample_vals = df[col].dropna().head(3).tolist()
                    parts.append(
                        f"  {col}: high-cardinality ({n_unique} unique),"
                        f" samples={sample_vals}, nulls={nulls}"
                    )
        except Exception:  # noqa: BLE001
            parts.append(f"  {col}: (stats unavailable)")
    return "\n".join(parts) if parts else "(no stats)"


def _strip_fences(code: str) -> str:
    """Remove markdown code fences from LLM output."""
    code = code.strip()
    if code.startswith("```"):
        lines = code.splitlines()
        start = 1  # skip opening fence line (```python or ```)
        end = len(lines)
        if lines[-1].strip() == "```":
            end -= 1
        code = "\n".join(lines[start:end])
    return code.strip()


def run_analyst(df: pd.DataFrame, business_question: str) -> dict[str, Any]:
    """Answer a business question from a Silver DataFrame.

    Returns a dict with keys:
        gold_df   : pd.DataFrame
        fig       : plotly Figure or None
        narrative : str
        code      : str (generated Python)
        attempts  : int
        error     : str or None
    """
    import numpy as np
    import plotly.express as px
    import plotly.graph_objects as go

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

        safe_globals = {**_SAFE_GLOBALS, "pd": pd, "np": np, "px": px, "go": go}
        local_env: dict[str, Any] = {"df": df.copy()}

        try:
            exec(code, safe_globals, local_env)  # noqa: S102

            gold_df = local_env.get("gold_df")
            fig = local_env.get("fig")
            narrative = local_env.get("narrative", "")

            if not isinstance(gold_df, pd.DataFrame):
                raise TypeError(f"gold_df must be a pd.DataFrame, got {type(gold_df).__name__}")
            if fig is None:
                raise ValueError("fig must be defined (a Plotly Figure object)")

            return {
                "gold_df": gold_df,
                "fig": fig,
                "narrative": str(narrative),
                "code": code,
                "attempts": attempt,
                "error": None,
            }

        except Exception as exc:  # noqa: BLE001
            last_error = textwrap.shorten(str(exc), width=400, placeholder="...")

    return {
        "gold_df": pd.DataFrame(),
        "fig": None,
        "narrative": "Não foi possível gerar a análise automaticamente. Tente reformular a pergunta com mais detalhes.",
        "code": last_code,
        "attempts": 3,
        "error": last_error,
    }
