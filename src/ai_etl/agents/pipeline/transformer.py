"""Transformer Agent — generates and executes Python transformation code via LLM."""

import json

from ai_etl.audit.logger import log_action
from ai_etl.core.llm import add_response_usage, get_llm, invoke_llm, response_text
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
  silently turn a large fraction of it into NaT instead of raising. But ISO
  (YYYY-MM-DD) strings must NEVER be re-read with `dayfirst=True` — that flag does
  not mean "this format is ambiguous, try both and compare," it means "reinterpret
  which token is day vs month," which silently SWAPS a perfectly valid ISO date
  whenever both day and month are ≤ 12 (roughly 39% of all real dates — this is the
  common case, not a rare edge case). The only way to tell an ISO-shaped string
  apart from a genuinely day-first one is to check whether it matches the ISO
  format directly — comparing two numeric-parse *results* against each other
  cannot distinguish "this is unambiguous ISO" from "this is genuinely ambiguous
  day-first text," because both produce two different-but-valid timestamps either
  way. Always try a strict ISO 8601 parse FIRST; only fall back to the day-first
  heuristic for values that don't match ISO. The strict check must be
  `format="ISO8601"`, which accepts a date alone ("2025-01-06") and a date followed
  by a time ("2025-01-06 00:04:25", "2025-01-06T00:04:25Z"). A date-only
  `format="%Y-%m-%d"` is NOT a valid ISO check: it rejects every ISO value that
  carries a time part and sends it to the day-first branch, which swaps day and
  month (real bug found 2026-10-12 on a public dataset of 20,000 timestamps:
  19,974 of them came back with day and month swapped). {date_parse_hint}

Example fallback pattern (WRONG: no fallback attempted, silently drops day-first dates):
```python
df["date"] = pd.to_datetime(df["date"], errors="coerce")
```

ALSO WRONG (comparing the two parses' *results* to detect "ambiguity" — this SILENTLY
CORRUPTS ISO dates whenever day and month are both ≤ 12, a real bug found 2026-09-04
that this exact "agreement check" pattern failed to catch, confirmed by re-testing
live: the two parses of an ISO date like "2026-02-01" don't error and don't agree —
they just produce two different wrong-or-right timestamps, so an agreement check
alone can't tell which one is real):
```python
default_parsed = pd.to_datetime(df["date"], errors="coerce")
dayfirst_parsed = pd.to_datetime(df["date"], errors="coerce", dayfirst=True)
parsed = dayfirst_parsed if (dayfirst_parsed != default_parsed).any() else default_parsed  # WRONG
df["date"] = parsed
```

ALSO WRONG, a subtler one (checking the whole column's format uniformly — this
DROPS valid dates whenever a column genuinely MIXES ISO and non-ISO strings, a
real bug found 2026-09-05 live testing: `pd.to_datetime(...)` with no explicit
`format=` infers ONE format from the column and applies it to every row, so a
column with SOME rows like "2026-03-15" and OTHER rows like "02/03/2026" turns
the minority-format rows into NaT even though each string is individually
parseable — this is a pandas gotcha, not a logic bug in the branch below, and
it means "is the whole column ISO?" is the wrong question to ask):
```python
non_null = df["date"].notna().sum()
strict_iso = pd.to_datetime(df["date"], format="ISO8601", errors="coerce")
if non_null > 0 and strict_iso.notna().sum() == non_null:
    parsed = strict_iso
else:
    # WRONG: still parses the whole column at once. If some rows are ISO and
    # others aren't, whichever format pandas infers from the column silently
    # fails on rows in the other format.
    default_parsed = pd.to_datetime(df["date"], errors="coerce")
    dayfirst_parsed = pd.to_datetime(df["date"], errors="coerce", dayfirst=True)
    parsed = (
        dayfirst_parsed
        if dayfirst_parsed.isna().sum() <= default_parsed.isna().sum()
        else default_parsed
    )
df["date"] = parsed
```

RIGHT (parse EACH VALUE independently with `.apply()`, so pandas never gets a
chance to infer one format for a column that may genuinely mix them — a
strict ISO check per value, falling back to this tenant's locale-preferred
reading only for that specific value if it isn't ISO-shaped):
```python
def _parse_one_date(value):
    if pd.isna(value):
        return pd.NaT
    # "ISO8601" (not "%Y-%m-%d"): must also match ISO values that carry a time part.
    iso = pd.to_datetime(value, format="ISO8601", errors="coerce")
    if pd.notna(iso):
        return iso
    # Not ISO-shaped — apply this tenant's locale preference to THIS value only.
    return pd.to_datetime(value, errors="coerce", dayfirst=True)  # or dayfirst=False, see hint above

df["date"] = df["date"].apply(_parse_one_date)
```

When cleaning a monetary/currency column that has a currency-symbol prefix (e.g.
"R$ 1.234,56", "$1,234.56"), strip the symbol as a LITERAL string — never write a
backslash before it. `\\$` is NOT a valid Python escape sequence: in a plain
(non-raw) string literal, Python keeps the backslash character literally, so
`'R\\$ '` is actually the 4-character string R, \\, $, space — which never
matches "R$ " (3 characters, no backslash) in the real data. `str.replace()`'s
first argument is a literal substring by default (`regex=False`), so `$` needs
no escaping at all; write it exactly as it appears in the data.

WRONG (real bug, found 2026-09-04 live testing, confirmed twice independently in
generated code — the invalid `\\$` escape silently makes every `.str.replace()`
call a no-op, so the currency symbol is never stripped, and the whole column
becomes NaN after `pd.to_numeric`, not just some rows):
```python
df["preco"] = df["preco"].str.replace('R\\$ ', '').str.replace('.', '').str.replace(',', '.')
df["preco"] = pd.to_numeric(df["preco"], errors="coerce")
```

RIGHT (no backslash before the currency symbol; `regex=False` made explicit so `.`
is treated as a literal thousands-separator character, not "match any character"):
```python
df["preco"] = (
    df["preco"]
    .str.replace("R$ ", "", regex=False)
    .str.replace(".", "", regex=False)
    .str.replace(",", ".", regex=False)
)
df["preco"] = pd.to_numeric(df["preco"], errors="coerce")
```

When coercing any column to a numeric type, a value that is genuinely not a number
(e.g. the text "cem" in a quantity column) must become `NaN`, NOT `0`. Use
`pd.to_numeric(col, errors="coerce")` and do NOT follow it with `.fillna(0)` on a
column whose non-numeric entries are *bad data* rather than a true zero — filling them
with 0 silently invents a real value (it turned "cem", i.e. 100 units, into 0 units in a
real run), which then corrupts every downstream sum/mean/ranking without any trace. Leave
such values as NaN so the Quality step can see and report them; only fillna(0) when a
missing value legitimately means zero for that column's real-world meaning, and say so.

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

    llm_usage = state.get("llm_usage")
    for _ in range(MAX_ATTEMPTS):
        attempts += 1
        response = invoke_llm(llm, prompt, provider_override)
        llm_usage = add_response_usage(llm_usage, response)
        code = _clean_code(response_text(response))

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
                "llm_usage": llm_usage,
                "audit_log": new_log,
            }

        last_error = error
        hint = ""
        if error and "__import__" in error:
            hint = (
                " You used an `import` statement — remove it entirely, pd/np are already available."
            )
        # The failed code goes back with its error. It used to be the error alone, so
        # the model rewrote from scratch without seeing what it had written. Real runs,
        # 2026-10-12: the same mistake came back in all three attempts (columns renamed
        # and then referred to by the old names; requested column names missing).
        prompt += (
            f"\n\nPrevious attempt failed. Code of that attempt:\n{code}\n\n"
            f"Error it raised:\n{error}\n\n"
            f"Correct that code and return the complete transform() function.{hint}"
        )

    new_log = log_action(
        state, "transformer", "code_failed", {"attempts": attempts, "error": last_error}
    )
    return {
        **state,
        # Wave 1 item 4b, 2026-09-05 mega-audit remediation
        # (docs/work/2026-09-05-mega-audit-remediation-plan.md): before this
        # fix, a total failure never set `transformation_code` at all, so the
        # History page's Code tab showed "No code available for this run."
        # with no way to see what was actually tried — confirmed live for the
        # nested-JSON case (Data Engineer persona, `pedidos_aninhado.json`).
        # `code` here is the last attempt's generated code (the for-loop
        # variable, still in scope after the loop exits) — showing it doesn't
        # make the run succeed, but it turns an opaque "Failed" into something
        # a technical operator can actually audit and act on.
        "transformation_code": code,
        "transformation_attempts": attempts,
        "transformation_error": last_error,
        "llm_usage": llm_usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
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
