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

import ast
import re
from collections.abc import Iterator, Mapping

import pandas as pd

# Appended to the error fed back to the model when the sandbox refused an import
# (the same hint the Transformer already gives, agents/pipeline/transformer.py).
IMPORT_ERROR_HINT = (
    " You wrote an `import` statement: remove every import. Only the names listed in"
    " the instructions exist in this environment; nothing else can be imported."
)


def import_error_hint(error: str) -> str:
    """`IMPORT_ERROR_HINT` when `error` is the sandbox refusing an import, else ""."""
    return IMPORT_ERROR_HINT if "__import__" in error else ""


def drop_redundant_imports(code: str, provided: Mapping[str, str]) -> str:
    """Remove top-level imports that only bind names the sandbox already provides.

    The sandbox has no import mechanism (ADR-003), so any `import` raises
    "__import__ not found", including `import pandas as pd`, which asks for a name
    that is already there. Real runs, 2026-10-12: with `gpt-4o` and
    `claude-haiku-4-5` every sub-analysis failed this way (case-study batches), and
    with the default model both Gold sub-analyses of a production run failed three
    attempts each on `import pandas as pd` / `import plotly.express as px`.

    `provided` maps each name present in the sandbox to the root package it comes
    from (`{"pd": "pandas", "px": "plotly"}`). A statement is removed only when
    every name it binds is provided under that same name and from that same root
    package, so `import numpy as pd` or `import requests` stay and fail as before.
    Code that does not parse is returned unchanged: the sandbox reports the syntax
    error.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    def bound_names(node: ast.stmt) -> list[tuple[str, str]] | None:
        if isinstance(node, ast.Import):
            return [
                (alias.asname or alias.name.split(".")[0], alias.name.split(".")[0])
                for alias in node.names
            ]
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            root = node.module.split(".")[0]
            return [(alias.asname or alias.name, root) for alias in node.names]
        return None

    def lines_of(node: ast.stmt) -> set[int]:
        return set(range(node.lineno - 1, node.end_lineno or node.lineno))

    redundant: list[ast.stmt] = []
    kept_lines: set[int] = set()
    for node in tree.body:
        names = bound_names(node)
        if names and all(provided.get(name) == root for name, root in names):
            redundant.append(node)
        else:
            kept_lines |= lines_of(node)

    drop: set[int] = set()
    for node in redundant:
        # A line shared with a statement that stays (`import x; y = 1`) is left alone.
        if not lines_of(node) & kept_lines:
            drop |= lines_of(node)
    if not drop:
        return code
    return "\n".join(
        line for index, line in enumerate(code.splitlines()) if index not in drop
    ).strip()


# A decimal number ("26,6", "0.85") or a percentage ("45%") written as text.
_TYPED_FIGURE = re.compile(r"\d+[.,]\d+|\d+\s*%")
_FORMAT_FIELD = re.compile(r"\{[^{}]*\}")


def _string_literals(node: ast.AST) -> Iterator[str]:
    """The pieces of literal text inside an expression that builds a string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.JoinedStr):  # f-string: only the text between the fields
        for part in node.values:
            yield from _string_literals(part)
    elif isinstance(node, ast.BinOp):  # "a" + b, "a %s" % b
        yield from _string_literals(node.left)
        yield from _string_literals(node.right)
    elif isinstance(node, ast.IfExp):
        yield from _string_literals(node.body)
        yield from _string_literals(node.orelse)
    elif isinstance(node, (ast.List, ast.Tuple)):
        for element in node.elts:
            yield from _string_literals(element)
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "format":  # "taxa de {:.1f}%".format(x): drop the fields
            for text in _string_literals(node.func.value):
                yield _FORMAT_FIELD.sub("", text)
        elif node.func.attr == "join":  # " ".join([...])
            for argument in node.args:
                yield from _string_literals(argument)


def hand_typed_figures(code: str, variable: str = "narrative") -> list[str]:
    """Decimal numbers and percentages typed as text in what `code` assigns to `variable`.

    The narrative of a sub-analysis is a variable assigned by the generated code. When
    the model writes the figures as text, they are guesses made before anything was
    computed. Real run in production, 2026-10-12 (Kaggle Telco churn): the text said
    "sem parceiro 30,5%, com parceiro 23,5%"; the table computed by the same code, and a
    plain-pandas reference, said 33.0 and 19.7. Four of four diagnostic texts of that
    run had such figures.

    Only literal text is inspected: a figure that reaches the narrative through an
    f-string field, `format` or a variable is computed and is not reported. Whole
    numbers without a percent sign ("top 5", "2025") are not reported. A string built
    in another variable first is not seen. Code that does not parse returns no figure:
    the sandbox reports the syntax error.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    figures: list[str] = []
    for node in ast.walk(tree):
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == variable for t in node.targets):
                value = node.value
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            if isinstance(node.target, ast.Name) and node.target.id == variable:
                value = node.value
        if value is not None:
            for text in _string_literals(value):
                figures.extend(_TYPED_FIGURE.findall(text))
    return figures


def typed_figures_error(figures: list[str], data_name: str) -> str:
    """The message fed back to the model when `hand_typed_figures` found something."""
    shown = ", ".join(figures[:6])
    return (
        f"The narrative states figures typed by hand ({shown}). Do not type any decimal "
        f"number or percentage in the narrative text: compute each figure from "
        f"`{data_name}` or from a variable and insert it with an f-string field, so the "
        f"text can only say what the code computed."
    )


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
        except Exception:
            parts.append(f"  {col}: (stats unavailable)")
    return "\n".join(parts) if parts else "(no stats)"
