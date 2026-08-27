"""Transformer Agent — generates and executes Python transformation code via LLM."""

import json

from ai_etl.audit.logger import log_action
from ai_etl.core.llm import get_llm, invoke_llm
from ai_etl.core.locale import date_parse_hint, resolve_locale
from ai_etl.core.sandbox import execute_in_sandbox, scale_timeout_for_rows
from ai_etl.core.state import PipelineState

# Matches core/sandbox.py's own default — made explicit here (rather than relying on
# execute_in_sandbox()'s default parameter) so it has a name to scale via
# scale_timeout_for_rows() below (ADR-012).
TRANSFORMER_TIMEOUT_SECONDS = 30

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
- When parsing a date/datetime column with `pd.to_datetime(..., errors="coerce")`, a
  day-first dataset (DD/MM/YYYY) parsed with the default month-first reading will
  silently turn a large fraction of it into NaT instead of raising — always try BOTH
  readings for any ambiguous date column and keep whichever produced fewer NaT.
  {date_parse_hint}

Example fallback pattern (WRONG: no fallback attempted, silently drops day-first dates):
```python
df["date"] = pd.to_datetime(df["date"], errors="coerce")
```

RIGHT (tries both readings, keeps whichever produced fewer NaT — see the hint above for
which reading to attempt first for this tenant):
```python
parsed = pd.to_datetime(df["date"], errors="coerce")
non_null = df["date"].notna().sum()
if non_null > 0 and parsed.isna().sum() / non_null > 0.05:
    parsed_alt = pd.to_datetime(df["date"], errors="coerce", dayfirst=True)
    if parsed_alt.isna().sum() < parsed.isna().sum():
        parsed = parsed_alt
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

    provider_override = state.get("llm_provider_override")
    model_override = state.get("llm_model_override")
    llm = get_llm(provider=provider_override, model=model_override)
    if provider_override or model_override:
        # Sprint 30/gap-closing (ADR-031 §5) — see orchestrator_node's identical
        # comment for rationale.
        state = {
            **state,
            "audit_log": log_action(
                state,
                "transformer",
                "llm_override_used",
                {"provider": provider_override, "model": model_override},
            ),
        }
    transformations = state["pipeline_plan"].get("transformations", [])
    source_names = list(state["extracted_data"].keys())
    schema_summary = json.dumps(state["source_schemas"], indent=2, default=str)

    locale = resolve_locale(state.get("locale"))
    prompt = TRANSFORMER_PROMPT.format(
        transformations="\n".join(f"- {t}" for t in transformations),
        schema_summary=schema_summary,
        source_names=", ".join(source_names),
        date_parse_hint=date_parse_hint(locale),
    )

    last_error: str | None = None
    attempts = state.get("transformation_attempts", 0)
    # ADR-012: scaled by the largest source — Transformer receives every extracted
    # source at once (unlike Analyst/Science, which get a single already-merged
    # Silver DataFrame), so the widest single input bounds how long legitimate
    # cleaning/merging code over it can take.
    max_source_rows = max((len(df) for df in state["extracted_data"].values()), default=0)
    timeout_seconds = scale_timeout_for_rows(TRANSFORMER_TIMEOUT_SECONDS, max_source_rows)

    for _ in range(MAX_ATTEMPTS):
        attempts += 1
        response = invoke_llm(llm, prompt, provider_override)
        code = _clean_code(str(response.content))

        sandbox_result = execute_in_sandbox(
            code, state["extracted_data"], mode="function", timeout_seconds=timeout_seconds
        )
        result = sandbox_result["values"].get("result")
        error = sandbox_result["error"]

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
