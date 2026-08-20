"""Orchestrator Agent — parses NL spec into a structured pipeline plan."""

import json
import uuid

from ai_etl.audit.logger import log_action
from ai_etl.core.llm import get_llm
from ai_etl.core.state import PipelineState

ORCHESTRATOR_PROMPT = """You are a data pipeline planner.

The user provided this pipeline specification:
"{spec}"

Extract a structured pipeline plan as JSON with these fields:
- sources: list of data sources, each with:
    - name (string identifier)
    - type: "csv" | "postgres" | "sqlite" | "mysql" | "mongodb" | "rest" | "document"
    - For csv: path (file path)
    - For postgres: table (schema.table)
    - For sqlite: path (SQLite .db/.sqlite file path), table (table name, no schema prefix)
    - For mysql: table (database.table) — MySQL and MariaDB both use this type
    - For mongodb: database (database name), collection (collection name), query (optional \
MongoDB filter dict), limit (optional max documents to fetch)
    - For rest: url (endpoint URL), params (optional query params dict), auth (optional; \
only include if the spec names an environment variable or token endpoint holding a \
credential — never invent a literal secret value): \
{{"type": "api_key", "header": "X-API-Key", "env_var": "ENV_VAR_NAME"}} or \
{{"type": "bearer", "env_var": "ENV_VAR_NAME"}} or \
{{"type": "basic", "username_env_var": "ENV_VAR_NAME", "password_env_var": "ENV_VAR_NAME"}} or \
{{"type": "oauth2_client_credentials", "token_url": "https://...", \
"client_id_env_var": "ENV_VAR_NAME", "client_secret_env_var": "ENV_VAR_NAME", \
"scope": "optional scope string"}}
    - For document: path (PDF or DOCX file path)
- destination: target output, with:
    - type: "csv" | "postgres"
    - For csv: path (output file path)
    - For postgres: table (schema.table)
- transformations: list of transformation descriptions in plain English
- quality_checks: list of quality checks to apply (infer from spec, default to: null_check, duplicate_check)

Available source types: csv, postgres, sqlite, mysql, mongodb, rest, document
Available destination types: csv, postgres

Respond ONLY with valid JSON. No explanation, no markdown code fences.
"""


def orchestrator_node(state: PipelineState) -> PipelineState:
    """Parse the NL spec into a structured pipeline_plan.

    Retries up to 2 times on invalid JSON.
    Sets state["error"] if parsing fails after all retries.
    """
    llm = get_llm()
    spec = state["spec"]
    run_id = state.get("run_id") or str(uuid.uuid4())

    prompt = ORCHESTRATOR_PROMPT.format(spec=spec)
    last_error: str | None = None

    for attempt in range(1, 3):
        response = llm.invoke(prompt)
        content = str(response.content).strip()

        try:
            pipeline_plan = json.loads(content)
            new_log = log_action(
                state,
                "orchestrator",
                "plan_created",
                {"attempt": attempt, "sources": len(pipeline_plan.get("sources", []))},
            )
            return {**state, "run_id": run_id, "pipeline_plan": pipeline_plan, "audit_log": new_log}
        except json.JSONDecodeError as e:
            last_error = str(e)
            prompt += f"\n\nPrevious response was not valid JSON: {e}\nResponse was:\n{content}\n\nReturn ONLY valid JSON."

    new_log = log_action(state, "orchestrator", "plan_failed", {"error": last_error})
    return {
        **state,
        "run_id": run_id,
        "error": f"Orchestrator failed to produce valid JSON: {last_error}",
        "status": "failed",
        "audit_log": new_log,
    }
