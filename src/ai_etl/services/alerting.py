"""Drift check + digest delivery orchestration for saved pipelines (Sprint
14, ADR-018).

Called from `services/execution_queue.py::run_full_analysis_task` only when
the run was produced by a scheduled fire (`saved_pipeline_id` is not
`None`). Ties together `audit/db.py` (fetch the previous run's KPIs),
`core/drift.py` (pure comparison), `services/digest.py` (formatting), and
`services/notifications.py` (delivery) — kept out of `core/` because it does
real I/O (DB reads, outbound HTTP), matching this project's `core/` vs
`services/`/`audit/` layering.
"""

from __future__ import annotations

from typing import Any

from ai_etl.audit.db import get_previous_completed_run, load_full_result
from ai_etl.core.analysis_types import AdvisorResult, ScienceResult, TokenUsage
from ai_etl.core.drift import DriftMetric, detect_kpi_drift
from ai_etl.core.llm import get_model_name
from ai_etl.core.pricing import compute_cost_usd
from ai_etl.services.digest import build_digest
from ai_etl.services.notifications import (
    send_email_digest,
    send_google_chat_digest,
    send_slack_digest,
    send_teams_digest,
)

DEFAULT_DRIFT_THRESHOLD_PCT = 20.0


def _extract_science_metrics(science_results: list[Any]) -> dict[str, float]:
    """Every numeric `model_info.metrics` entry across all Science
    sub-tasks, keyed `"science · {task_question} · {metric_name}"` so each
    sub-task's own model is tracked independently (ADR-018 Decision 4).
    Non-numeric metric values (and `bool`, which is a `int` subclass in
    Python but not a meaningful KPI) are skipped, not stringified.
    """
    metrics: dict[str, float] = {}
    for science in science_results:
        question = (science.get("task_question") or "").strip()
        model_info = science.get("model_info") or {}
        for metric_name, value in (model_info.get("metrics") or {}).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            metrics[f"science · {question} · {metric_name}"] = float(value)
    return metrics


def _build_current_kpis(
    rows_loaded: int | None,
    cost_usd: float | None,
    total_tokens: int,
    science_results: list[ScienceResult],
) -> dict[str, float]:
    kpis: dict[str, float] = {}
    if rows_loaded is not None:
        kpis["rows_loaded"] = float(rows_loaded)
    if cost_usd is not None:
        kpis["cost_usd"] = cost_usd
    kpis["total_tokens"] = float(total_tokens)
    kpis.update(_extract_science_metrics(list(science_results)))
    return kpis


def _build_previous_kpis(
    previous_run: dict[str, Any], log_dir: str, tenant_id: str
) -> dict[str, float]:
    kpis: dict[str, float] = {}
    if previous_run.get("rows_loaded") is not None:
        kpis["rows_loaded"] = float(previous_run["rows_loaded"])
    if previous_run.get("cost_usd") is not None:
        kpis["cost_usd"] = float(previous_run["cost_usd"])
    if previous_run.get("total_tokens") is not None:
        kpis["total_tokens"] = float(previous_run["total_tokens"])

    previous_full = load_full_result(previous_run["run_id"], log_dir=log_dir, tenant_id=tenant_id)
    if previous_full is not None:
        kpis.update(_extract_science_metrics(previous_full.get("science", [])))
    return kpis


def check_drift_and_notify(
    pipeline: dict[str, Any],
    run_id: str,
    rows_loaded: int | None,
    tokens: TokenUsage,
    science_results: list[ScienceResult],
    advisor_result: "AdvisorResult | dict[str, Any]",
    business_question: str,
    log_dir: str = "./runs",
) -> dict[str, Any] | None:
    """Compare `run_id` (the fire that just completed) against the previous
    completed run of the same saved pipeline; if any tracked KPI drifted
    past `pipeline["drift_threshold_pct"]`, format and (best-effort) deliver
    a digest.

    Returns `None` when there is no previous completed run to compare
    against (the pipeline's first fire) — not an error. Otherwise returns a
    small summary dict: `{"triggered": bool, "findings": list[DriftMetric],
    "email_sent": bool, "slack_sent": bool, "teams_sent": bool,
    "google_chat_sent": bool}`. All four `*_sent` keys are always `False`
    when `triggered` is `False` (nothing was built to send).

    Callers (`services/execution_queue.py`) are expected to wrap this in a
    try/except — see that module's docstring — since a failure here (a DB
    hiccup, a delivery-provider error) must never fail the pipeline run
    itself.
    """
    previous_run = get_previous_completed_run(pipeline["id"], exclude_run_id=run_id)
    if previous_run is None:
        return None

    model_name = get_model_name()
    cost_usd = compute_cost_usd(model_name, tokens)
    current_kpis = _build_current_kpis(
        rows_loaded, cost_usd, tokens.get("total_tokens", 0), science_results
    )
    previous_kpis = _build_previous_kpis(previous_run, log_dir, pipeline["tenant_id"])

    threshold_pct = float(pipeline.get("drift_threshold_pct") or DEFAULT_DRIFT_THRESHOLD_PCT)
    findings: list[DriftMetric] = detect_kpi_drift(previous_kpis, current_kpis, threshold_pct)
    triggered = [f for f in findings if f["triggered"]]

    if not triggered:
        return {
            "triggered": False,
            "findings": findings,
            "email_sent": False,
            "slack_sent": False,
            "teams_sent": False,
            "google_chat_sent": False,
        }

    digest = build_digest(
        pipeline_name=pipeline["name"],
        business_question=business_question,
        run_id=run_id,
        findings=triggered,
        advisor_result=advisor_result,
    )
    email_sent = send_email_digest(digest["subject"], digest["html"], digest["text"])
    slack_sent = send_slack_digest(digest["slack_blocks"], digest["text"])
    teams_sent = send_teams_digest(digest["subject"], digest["text"])
    google_chat_sent = send_google_chat_digest(digest["text"])

    return {
        "triggered": True,
        "findings": findings,
        "email_sent": email_sent,
        "slack_sent": slack_sent,
        "teams_sent": teams_sent,
        "google_chat_sent": google_chat_sent,
    }
