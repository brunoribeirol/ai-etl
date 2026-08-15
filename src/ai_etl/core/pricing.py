"""Per-model token pricing and cost calculation (Sprint 3 — evaluation metric 3,
"cost per execution"). See Vault: `artefact/evaluation-metrics.md`.

Prices are USD per 1,000,000 tokens, input/output priced separately (OpenAI's
own pricing unit) — checked against OpenAI's published pricing as of this
sprint. `core/llm.py` reads a single model name from `AI_ETL_LLM_MODEL` and
uses it for every agent call within a run, so one model name is enough to
price a whole run's `TokenUsage` — there's no per-call model mixing to track.
"""

from ai_etl.core.analysis_types import TokenUsage

# USD per 1M tokens. Update alongside AI_ETL_LLM_MODEL's supported values in
# core/llm.py if a new model is adopted — an unpriced model isn't an error
# (see compute_cost_usd below), but its cost silently reads as $0.00, which is
# worse than an obviously-wrong estimate, so keep this in sync.
MODEL_PRICING_USD_PER_MILLION_TOKENS: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
}


def compute_cost_usd(model_name: str, tokens: TokenUsage) -> float | None:
    """Return the USD cost of `tokens` for `model_name`, or `None` if the model
    isn't in `MODEL_PRICING_USD_PER_MILLION_TOKENS`.

    `None` (not `0.0`) on an unpriced model is deliberate: `0.0` would be
    indistinguishable from "this model is free" or "this run used zero
    tokens" when displayed later — `None` lets callers show "cost unknown"
    instead of a misleadingly precise-looking zero.
    """
    pricing = MODEL_PRICING_USD_PER_MILLION_TOKENS.get(model_name)
    if pricing is None:
        return None

    input_cost = tokens.get("input_tokens", 0) / 1_000_000 * pricing["input"]
    output_cost = tokens.get("output_tokens", 0) / 1_000_000 * pricing["output"]
    return round(input_cost + output_cost, 6)
