"""Orchestrator Agent — parses NL spec into a structured pipeline plan."""

import json
import uuid

from pydantic import ValidationError

from ai_etl.agents._llm_codegen import strip_code_fences
from ai_etl.audit.logger import log_action
from ai_etl.core.llm import get_llm
from ai_etl.core.pipeline_plan_schema import PipelinePlan
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
    - type: "csv" | "postgres" | "s3_parquet"
    - For csv: path (output file path)
    - For postgres: table (schema.table)
    - For s3_parquet: bucket (S3 bucket name), key (S3 object key, e.g. "warehouse/sales/2026.parquet")
- transformations: list of transformation descriptions in plain English
- quality_checks: list of quality checks to apply (infer from spec, default to: null_check, duplicate_check)

Available source types: csv, postgres, sqlite, mysql, mongodb, rest, document
Available destination types: csv, postgres, s3_parquet

Respond ONLY with valid JSON. No explanation, no markdown code fences.
"""


def orchestrator_node(state: PipelineState) -> PipelineState:
    """Parse the NL spec into a structured pipeline_plan.

    Retries up to 2 times on invalid JSON.
    Sets state["error"] if parsing fails after all retries.
    """
    provider_override = state.get("llm_provider_override")
    model_override = state.get("llm_model_override")
    llm = get_llm(provider=provider_override, model=model_override)
    spec = state["spec"]
    run_id = state.get("run_id") or str(uuid.uuid4())

    if provider_override or model_override:
        # Sprint 30/gap-closing (ADR-031 §5) — visibility into which provider/model
        # actually ran this execution, not just the saved pipeline's configured
        # intent. Logged once per node that calls get_llm() with an override.
        state = {
            **state,
            "audit_log": log_action(
                state,
                "orchestrator",
                "llm_override_used",
                {"provider": provider_override, "model": model_override},
            ),
        }

    prompt = ORCHESTRATOR_PROMPT.format(spec=spec)
    last_error: str | None = None

    for attempt in range(1, 3):
        response = llm.invoke(prompt)
        # Real bug found 2026-08-23 running a live model comparison: unlike every
        # other agent that parses an LLM response (Transformer/Analyst/Science/
        # Advisor, via this same strip_code_fences), the Orchestrator used to call
        # json.loads() directly with no fence-stripping — already flagged as a real
        # gap in the 2026-08-22 audit ("costs 1 retry whenever the model ignores 'no
        # markdown fences'"), confirmed here as more than cosmetic: claude-haiku-4-5
        # consistently wraps its JSON in ```json fences on every attempt, so the old
        # code failed both retries and the run every time (100% failure rate).
        content = strip_code_fences(str(response.content).strip())

        try:
            pipeline_plan = json.loads(content)
            # Wave 0 (2026-08-24 audit, Red Team CRITICAL finding) — structural gate
            # before any downstream node reads pipeline_plan. An unexpected `type`
            # value or a missing `name`/`type` fails here and retries with feedback,
            # same as malformed JSON. Doesn't validate `query` content — that defense
            # lives at the connector level (sources/sqlite_source.py,
            # sources/mysql_source.py), so it runs regardless of caller. Validated
            # against `PipelinePlan`, not stored — extractor_node keeps reading the
            # original dict, plan shape downstream is unchanged.
            PipelinePlan.model_validate(pipeline_plan)
            new_log = log_action(
                state,
                "orchestrator",
                "plan_created",
                {"attempt": attempt, "sources": len(pipeline_plan.get("sources", []))},
            )
            return {**state, "run_id": run_id, "pipeline_plan": pipeline_plan, "audit_log": new_log}
        except (json.JSONDecodeError, ValidationError) as e:
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
