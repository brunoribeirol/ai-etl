"""Output sanity-check for Gold/Science results (Sprint 21, ADR-026).

`core/sandbox.py` (ADR-007) only guarantees the LLM-generated Analyst/Science code
*ran* without raising — not that its *logic* is correct. This module runs a fixed set
of deterministic, statistical checks over a successful `gold_df`/`predictions_df`
against the `silver_df` it was derived from, to catch a result that is internally
consistent (right dtypes, right shape) but numerically implausible.

Pure functions, no I/O — mirrors `core/drift.py`'s own shape. Every check emits one
entry per rule it evaluates whenever the rule is applicable (`severity: "ok"` on pass),
mirroring `agents/pipeline/quality.py::_check_custom_rules` (ADR-023) — so a run's manifest
shows every check that ran, not just the ones that fired. A non-applicable check (e.g.
no shared numeric column between `gold_df` and `silver_df`) is skipped entirely rather
than emitted as a false "ok".

Severity here is always "ok" or "warning", never "error" — see ADR-026 Decision 1 for
why a sanity-check finding flags a result with a caveat instead of withholding it.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ai_etl.core.analysis_types import OutputSanityCheck, OutputSanityCheckEntry

# Sum of an aggregated/filtered column may equal but never legitimately exceed the
# source total — this tolerance only absorbs float rounding noise, not real drift.
_SUM_TOLERANCE_RATIO = 0.005

# How far past the historical [min, max] range (as a multiple of that range's span) a
# forecast/regression prediction may go before being flagged. Deliberately generous —
# a real trend or seasonal forecast can legitimately exceed historical bounds; this
# only catches an order-of-magnitude error, not a normal extrapolation.
_PREDICTION_RANGE_MULTIPLIER = 3.0

# Textbook-valid ranges for well-known metric keys. A metric outside its
# mathematically valid range means the sandboxed code computed it against
# mismatched arrays/columns — a bad-but-valid fit (e.g. r2=-0.3) is not flagged.
_METRIC_RANGES: dict[str, tuple[float, float]] = {
    "r2": (float("-inf"), 1.0 + _SUM_TOLERANCE_RATIO),
    "accuracy": (-1.0, 1.0),
    "silhouette": (-1.0, 1.0),
    "silhouette_score": (-1.0, 1.0),
    "rmse": (0.0, float("inf")),
    "mae": (0.0, float("inf")),
    "mse": (0.0, float("inf")),
}


def _ok(check: str, detail: str) -> OutputSanityCheckEntry:
    return {"check": check, "severity": "ok", "detail": detail}


def _warning(check: str, detail: str) -> OutputSanityCheckEntry:
    return {"check": check, "severity": "warning", "detail": detail}


def _summarize(checks: list[OutputSanityCheckEntry]) -> OutputSanityCheck:
    warnings = [c for c in checks if c["severity"] == "warning"]
    severity: Any = "warning" if warnings else "ok"
    summary = (
        f"{len(checks)} sanity check(s): {len(warnings)} warning(s)"
        if checks
        else "No applicable sanity checks for this result."
    )
    return {"checks": checks, "severity": severity, "summary": summary}


def check_gold_output(
    gold_df: pd.DataFrame, silver_df: pd.DataFrame, narrative: str
) -> OutputSanityCheck:
    """Sanity-check one Gold (Analyst) sub-task's `gold_df` against the Silver data
    it was derived from.

    Checks: `sum_conservation` (an aggregation can't sum to more than its source),
    `row_count_bound` (a filter/aggregation can't produce more rows than its input),
    `empty_result` (a narrative citing a number with no data behind it).
    """
    checks: list[OutputSanityCheckEntry] = []

    if isinstance(silver_df, pd.DataFrame) and not silver_df.empty and not gold_df.empty:
        gold_numeric = set(gold_df.select_dtypes(include="number").columns)
        silver_numeric = set(silver_df.select_dtypes(include="number").columns)
        shared = gold_numeric & silver_numeric
        for col in sorted(shared):
            gold_sum = float(gold_df[col].sum())
            silver_sum = float(silver_df[col].sum())
            limit = silver_sum * (1 + _SUM_TOLERANCE_RATIO)
            # A negative silver_sum flips what "exceeds" means; only check the common,
            # unambiguous case of a non-negative source total.
            if silver_sum >= 0 and gold_sum > limit:
                checks.append(
                    _warning(
                        "sum_conservation",
                        f"Column '{col}': gold_df sum ({gold_sum:.4g}) exceeds the Silver "
                        f"total ({silver_sum:.4g}) — an aggregation/filter should never "
                        "produce a larger sum than its source.",
                    )
                )
            else:
                checks.append(
                    _ok(
                        "sum_conservation",
                        f"Column '{col}': gold_df sum ({gold_sum:.4g}) is within the Silver "
                        f"total ({silver_sum:.4g}).",
                    )
                )

    if isinstance(silver_df, pd.DataFrame) and not silver_df.empty:
        if len(gold_df) > len(silver_df):
            checks.append(
                _warning(
                    "row_count_bound",
                    f"gold_df has more rows ({len(gold_df)}) than silver_df ({len(silver_df)}) "
                    "— an aggregation/filter should never grow the row count.",
                )
            )
        else:
            checks.append(
                _ok(
                    "row_count_bound",
                    f"gold_df row count ({len(gold_df)}) is within silver_df's ({len(silver_df)}).",
                )
            )

    if gold_df.empty and any(ch.isdigit() for ch in narrative):
        checks.append(
            _warning(
                "empty_result",
                "narrative cites a specific number, but gold_df is empty — no data backs it.",
            )
        )

    return _summarize(checks)


def check_science_output(
    predictions_df: pd.DataFrame, model_info: dict[str, Any], silver_df: pd.DataFrame
) -> OutputSanityCheck:
    """Sanity-check one Science sub-task's `predictions_df`/`model_info` against the
    Silver data it was derived from.

    Checks: `metric_range` (a metric outside its mathematically valid range),
    `prediction_range` (a forecast/regression value far outside historical bounds).
    """
    checks: list[OutputSanityCheckEntry] = []

    metrics = model_info.get("metrics", {})
    if isinstance(metrics, dict):
        for name, value in metrics.items():
            bounds = _METRIC_RANGES.get(str(name).lower())
            if bounds is None or not isinstance(value, (int, float)):
                continue
            low, high = bounds
            if low <= float(value) <= high:
                checks.append(
                    _ok("metric_range", f"'{name}' ({value:.4g}) is within a valid range.")
                )
            else:
                checks.append(
                    _warning(
                        "metric_range",
                        f"'{name}' ({value:.4g}) is outside its mathematically valid range "
                        f"[{low}, {high}] — likely computed against mismatched arrays/columns.",
                    )
                )

    task = model_info.get("task")
    target = model_info.get("target")
    if (
        task in ("forecast", "regression")
        and isinstance(target, str)
        and isinstance(silver_df, pd.DataFrame)
        and target in silver_df.columns
        and pd.api.types.is_numeric_dtype(silver_df[target])
    ):
        history = silver_df[target].dropna()
        if len(history) > 0:
            hist_min, hist_max = float(history.min()), float(history.max())
            span = max(hist_max - hist_min, abs(hist_max), 1.0)
            lower_bound = hist_min - _PREDICTION_RANGE_MULTIPLIER * span
            upper_bound = hist_max + _PREDICTION_RANGE_MULTIPLIER * span

            prediction_cols = [
                c
                for c in predictions_df.select_dtypes(include="number").columns
                if c not in ("actual", "residual")
            ]
            for col in prediction_cols:
                values = predictions_df[col].dropna()
                if values.empty:
                    continue
                out_of_range = values[(values < lower_bound) | (values > upper_bound)]
                if not out_of_range.empty:
                    checks.append(
                        _warning(
                            "prediction_range",
                            f"Column '{col}': {len(out_of_range)} value(s) fall far outside the "
                            f"historical '{target}' range [{hist_min:.4g}, {hist_max:.4g}] "
                            f"(checked against [{lower_bound:.4g}, {upper_bound:.4g}]).",
                        )
                    )
                else:
                    checks.append(
                        _ok(
                            "prediction_range",
                            f"Column '{col}' stays within a reasonable range of the historical "
                            f"'{target}' values.",
                        )
                    )

    return _summarize(checks)
