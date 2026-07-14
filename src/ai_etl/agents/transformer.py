"""Transformer Agent — generates and executes Python transformation code via LLM."""

import json

from ai_etl.audit.logger import log_action
from ai_etl.core.llm import get_llm
from ai_etl.core.sandbox import execute_in_sandbox
from ai_etl.core.state import PipelineState

TRANSFORMER_PROMPT = """You are a Python data transformation expert.

Pipeline transformations to implement:
{transformations}

Available DataFrames (already loaded, passed as the `dfs` dict):
{schema_summary}

Write a Python function with this exact signature:
```python
def transform(dfs: dict) -> pd.DataFrame:
    # dfs keys: {source_names}
    ...
    return result_df
```

Rules:
- Use ONLY pandas (pd) and numpy (np), already available as pre-loaded variables.
- Do NOT write any `import` statement — the sandbox has no `__import__` builtin, so any
  `import` line, including `import re` or `from datetime import ...`, will crash with
  "ImportError: __import__ not found". Everything you need is reachable via pd/np.
- The function must return a single pd.DataFrame.
- Handle edge cases (empty DataFrames, missing columns).
- Do not read from files or databases — data is already in `dfs`.
- When parsing a date/datetime column with `pd.to_datetime(..., errors="coerce")`, the
  default parse assumes month-first (MM/DD/YYYY) and will silently turn a large fraction
  of a day-first dataset (DD/MM/YYYY, common outside the US) into NaT instead of raising.
  Try the default parse, and if more than 5% of non-null values become NaT, retry the
  same column with `dayfirst=True` and keep whichever attempt produced fewer NaT.

WRONG (silently drops most day-first dates as NaT, no fallback attempted):
```python
df["date"] = pd.to_datetime(df["date"], errors="coerce")
```

RIGHT (falls back to dayfirst parsing when the default parse looks wrong):
```python
parsed = pd.to_datetime(df["date"], errors="coerce")
non_null = df["date"].notna().sum()
if non_null > 0 and parsed.isna().sum() / non_null > 0.05:
    parsed_dayfirst = pd.to_datetime(df["date"], errors="coerce", dayfirst=True)
    if parsed_dayfirst.isna().sum() < parsed.isna().sum():
        parsed = parsed_dayfirst
df["date"] = parsed
```

Respond ONLY with the Python function. No explanation, no markdown fences.
"""

MAX_ATTEMPTS = 3


def transformer_node(state: PipelineState) -> PipelineState:
    """Generate transformation code via LLM and execute in sandbox.

    Retries up to MAX_ATTEMPTS times, feeding the error back to the LLM.
    Sets state["error"] if all attempts fail.
    """
    if state.get("error"):
        return state

    llm = get_llm()
    transformations = state["pipeline_plan"].get("transformations", [])
    source_names = list(state["extracted_data"].keys())
    schema_summary = json.dumps(state["source_schemas"], indent=2, default=str)

    prompt = TRANSFORMER_PROMPT.format(
        transformations="\n".join(f"- {t}" for t in transformations),
        schema_summary=schema_summary,
        source_names=", ".join(source_names),
    )

    last_error: str | None = None
    attempts = state.get("transformation_attempts", 0)

    for _ in range(MAX_ATTEMPTS):
        attempts += 1
        response = llm.invoke(prompt)
        code = _clean_code(str(response.content))

        result, error = execute_in_sandbox(code, state["extracted_data"])

        if error is None and result is not None:
            new_log = log_action(
                state,
                "transformer",
                "code_executed",
                {"attempts": attempts, "output_shape": list(result.shape)},
            )
            return {
                **state,
                "transformation_code": code,
                "transformed_data": result,
                "transformation_attempts": attempts,
                "transformation_error": None,
                "audit_log": new_log,
            }

        last_error = error
        hint = ""
        if error and "__import__" in error:
            hint = (
                " You used an `import` statement — remove it entirely, pd/np are already available."
            )
        prompt += f"\n\nPrevious attempt failed:\n{error}\n\nFix the transform() function.{hint}"

    new_log = log_action(
        state, "transformer", "code_failed", {"attempts": attempts, "error": last_error}
    )
    return {
        **state,
        "transformation_attempts": attempts,
        "transformation_error": last_error,
        "error": f"Transformer failed after {attempts} attempts: {last_error}",
        "status": "failed",
        "audit_log": new_log,
    }


def _clean_code(raw: str) -> str:
    """Strip markdown code fences from LLM output."""
    raw = raw.strip()
    if raw.startswith("```python"):
        raw = raw[len("```python") :].strip()
    if raw.startswith("```"):
        raw = raw[3:].strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()
    return raw
