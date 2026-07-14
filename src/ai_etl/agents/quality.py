"""Quality Agent — deterministic data quality checks on the transformed DataFrame."""

from typing import Any

import pandas as pd

from ai_etl.audit.logger import log_action
from ai_etl.core.state import PipelineState

SEVERITY_ORDER = {"ok": 0, "warning": 1, "error": 2}
NULL_WARNING_THRESHOLD = 0.05  # 5% nulls → warning
NULL_ERROR_THRESHOLD = 0.20  # 20% nulls → error
IDENTIFIER_UNIQUENESS_THRESHOLD = 0.8  # column looks like a name/identifier, not a category


def quality_node(state: PipelineState) -> PipelineState:
    """Run quality checks on transformed_data and produce a quality_report.

    Checks: null values, exact duplicates, logical (by-name) duplicates,
    type inconsistency, outliers (IQR).
    Severity levels: "ok" | "warning" | "error"
    If severity == "error", the graph routes to END (pipeline blocked).
    """
    if state.get("error"):
        return state

    df: pd.DataFrame = state["transformed_data"]  # type: ignore[assignment]  # non-None guaranteed by error short-circuit above
    checks: list[dict[str, Any]] = []

    checks.extend(_check_nulls(df))
    checks.append(_check_duplicates(df))
    checks.extend(_check_logical_duplicates(df))
    checks.extend(_check_types(df, state["source_schemas"]))
    checks.extend(_check_outliers_iqr(df))

    overall_severity = max(
        (c["severity"] for c in checks),
        key=lambda s: SEVERITY_ORDER[s],
        default="ok",
    )

    error_count = sum(1 for c in checks if c["severity"] == "error")
    warning_count = sum(1 for c in checks if c["severity"] == "warning")

    quality_report = {
        "checks": checks,
        "severity": overall_severity,
        "summary": f"{len(checks)} checks: {warning_count} warnings, {error_count} errors",
    }

    new_log = log_action(
        state,
        "quality",
        "checks_complete",
        {"severity": overall_severity, "checks": len(checks), "errors": error_count},
    )
    # When severity is "error", the graph routes directly to END (bypassing Loader).
    # Set status AND error here so the final state is never left "running" with no
    # explanation — callers (e.g. app.py) render `error` directly and must not see None.
    blocked = overall_severity == "error"
    final_status = "failed" if blocked else state["status"]
    final_error: str | None = (
        f"Quality gate blocked the pipeline: {quality_report['summary']}"
        if blocked
        else state.get("error")
    )
    return {
        **state,
        "quality_report": quality_report,
        "status": final_status,
        "error": final_error,
        "audit_log": new_log,
    }


def _check_nulls(df: pd.DataFrame) -> list[dict[str, Any]]:
    results = []
    for col in df.columns:
        null_ratio = float(df[col].isnull().mean())
        if null_ratio == 0.0:
            continue
        severity = "ok"
        if null_ratio >= NULL_ERROR_THRESHOLD:
            severity = "error"
        elif null_ratio >= NULL_WARNING_THRESHOLD:
            severity = "warning"
        results.append(
            {
                "check": "null",
                "column": col,
                "null_ratio": round(null_ratio, 4),
                "severity": severity,
            }
        )
    return results


def _check_duplicates(df: pd.DataFrame) -> dict[str, Any]:
    dup_count = int(df.duplicated().sum())
    return {
        "check": "duplicate",
        "count": dup_count,
        "severity": "warning" if dup_count > 0 else "ok",
    }


def _check_logical_duplicates(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Flag repeated values in columns that behave like an identifier/name.

    `_check_duplicates` only catches fully identical rows. A column with high
    uniqueness (e.g. a product or customer name) that still has repeated values
    signals *logical* duplicates — the same entity recorded more than once with
    different attributes — which exact row-matching would miss entirely.
    Low-cardinality columns (categories, flags) are excluded on purpose: repeats
    there are expected, not a data-quality signal.
    """
    results = []
    for col in df.select_dtypes(include=["object", "string"]).columns:
        non_null = df[col].dropna()
        if non_null.empty:
            continue
        uniqueness = non_null.nunique() / len(non_null)
        if uniqueness < IDENTIFIER_UNIQUENESS_THRESHOLD:
            continue
        counts = non_null.value_counts()
        repeated = counts[counts > 1]
        if repeated.empty:
            continue
        results.append(
            {
                "check": "duplicate_by_column",
                "column": col,
                "group_count": int(len(repeated)),
                "row_count": int(repeated.sum()),
                "sample_values": repeated.head(5).index.tolist(),
                "severity": "warning",
            }
        )
    return results


def _check_types(df: pd.DataFrame, source_schemas: dict[str, Any]) -> list[dict[str, Any]]:
    """Flag columns whose transformed dtype diverges from every source schema.

    Comparison is by column name against the dtypes extracted per source, since
    the Transformer may rename, drop or add columns freely — a mismatch here is
    a signal for review, not a pipeline-blocking condition.
    """
    expected_dtypes: dict[str, set[str]] = {}
    for schema in source_schemas.values():
        for col, dtype in schema.get("dtypes", {}).items():
            expected_dtypes.setdefault(col, set()).add(dtype)

    results = []
    for col in df.columns:
        expected = expected_dtypes.get(col)
        if expected is None:
            continue
        actual = str(df[col].dtype)
        if actual not in expected:
            results.append(
                {
                    "check": "type_mismatch",
                    "column": col,
                    "expected_dtype": sorted(expected),
                    "actual_dtype": actual,
                    "severity": "warning",
                }
            )
    return results


def _check_outliers_iqr(df: pd.DataFrame) -> list[dict[str, Any]]:
    results = []
    for col in df.select_dtypes(include="number").columns:
        q1 = float(df[col].quantile(0.25))
        q3 = float(df[col].quantile(0.75))
        iqr = q3 - q1
        if iqr == 0:
            continue
        lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        outlier_count = int(((df[col] < lower) | (df[col] > upper)).sum())
        if outlier_count > 0:
            results.append(
                {
                    "check": "outlier_iqr",
                    "column": col,
                    "outlier_count": outlier_count,
                    "severity": "warning",
                }
            )
    return results
