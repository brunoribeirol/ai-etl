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
    - type: "csv" | "postgres" | "rest"
    - For csv: path (file path)
    - For postgres: table (schema.table)
    - For rest: url (endpoint URL), params (optional query params dict)
- destination: target output, with:
    - type: "csv" | "postgres"
    - For csv: path (output file path)
    - For postgres: table (schema.table)
- transformations: list of transformation descriptions in plain English
- quality_checks: list of quality checks to apply (infer from spec, default to: null_check, duplicate_check)

Available source types: csv, postgres, rest
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
