"""KPI drift comparison for saved (recurring) pipelines (Sprint 14, ADR-018).

Pure functions, no I/O — mirrors `core/scheduling.py`'s split between
"execution-scheduling infrastructure" (here: comparison math) and the
services layer that fetches/persists/delivers around it
(`audit/db.py::get_previous_completed_run`, `services/alerting.py`).

A KPI is any named numeric value both runs happen to have (`rows_loaded`,
`cost_usd`, `total_tokens`, or a Science sub-task's own numeric metric — see
`services/alerting.py::_extract_science_metrics`). Comparison is always
"current vs. the immediately previous completed run of the same saved
pipeline" (ADR-018 Decision 1) — no trend, no history, just those two points.
"""

from __future__ import annotations

from typing import TypedDict


class DriftMetric(TypedDict):
    """One KPI's comparison between the previous and current run.

    `pct_change` is `None` exactly when `previous == 0` (a percentage change
    from zero is undefined) — see `compute_pct_change`'s docstring for how
    `triggered` is still decided in that case.
    """

    name: str
    previous: float
    current: float
    pct_change: float | None
    threshold_pct: float
    triggered: bool


def compute_pct_change(previous: float, current: float) -> float | None:
    """Return the signed percentage change from `previous` to `current`,
    or `None` if `previous` is zero (division by zero has no meaningful
    percentage — see `detect_kpi_drift` for how that case is still flagged).
    """
    if previous == 0:
        return None
    return (current - previous) / abs(previous) * 100


def detect_kpi_drift(
    previous: dict[str, float],
    current: dict[str, float],
    threshold_pct: float,
) -> list[DriftMetric]:
    """Compare every KPI present in `current` against `previous`.

    A KPI only present in one of the two dicts (e.g. a Science sub-task's
    question changed wording between fires, so its metric key doesn't match
    the prior run's) is skipped — there is no meaningful "previous value" to
    compare against, and treating "no prior value" as infinite drift would
    false-positive on every pipeline edit, not just a real KPI change
    (ADR-018 Decision 4).

    When `previous[name] == 0`, `pct_change` is `None` and `triggered` is
    `True` whenever `current[name] != 0` (going from zero to non-zero, or the
    reverse, is inherently notable and can't be expressed as a percentage) —
    `False` when both are zero (genuinely unchanged).

    Returns one `DriftMetric` per KPI compared, in `current`'s iteration
    order — callers filter for `triggered` themselves (see
    `services/alerting.py`) rather than this function only returning the
    triggered subset, so a caller that wants the full comparison (e.g. for
    a debug/audit view) still can.
    """
    findings: list[DriftMetric] = []
    for name, current_value in current.items():
        if name not in previous:
            continue
        previous_value = previous[name]
        pct_change = compute_pct_change(previous_value, current_value)
        if pct_change is None:
            triggered = current_value != previous_value
        else:
            triggered = abs(pct_change) >= threshold_pct
        findings.append(
            {
                "name": name,
                "previous": previous_value,
                "current": current_value,
                "pct_change": pct_change,
                "threshold_pct": threshold_pct,
                "triggered": triggered,
            }
        )
    return findings
