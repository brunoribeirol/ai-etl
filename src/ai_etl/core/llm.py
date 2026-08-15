"""LLM wrapper — OpenAI client with retry logic and model configuration."""

import os
from typing import Any

from langchain_openai import ChatOpenAI

from ai_etl.core.analysis_types import TokenUsage


def get_model_name() -> str:
    """Return the configured model name (AI_ETL_LLM_MODEL env var, default:
    gpt-4o-mini), without constructing a client.

    Extracted out of get_llm() so callers that only need the model name for
    logging/cost-tracking purposes (audit/db.py's cost-per-run persistence,
    Sprint 3/ADR-008) don't have to instantiate a ChatOpenAI client just to
    read it back off `.model_name`.
    """
    return os.getenv("AI_ETL_LLM_MODEL", "gpt-4o-mini")


def get_llm(temperature: float = 0.0) -> ChatOpenAI:
    """Return a configured ChatOpenAI instance.

    Model is read from AI_ETL_LLM_MODEL env var (default: gpt-4o-mini).
    Use gpt-4o-mini for development; gpt-4o for the final case study.
    """
    return ChatOpenAI(model=get_model_name(), temperature=temperature)


def extract_token_usage(response: Any) -> TokenUsage:
    """Read token usage off an LLM response, defaulting to zeros if unavailable.

    `usage_metadata` isn't populated by every provider/mock, so this must never raise —
    cost tracking degrading to zero is preferable to it crashing an analysis.
    """
    usage = getattr(response, "usage_metadata", None) or {}
    return {
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    }


def sum_token_usage(*usages: TokenUsage) -> TokenUsage:
    """Add up multiple TokenUsage dicts (e.g. one per retry attempt)."""
    return {
        "input_tokens": sum(u.get("input_tokens", 0) for u in usages),
        "output_tokens": sum(u.get("output_tokens", 0) for u in usages),
        "total_tokens": sum(u.get("total_tokens", 0) for u in usages),
    }
