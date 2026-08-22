"""Operator-facing failure alerting for scheduled pipelines (Sprint 15,
ADR-020).

Distinct from `services/digest.py`/`services/alerting.py` (Sprint 14,
ADR-018): that pair is about pipeline *content* — a KPI moved between two
successful runs of the same saved pipeline. This module is about pipeline
*health* — a scheduled pipeline stopped completing at all. Different
audience (whoever configured the pipeline, not an executive consumer),
different tone (operational, not a business narrative), same delivery
mechanism underneath: `services/notifications.py`'s four generic send
functions are reused as-is, no new delivery code.
"""

from __future__ import annotations

from typing import Any

from ai_etl.services.notifications import (
    send_email_digest,
    send_google_chat_digest,
    send_slack_digest,
    send_teams_digest,
)


def build_health_alert(pipeline: dict[str, Any]) -> dict[str, Any]:
    """Format an operator-facing alert for a saved pipeline that has just
    crossed the consecutive-failure threshold
    (`execution_queue.py::HEALTH_ALERT_FAILURE_THRESHOLD`).

    `pipeline` is the dict shape `audit.db.get_saved_pipeline`/
    `list_saved_pipelines` already return — this function reads
    `name`/`id`/`consecutive_failures`/`last_error` off it, nothing new.
    """
    name = pipeline["name"]
    pipeline_id = pipeline["id"]
    failures = pipeline["consecutive_failures"]
    last_error = pipeline.get("last_error") or "(nenhum detalhe de erro registrado)"

    subject = f'[AI-ETL] Pipeline "{name}" falhou {failures} execuções seguidas'
    text = (
        f'O pipeline agendado "{name}" (id {pipeline_id}) falhou {failures} execuções '
        "consecutivas, mesmo após as tentativas automáticas de retry.\n\n"
        f"Último erro registrado:\n{last_error}\n\n"
        "Verifique a fonte de dados e a configuração deste pipeline — ele não vai voltar "
        "a rodar sozinho até que a causa seja corrigida."
    )
    html = (
        f"<p>O pipeline agendado <strong>{name}</strong> (id {pipeline_id}) falhou "
        f"<strong>{failures}</strong> execuções consecutivas, mesmo após as tentativas "
        "automáticas de retry.</p>"
        f"<p><strong>Último erro registrado:</strong></p><pre>{last_error}</pre>"
        "<p>Verifique a fonte de dados e a configuração deste pipeline — ele não vai "
        "voltar a rodar sozinho até que a causa seja corrigida.</p>"
    )
    slack_blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{subject}*\n\nÚltimo erro:\n```{last_error}```",
            },
        }
    ]
    return {"subject": subject, "text": text, "html": html, "slack_blocks": slack_blocks}


def check_and_alert_pipeline_health(pipeline: dict[str, Any]) -> dict[str, bool]:
    """Deliver a health alert for `pipeline` via every configured channel.

    Same "never raises, returns `False` per channel if unconfigured or the
    request fails" contract as every `notifications.py` function — this
    function doesn't add its own try/except because none of the four calls
    below can raise on their own. Callers (`execution_queue.py`) are
    expected to call this at most once per threshold crossing (an
    `== threshold`, not `>= threshold`, comparison at the call site) so a
    pipeline that stays broken doesn't re-alert on every subsequent
    failure.
    """
    alert = build_health_alert(pipeline)
    return {
        "email_sent": send_email_digest(alert["subject"], alert["html"], alert["text"]),
        "slack_sent": send_slack_digest(alert["slack_blocks"], alert["text"]),
        "teams_sent": send_teams_digest(alert["subject"], alert["text"]),
        "google_chat_sent": send_google_chat_digest(alert["text"]),
    }
