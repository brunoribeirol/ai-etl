"""Format a drift finding + the Advisor's existing output as a ready-to-send
executive briefing (Sprint 14, ADR-018).

No new agent, no new LLM call — this is pure formatting over what
`agents/advisor.py` (via `services/pipeline_service.run_advisor_analysis`)
already produced for the run. `services/alerting.py` calls `build_digest`
once drift has been detected, then hands the result to
`services/notifications.py` for delivery.
"""

from __future__ import annotations

from typing import Any

from ai_etl.core.analysis_types import AdvisorResult
from ai_etl.core.drift import DriftMetric


def _format_metric_line(metric: DriftMetric) -> str:
    if metric["pct_change"] is None:
        change = "novo valor" if metric["previous"] == 0 else "caiu para zero"
        return f"- {metric['name']}: {metric['previous']:g} → {metric['current']:g} ({change})"
    sign = "+" if metric["pct_change"] >= 0 else ""
    return (
        f"- {metric['name']}: {metric['previous']:g} → {metric['current']:g} "
        f"({sign}{metric['pct_change']:.1f}%, limite {metric['threshold_pct']:g}%)"
    )


def _format_recommendations(advisor_result: "AdvisorResult | dict[str, Any]") -> str:
    recommendations = advisor_result.get("recommendations", [])
    if not recommendations:
        return "Nenhuma recomendação disponível para esta execução."
    lines = []
    for rec in recommendations:
        priority = rec.get("priority", "medium").upper()
        lines.append(f"- [{priority}] {rec.get('action', '')} — {rec.get('expected_impact', '')}")
    return "\n".join(lines)


def build_digest(
    pipeline_name: str,
    business_question: str,
    run_id: str,
    findings: list[DriftMetric],
    advisor_result: "AdvisorResult | dict[str, Any]",
) -> dict[str, Any]:
    """Build the subject/text/HTML/Slack Block Kit content for one digest.

    `findings` should already be filtered to the triggered subset (callers
    build a digest only once `detect_kpi_drift` found something worth
    reporting — `services/alerting.py` owns that decision, not this
    function). Returns a plain dict (not a TypedDict) since its four keys
    are each consumed by an independent, optional delivery channel
    (`services/notifications.py`) — a caller only using one channel still
    gets a fully-formed dict, nothing here requires both to be sent.
    """
    subject = f"[AI-ETL] Alerta de variação — {pipeline_name}"
    summary = advisor_result.get("summary") or "Resumo executivo não disponível."
    metric_lines = "\n".join(_format_metric_line(f) for f in findings)
    recommendation_lines = _format_recommendations(advisor_result)

    text = (
        f"{subject}\n\n"
        f"Pipeline: {pipeline_name}\n"
        f"Pergunta de negócio: {business_question or '(nenhuma)'}\n"
        f"Execução: {run_id}\n\n"
        f"O que mudou:\n{metric_lines}\n\n"
        f"Resumo executivo:\n{summary}\n\n"
        f"Recomendações:\n{recommendation_lines}\n"
    )

    html = (
        f"<h2>{subject}</h2>"
        f"<p><strong>Pipeline:</strong> {pipeline_name}<br>"
        f"<strong>Pergunta de negócio:</strong> {business_question or '(nenhuma)'}<br>"
        f"<strong>Execução:</strong> {run_id}</p>"
        f"<h3>O que mudou</h3><ul>"
        + "".join(f"<li>{_format_metric_line(f)[2:]}</li>" for f in findings)
        + "</ul>"
        f"<h3>Resumo executivo</h3><p>{summary}</p>"
        f"<h3>Recomendações</h3><ul>"
        + "".join(
            f"<li><strong>[{r.get('priority', 'medium').upper()}]</strong> "
            f"{r.get('action', '')} — {r.get('expected_impact', '')}</li>"
            for r in advisor_result.get("recommendations", [])
        )
        + "</ul>"
    )

    slack_blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": subject}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Pipeline:* {pipeline_name}\n*Execução:* {run_id}",
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*O que mudou:*\n{metric_lines}"},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Resumo:*\n{summary}"}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Recomendações:*\n{recommendation_lines}"},
        },
    ]

    return {"subject": subject, "text": text, "html": html, "slack_blocks": slack_blocks}
