"""Second-pass LLM review of a Gold/Science result against the original business
question (Sprint 21 follow-up, ADR-037).

Additive to `core/output_validation.py`'s deterministic checks, never a replacement:
`review_gold_result`/`review_science_result` each make one extra `get_llm().invoke()`
call asking whether a successful sub-task's narrative/result actually answers the
question it was supposed to, given a compact preview of the result (never the full
DataFrame — same truncation posture every other agent prompt already uses, ADR-012).

Opt-in (`core.llm.is_llm_review_enabled()`), never called unless a caller checks that
flag first — this module itself doesn't gate on it, matching `core/output_validation.py`'s
own "pure function, caller decides when to call it" shape.

Never raises: any failure (LLM error, malformed JSON) is caught and returns `None` —
a skipped review is different from one that ran and found nothing (see ADR-037
Decision 3), so callers must treat `None` as "no entry to append", not "review passed".
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd

from ai_etl.agents._llm_codegen import strip_code_fences
from ai_etl.core.analysis_types import OutputSanityCheckEntry, TokenUsage
from ai_etl.core.llm import extract_token_usage, get_llm

logger = logging.getLogger(__name__)

_REVIEW_PROMPT_TEMPLATE = """You are reviewing whether an automated data-analysis result \
actually answers the business question it was generated for.

Business question: {question}

Narrative the analysis produced:
{narrative}

Compact preview of the result data (not the full dataset):
{preview}

Does the narrative genuinely answer the business question, using data consistent with the \
preview above? Respond with ONLY a JSON object, no other text:
{{"consistent": true or false, "issue": "<short explanation if not consistent, else null>"}}
"""


def _parse_review_response(raw: str) -> tuple[bool, str | None]:
    """Parse the LLM's JSON response. Raises on anything malformed — callers catch it,
    same "never raises past this module's own boundary" posture as the functions below.
    """
    parsed: Any = json.loads(strip_code_fences(raw.strip()))
    if not isinstance(parsed, dict) or "consistent" not in parsed:
        raise ValueError("review response missing 'consistent' key")
    consistent = bool(parsed["consistent"])
    issue = parsed.get("issue")
    return consistent, (str(issue) if issue else None)


def _run_review(
    question: str,
    narrative: str,
    preview: str,
    llm_provider_override: str | None,
    llm_model_override: str | None,
) -> tuple[OutputSanityCheckEntry | None, TokenUsage]:
    zero_tokens: TokenUsage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    try:
        llm = get_llm(provider=llm_provider_override, model=llm_model_override)
        prompt = _REVIEW_PROMPT_TEMPLATE.format(
            question=question, narrative=narrative, preview=preview
        )
        response = llm.invoke(prompt)
        tokens = extract_token_usage(response)
        consistent, issue = _parse_review_response(str(response.content))
    except Exception:  # noqa: BLE001 — never let a review failure break the sub-task itself
        logger.warning("llm_review: review call failed, skipping this entry", exc_info=True)
        return None, zero_tokens

    if consistent:
        return (
            {
                "check": "llm_review",
                "severity": "ok",
                "detail": "LLM review found the narrative consistent with the question and data.",
            },
            tokens,
        )
    return (
        {
            "check": "llm_review",
            "severity": "warning",
            "detail": issue
            or "LLM review flagged the narrative as inconsistent with the question and/or data.",
        },
        tokens,
    )


def review_gold_result(
    business_question: str,
    narrative: str,
    gold_df: pd.DataFrame,
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
) -> tuple[OutputSanityCheckEntry | None, TokenUsage]:
    """Review one Gold sub-task's narrative/`gold_df` against `business_question`.

    Returns `(None, zero_tokens)` if the review call itself fails — see module
    docstring. Otherwise returns one `OutputSanityCheckEntry` to append via
    `core.output_validation.append_check`, plus the token usage the review call
    consumed (fold into the sub-task's own `tokens` for accurate cost tracking).
    """
    preview = (
        f"shape={gold_df.shape}\n{gold_df.head(5).to_string()}"
        if not gold_df.empty
        else "gold_df is empty."
    )
    return _run_review(
        business_question, narrative, preview, llm_provider_override, llm_model_override
    )


def review_science_result(
    business_question: str,
    narrative: str,
    predictions_df: pd.DataFrame,
    model_info: dict[str, Any],
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
) -> tuple[OutputSanityCheckEntry | None, TokenUsage]:
    """Review one Science sub-task's narrative/`predictions_df`/`model_info` against
    `business_question`. Same contract as `review_gold_result`.
    """
    preview = (
        f"model_info={json.dumps(model_info, default=str)}\n"
        f"predictions shape={predictions_df.shape}\n{predictions_df.head(5).to_string()}"
        if not predictions_df.empty
        else f"model_info={json.dumps(model_info, default=str)}\npredictions_df is empty."
    )
    return _run_review(
        business_question, narrative, preview, llm_provider_override, llm_model_override
    )
