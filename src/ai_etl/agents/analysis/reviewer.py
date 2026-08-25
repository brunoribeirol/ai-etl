"""Second-pass LLM review of a Gold/Science result against the original business
question (Sprint 21 follow-up, ADR-037).

Additive to `core/output_validation.py`'s deterministic checks, never a replacement:
`review_gold_result`/`review_science_result` each make one extra `get_llm().invoke()`
call asking whether a successful sub-task's narrative/result actually answers the
question it was supposed to, given a compact preview of the result (never the full
DataFrame — same truncation posture every other agent prompt already uses, ADR-012).

That same call also checks a distinct failure mode the factual-consistency check can't
catch (2026-08-24 LLM/Prompt-Engineer audit, reproduced): a directional/trend/comparison
question ("is revenue trending up or down?") answered with a hedged, non-committal
narrative ("increased in some months, decreased in others") that never states a
direction. A narrative can be fully consistent with the data — nothing it says is
wrong — while still failing to actually answer a question that asked for a direction.
This surfaces as a second, independent `OutputSanityCheckEntry` (`llm_review_hedge`),
appended only when the question is directional AND the narrative hedges — mirroring
`core/output_validation.py`'s "non-applicable check is skipped entirely" convention
for a non-directional question, rather than emitting a false "ok".

Opt-in (`core.llm.is_llm_review_enabled()`), never called unless a caller checks that
flag first — this module itself doesn't gate on it, matching `core/output_validation.py`'s
own "pure function, caller decides when to call it" shape.

Never raises: any failure (LLM error, malformed JSON) is caught and returns `[]` — a
skipped review is different from one that ran and found nothing (see ADR-037
Decision 3), so callers must treat an empty list as "no entries to append", not
"review passed".
"""

from __future__ import annotations

import json
import logging
from typing import Any, NamedTuple

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
preview above?

Separately, check for hedging: if the business question asks for a direction, trend, or \
comparison (e.g. "is X increasing or decreasing", "is X trending up or down", "which is \
higher/better/worse"), does the narrative commit to an actual, clear answer — a stated \
direction or comparison — or does it hedge without ever stating one (e.g. "it varied", \
"increased in some periods and decreased in others", "results were mixed")? A narrative can \
be factually consistent with the data and still be a non-answer to a directional question if \
it never commits to a direction. If the question is NOT directional/comparative, answer false \
for both `directional_question` and `hedges_direction`.

Respond with ONLY a JSON object, no other text:
{{"consistent": true or false, "issue": "<short explanation if not consistent, else null>", \
"directional_question": true or false, "hedges_direction": true or false, \
"hedge_detail": "<short explanation of the hedge if hedges_direction is true, else null>"}}
"""


class _ParsedReview(NamedTuple):
    consistent: bool
    issue: str | None
    directional_question: bool
    hedges_direction: bool
    hedge_detail: str | None


def _parse_review_response(raw: str) -> _ParsedReview:
    """Parse the LLM's JSON response. Raises on anything malformed — callers catch it,
    same "never raises past this module's own boundary" posture as the functions below.
    """
    parsed: Any = json.loads(strip_code_fences(raw.strip()))
    if not isinstance(parsed, dict) or "consistent" not in parsed:
        raise ValueError("review response missing 'consistent' key")
    consistent = bool(parsed["consistent"])
    issue = parsed.get("issue")
    directional_question = bool(parsed.get("directional_question", False))
    hedges_direction = bool(parsed.get("hedges_direction", False))
    hedge_detail = parsed.get("hedge_detail")
    return _ParsedReview(
        consistent=consistent,
        issue=(str(issue) if issue else None),
        directional_question=directional_question,
        hedges_direction=hedges_direction,
        hedge_detail=(str(hedge_detail) if hedge_detail else None),
    )


def _run_review(
    question: str,
    narrative: str,
    preview: str,
    llm_provider_override: str | None,
    llm_model_override: str | None,
) -> tuple[list[OutputSanityCheckEntry], TokenUsage]:
    zero_tokens: TokenUsage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    try:
        llm = get_llm(provider=llm_provider_override, model=llm_model_override)
        prompt = _REVIEW_PROMPT_TEMPLATE.format(
            question=question, narrative=narrative, preview=preview
        )
        response = llm.invoke(prompt)
        tokens = extract_token_usage(response)
        parsed = _parse_review_response(str(response.content))
    except Exception:  # noqa: BLE001 — never let a review failure break the sub-task itself
        logger.warning("llm_review: review call failed, skipping this entry", exc_info=True)
        return [], zero_tokens

    entries: list[OutputSanityCheckEntry] = []
    if parsed.consistent:
        entries.append(
            {
                "check": "llm_review",
                "severity": "ok",
                "detail": "LLM review found the narrative consistent with the question and data.",
            }
        )
    else:
        entries.append(
            {
                "check": "llm_review",
                "severity": "warning",
                "detail": parsed.issue
                or "LLM review flagged the narrative as inconsistent with the question and/or "
                "data.",
            }
        )

    # Distinct from the check above: a hedge only matters for a directional question, and
    # is only worth surfacing when it actually happened — a non-directional question, or a
    # directional one that got a committed answer, has nothing to flag here (see module
    # docstring's "skipped entirely rather than a false ok" convention).
    if parsed.directional_question and parsed.hedges_direction:
        entries.append(
            {
                "check": "llm_review_hedge",
                "severity": "warning",
                "detail": parsed.hedge_detail
                or "LLM review found the narrative hedges instead of committing to a direction "
                "or comparison the business question asked for.",
            }
        )

    return entries, tokens


def review_gold_result(
    business_question: str,
    narrative: str,
    gold_df: pd.DataFrame,
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
) -> tuple[list[OutputSanityCheckEntry], TokenUsage]:
    """Review one Gold sub-task's narrative/`gold_df` against `business_question`.

    Returns `([], zero_tokens)` if the review call itself fails — see module
    docstring. Otherwise returns one or two `OutputSanityCheckEntry` items to append
    via `core.output_validation.append_check` (one per entry) — the factual-
    consistency check (`llm_review`), plus a second `llm_review_hedge` entry only
    when `business_question` is directional/comparative and the narrative hedges
    instead of committing to an answer — along with the token usage the review call
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
) -> tuple[list[OutputSanityCheckEntry], TokenUsage]:
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
