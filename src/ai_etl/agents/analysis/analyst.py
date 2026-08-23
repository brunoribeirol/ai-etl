"""Analyst Agent — Gold layer.

Receives the Silver DataFrame + a business question and produces:
- gold_df  : aggregated/processed DataFrame answering the question
- fig      : Plotly figure visualizing the result
- narrative: plain-language insight in Portuguese
"""

import json
import textwrap

import pandas as pd

from ai_etl.agents._llm_codegen import build_column_stats as _build_column_stats
from ai_etl.agents._llm_codegen import strip_code_fences as _strip_fences
from ai_etl.core.analysis_types import GoldResult, TokenUsage
from ai_etl.core.llm import extract_token_usage, get_llm, sum_token_usage
from ai_etl.core.sandbox import execute_in_sandbox, scale_timeout_for_rows

# Lower than Transformer's default (30s, core/sandbox.py) — deliberately, per
# ADR-007's flagged-but-unfixed compounding concern. Analyst retries up to 3x
# (`for attempt in range(1, 4)` below), so worst-case sandbox time alone is
# 3 x timeout_seconds, on top of 3 LLM round-trips, for a *single* Gold
# sub-task — and one business question can fan out into several sub-tasks
# (pipeline_service.run_analysis_tasks). Gold code here is aggregation/filter/
# plot work over an already-loaded Silver DataFrame, not model training, so it
# has no legitimate reason to run long; 15s is generous headroom over any
# expected case while keeping the worst-case ceiling (3 x 15s = 45s) tolerable
# for an interactive Streamlit session. Revisit if real usage shows otherwise.
ANALYST_TIMEOUT_SECONDS = 15

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


def run_analyst(
    df: pd.DataFrame,
    business_question: str,
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
) -> GoldResult:
    """Answer a business question from a Silver DataFrame.

    Returns a GoldResult dict (see ai_etl.core.analysis_types) with `task_question`
    left blank — callers running this per Planner sub-task fill it in themselves.

    Args:
        llm_provider_override / llm_model_override: Sprint 30/gap-closing (ADR-031
            §5) — see `agents/analysis/planner.py::plan_analysis_tasks`'s identical
            parameters for rationale. `None` (the default) — unchanged behavior.
    """
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

    llm = get_llm(provider=llm_provider_override, model=llm_model_override)
    last_error = ""
    last_code = ""
    attempt_usages: list[TokenUsage] = []
    # ADR-012: scaled once per call (not per attempt) — the input size doesn't
    # change across retries, only the generated code does.
    timeout_seconds = scale_timeout_for_rows(ANALYST_TIMEOUT_SECONDS, len(df))

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
            result_vars=["gold_df", "fig", "narrative"],
            extra_modules={"px": "plotly.express", "go": "plotly.graph_objects"},
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

        gold_df = sandbox_result["values"].get("gold_df")
        fig = sandbox_result["values"].get("fig")
        narrative = sandbox_result["values"].get("narrative", "")

        try:
            if not isinstance(gold_df, pd.DataFrame):
                raise TypeError(f"gold_df must be a pd.DataFrame, got {type(gold_df).__name__}")
            if fig is None:
                raise ValueError("fig must be defined (a Plotly Figure object)")

            return {
                "task_question": "",
                "gold_df": gold_df,
                "fig": fig,
                "narrative": str(narrative),
                "code": code,
                "attempts": attempt,
                "error": None,
                "tokens": sum_token_usage(*attempt_usages),
            }

        except Exception as exc:  # noqa: BLE001
            last_error = textwrap.shorten(str(exc), width=400, placeholder="...")

    return {
        "task_question": "",
        "gold_df": pd.DataFrame(),
        "fig": None,
        "narrative": "Não foi possível gerar a análise automaticamente. Tente reformular a pergunta com mais detalhes.",
        "code": last_code,
        "attempts": 3,
        "error": last_error,
        "tokens": sum_token_usage(*attempt_usages),
    }
