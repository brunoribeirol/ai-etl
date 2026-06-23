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
- Use ONLY pandas (pd) and numpy (np). No other imports are available.
- The function must return a single pd.DataFrame.
- Handle edge cases (empty DataFrames, missing columns).
- Do not read from files or databases — data is already in `dfs`.

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
        prompt += f"\n\nPrevious attempt failed:\n{error}\n\nFix the transform() function."

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
