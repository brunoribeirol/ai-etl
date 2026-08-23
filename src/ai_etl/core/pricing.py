"""Per-model token pricing and cost calculation (Sprint 3 — evaluation metric 3,
"cost per execution"). See Vault: `artefact/evaluation-metrics.md`.

Prices are USD per 1,000,000 tokens, input/output priced separately (OpenAI's
own pricing unit, reused here for every provider for a single consistent
cost-calc code path) — checked against each provider's own published pricing
as of the sprint that added it. `core/llm.py` reads a single model name (from
`AI_ETL_LLM_MODEL`, or a per-saved_pipeline override — Sprint 30, ADR-031) and
uses it for every agent call within a run, so one model name is enough to
price a whole run's `TokenUsage` — there's no per-call model mixing to track.

Sprint 30 (ADR-031) added Anthropic/Google pricing and explicit $0.00 Ollama
entries — before this, `compute_cost_usd` silently returned `None` (displayed
as "cost unknown", see its own docstring) for every non-OpenAI model, which
in practice meant the Sprint 29 budget cap never saw a real number for any
tenant running Anthropic/Google, undetected until the 2026-08-22 audit.
"""

from ai_etl.core.analysis_types import TokenUsage

# USD per 1M tokens. Update alongside core/llm.py's ALLOWED_MODELS_BY_PROVIDER if a
# new model is adopted — an unpriced model isn't an error (see compute_cost_usd
# below), but its cost silently reads as "unknown" (`None`), worse than an
# obviously-wrong estimate, so keep this in sync.
MODEL_PRICING_USD_PER_MILLION_TOKENS: dict[str, dict[str, float]] = {
    # OpenAI (Sprint 3).
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    # Anthropic (Sprint 30, ADR-031) — matches core/llm.py's
    # _DEFAULT_MODEL_BY_PROVIDER["anthropic"] and ALLOWED_MODELS_BY_PROVIDER.
    "claude-opus-5": {"input": 15.00, "output": 75.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    # Google (Sprint 30, ADR-031).
    "gemini-2.0-pro": {"input": 1.25, "output": 5.00},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    # Ollama (Sprint 30, ADR-031) — local inference, genuinely free: priced at
    # exactly $0.00/$0.00 rather than left unpriced, so `compute_cost_usd` returns
    # `0.0` (a real, correct cost) instead of `None` ("cost unknown") for these
    # models. See ALLOWED_MODELS_BY_PROVIDER["ollama"] in core/llm.py for the
    # full allowed set — every Ollama model shares this $0 entry via
    # `_OLLAMA_MODELS` below rather than one dict line per model name.
}

# Every Ollama model this deployment allows (core/llm.py's
# ALLOWED_MODELS_BY_PROVIDER["ollama"]) is local inference with no per-token
# billing — looped into MODEL_PRICING_USD_PER_MILLION_TOKENS at import time so
# compute_cost_usd finds an explicit $0 entry (not a silent `None`) for each one,
# without hand-duplicating the same {"input": 0.0, "output": 0.0} literal per model.
_OLLAMA_MODELS = ("llama3.1", "llama3.3", "mistral", "qwen2.5")
for _ollama_model in _OLLAMA_MODELS:
    MODEL_PRICING_USD_PER_MILLION_TOKENS[_ollama_model] = {"input": 0.0, "output": 0.0}
del _ollama_model


def compute_cost_usd(model_name: str, tokens: TokenUsage) -> float | None:
    """Return the USD cost of `tokens` for `model_name`, or `None` if the model
    isn't in `MODEL_PRICING_USD_PER_MILLION_TOKENS`.

    `None` (not `0.0`) on an unpriced model is deliberate: `0.0` would be
    indistinguishable from "this model is free" or "this run used zero
    tokens" when displayed later — `None` lets callers show "cost unknown"
    instead of a misleadingly precise-looking zero. Every Ollama model this
    deployment allows genuinely does return `0.0` here (see `_OLLAMA_MODELS`
    above) — that's the one case where `0.0` is the correct, non-misleading
    answer rather than an omission.
    """
    pricing = MODEL_PRICING_USD_PER_MILLION_TOKENS.get(model_name)
    if pricing is None:
        return None

    input_cost = tokens.get("input_tokens", 0) / 1_000_000 * pricing["input"]
    output_cost = tokens.get("output_tokens", 0) / 1_000_000 * pricing["output"]
    return round(input_cost + output_cost, 6)
