"""Shared LLM-codegen helpers (Sprint 33).

Extracted from `agents/analysis/analyst.py`, `agents/analysis/science.py`, and
`agents/analysis/planner.py`, which each duplicated an equivalent
`_strip_fences()`, and `analyst.py`/`science.py` which each additionally
duplicated a near-equivalent `_build_column_stats()`. Pure refactor — call
sites keep their public names/signatures unchanged, they just delegate to
these shared implementations now.

**Superset behavior for `build_column_stats()` (Sprint 33 extraction):**
`science.py`'s version had a datetime branch (`  {col}: datetime, range=...`)
that `analyst.py`'s and `planner.py`-adjacent copies didn't; `analyst.py`'s
version additionally included `samples=[...]` for high-cardinality
non-numeric columns that `science.py`'s copy didn't. Neither is a security-
relevant divergence (this only shapes LLM prompt context, not SQL/exec
input), so the shared version keeps the union of both — datetime detection
*and* high-cardinality samples — per the Sprint 33 spec's instruction to
reuse the most complete version without regressing simpler callers. Existing
tests for both agents assert only substrings (e.g. `"datetime" in stats`),
not exact output, so this superset introduces no regression for either.
"""

import pandas as pd


def strip_code_fences(text: str) -> str:
    """Remove a leading/trailing markdown code fence (```python / ```) from LLM output.

    Tolerates a missing closing fence (keeps whatever content follows the
    opening fence rather than dropping it).
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1  # skip opening fence line (```python or ```)
        end = len(lines)
        if lines[-1].strip() == "```":
            end -= 1
        text = "\n".join(lines[start:end])
    return text.strip()


def build_column_stats(df: pd.DataFrame) -> str:
    """Build a concise per-column stats summary to ground LLM codegen prompts.

    Numeric columns: min/max/mean/null count. Datetime columns: min/max
    range. Categorical columns (<=20 unique values): top-5 value counts.
    High-cardinality non-numeric columns: unique count, null count, and a
    small sample of values.
    """
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
                    sample_vals = df[col].dropna().head(3).tolist()
                    parts.append(
                        f"  {col}: high-cardinality ({n_unique} unique),"
                        f" samples={sample_vals}, nulls={nulls}"
                    )
        except Exception:  # noqa: BLE001
            parts.append(f"  {col}: (stats unavailable)")
    return "\n".join(parts) if parts else "(no stats)"
