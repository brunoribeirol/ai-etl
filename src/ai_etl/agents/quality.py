"""Quality Agent — deterministic data quality checks on the transformed DataFrame."""

from typing import Any

import pandas as pd

from ai_etl.audit.logger import log_action
from ai_etl.core.state import PipelineState

SEVERITY_ORDER = {"ok": 0, "warning": 1, "error": 2}
NULL_WARNING_THRESHOLD = 0.05  # 5% nulls → warning
NULL_ERROR_THRESHOLD = 0.20  # 20% nulls → error


def quality_node(state: PipelineState) -> PipelineState:
    """Run quality checks on transformed_data and produce a quality_report.

    Checks: null values, duplicates, type inconsistency (TODO), outliers (IQR).
    Severity levels: "ok" | "warning" | "error"
    If severity == "error", the graph routes to END (pipeline blocked).
    """
    if state.get("error"):
        return state

    df: pd.DataFrame = state["transformed_data"]  # type: ignore[assignment]  # non-None guaranteed by error short-circuit above
    checks: list[dict[str, Any]] = []

    checks.extend(_check_nulls(df))
    checks.append(_check_duplicates(df))
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
    # Set status here so the final state is never left as "running".
    final_status = "failed" if overall_severity == "error" else state["status"]
    return {**state, "quality_report": quality_report, "status": final_status, "audit_log": new_log}


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
