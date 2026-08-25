"""Planner — decomposes a business question into independently-answerable analysis tasks.

Rationale: a single Gold/Science call answering a multi-part business question in one
shot tends to cover only part of it, since the LLM must prioritize within one code
generation. Splitting the question into explicit sub-tasks and running each through
Gold or Science independently makes coverage a property of the loop, not of how well
one LLM call juggled everything at once.
"""

import json
import logging
from typing import Any, Literal

import pandas as pd

from ai_etl.agents._llm_codegen import strip_code_fences
from ai_etl.core.analysis_types import AnalysisTask, TokenUsage
from ai_etl.core.llm import extract_token_usage, get_llm
from ai_etl.core.locale import DEFAULT_LOCALE, narrative_language_instruction, resolve_locale

logger = logging.getLogger(__name__)

_ZERO_TOKENS: TokenUsage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

MAX_TASKS = 5
_VALID_TYPES = ("descriptive", "diagnostic_or_predictive")

_PROMPT_TEMPLATE = """\
You are a data analysis project manager. A user asked a business question about a
dataset. Break the question into independent, concrete analysis sub-tasks — each one
answerable on its own with a single descriptive or predictive/diagnostic analysis.

## Dataset columns
{columns_list}

## Business question
"{question}"

## Your task

Produce a JSON array of 1 to {max_tasks} sub-tasks. Each sub-task is an object with:
- "question": a specific, self-contained question (a narrow slice of the original
  question, not a restatement of the whole thing). {language_instruction}
- "type": "descriptive" for KPIs, aggregations, rankings, and comparisons on the
  existing data; "diagnostic_or_predictive" for explaining why something happened, or
  forecasting/classifying/clustering/regression

Rules:
- If the question is already simple and single-purpose, return just ONE sub-task.
- Do not invent sub-tasks the question didn't ask for.
- Cap at {max_tasks} sub-tasks — merge closely related asks into one when needed.
- If the question has a prescriptive/recommendation-seeking clause — "...and
  recommend...", "...what should we do...", "...suggest an action...", "...how can we
  improve...", or similar imperative phrasing asking what to DO about the data — DROP
  that clause entirely. Do not turn it into a sub-task of any type. A separate Advisor
  step already runs after every sub-task here finishes and owns that recommendation;
  duplicating it into a "descriptive" sub-task produces a shallow, generic answer that
  competes with the Advisor's better-contextualized one. Only decompose the
  descriptive/diagnostic part of the question.
- Respond ONLY with a valid JSON array. No markdown fences, no explanation.

Example output (Portuguese shown, but write in the language of the instruction above):
[
  {{"question": "Quais são os principais KPIs gerais do catálogo?", "type": "descriptive"}},
  {{"question": "Por que a avaliação média caiu em 2020?", "type": "diagnostic_or_predictive"}}
]

Example with a prescriptive clause to drop — question: "Quais foram os produtos mais
vendidos no último trimestre, e recomende uma ação para aumentar a receita?" — the
"recomende uma ação..." part is NOT a sub-task; only the descriptive part is planned:
[
  {{"question": "Quais foram os produtos mais vendidos no último trimestre?", "type": "descriptive"}}
]
"""


def plan_analysis_tasks(
    business_question: str,
    df: pd.DataFrame,
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
    locale: str = DEFAULT_LOCALE,
) -> tuple[list[AnalysisTask], TokenUsage]:
    """Decompose a business question into up to MAX_TASKS independent sub-tasks.

    Falls back to a single descriptive task wrapping the original question if the LLM
    output can't be parsed — a decomposition failure should degrade to the old one-shot
    behavior, not block the pipeline.

    Args:
        llm_provider_override / llm_model_override: Sprint 30/gap-closing (ADR-031
            §5) — the saved pipeline's LLM override, threaded in by
            `services/pipeline_service.py::run_analysis_tasks` (resolved once in
            `run_full_analysis` from `PipelineState`, since this agent runs outside
            the LangGraph/PipelineState — see that module's docstring). `None` (the
            default) for every existing caller — unchanged behavior.
        locale: Sprint 25 (ADR-036) — the tenant's configured locale, same threading
            pattern as `llm_provider_override` above. Defaults to `DEFAULT_LOCALE`
            ("pt-BR") — unchanged behavior for every existing caller.
    """
    prompt = _PROMPT_TEMPLATE.format(
        columns_list=str(df.columns.tolist()),
        question=business_question,
        max_tasks=MAX_TASKS,
        language_instruction=narrative_language_instruction(resolve_locale(locale)),
    )
    llm = get_llm(provider=llm_provider_override, model=llm_model_override)

    try:
        response = llm.invoke(prompt)
        tokens = extract_token_usage(response)
        raw = strip_code_fences(str(response.content).strip())
        parsed: Any = json.loads(raw)
        if not isinstance(parsed, list) or not parsed:
            raise ValueError("empty or non-list response")

        cleaned: list[AnalysisTask] = []
        for item in parsed[:MAX_TASKS]:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question", "")).strip()
            raw_type = item.get("type")
            task_type: Literal["descriptive", "diagnostic_or_predictive"] = (
                raw_type if raw_type in _VALID_TYPES else "descriptive"
            )
            if question:
                cleaned.append({"question": question, "type": task_type})

        if not cleaned:
            raise ValueError("no valid tasks parsed")
        return cleaned, tokens

    except Exception:  # noqa: BLE001 — any planning failure degrades to one-shot mode
        # No PipelineState exists at this layer (Planner runs outside the LangGraph
        # graph, see module docstring) — `log_action()` doesn't apply here, same
        # reasoning as `execution_queue.py`'s own `logging.warning()` at enqueue time.
        # Without this, a decomposition failure was previously silent: the caller
        # (and an external tester reporting "only got one sub-answer") had no signal
        # to distinguish "the question really was simple" from "the LLM call failed".
        logger.warning(
            "plan_analysis_tasks: decomposition failed, falling back to one-shot mode",
            exc_info=True,
        )
        return [{"question": business_question, "type": "descriptive"}], _ZERO_TOKENS
