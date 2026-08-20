"""LLM wrapper — multi-provider client factory with retry logic and model config.

Supports OpenAI, Anthropic, Google (Gemini), and local models via Ollama, selected
via AI_ETL_LLM_PROVIDER (default: openai, preserving existing behavior). See
docs/adr/ADR-014-multi-provider-llm.md for the full rationale, the env var contract,
and what's explicitly out of scope (no automatic cross-provider fallback).
"""

import os
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from ai_etl.core.analysis_types import TokenUsage

# Default model per provider, applied when AI_ETL_LLM_MODEL is unset. Each value is a
# literal, provider-native model id — no cross-provider name translation is attempted
# (see ADR-014 §2).
_DEFAULT_MODEL_BY_PROVIDER = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-5",
    "google": "gemini-2.0-flash",
    "ollama": "llama3.1",
}


def get_provider() -> str:
    """Return the configured LLM provider (AI_ETL_LLM_PROVIDER env var, default: openai)."""
    return os.getenv("AI_ETL_LLM_PROVIDER", "openai").strip().lower()


def get_model_name() -> str:
    """Return the configured model name for the active provider, without constructing
    a client.

    Extracted out of get_llm() so callers that only need the model name for
    logging/cost-tracking purposes (audit/db.py's cost-per-run persistence,
    Sprint 3/ADR-008) don't have to instantiate a client just to read it back off
    `.model_name`. Falls back to the active provider's own default model
    (_DEFAULT_MODEL_BY_PROVIDER) rather than always defaulting to gpt-4o-mini, since
    an OpenAI model id is meaningless for the other three providers.
    """
    provider = get_provider()
    default_model = _DEFAULT_MODEL_BY_PROVIDER.get(provider, _DEFAULT_MODEL_BY_PROVIDER["openai"])
    return os.getenv("AI_ETL_LLM_MODEL", default_model)


def _require_env(var_name: str, provider: str) -> str:
    """Read a required credential env var, failing fast with a clear, actionable
    error instead of letting the underlying provider SDK raise a generic one.
    """
    value = os.getenv(var_name)
    if not value:
        raise RuntimeError(
            f"AI_ETL_LLM_PROVIDER={provider} requires {var_name} to be set. "
            f"See .env.example for the full list of provider-specific env vars."
        )
    return value


def _build_openai(model: str, temperature: float) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model, temperature=temperature)


def _build_anthropic(model: str, temperature: float) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    _require_env("ANTHROPIC_API_KEY", "anthropic")
    # timeout/stop have no defaults in ChatAnthropic's generated __init__ signature
    # (mypy --strict flags them as missing without an explicit value) — None matches
    # the library's own runtime default for both.
    return ChatAnthropic(model_name=model, temperature=temperature, timeout=None, stop=None)


def _build_google(model: str, temperature: float) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    _require_env("GOOGLE_API_KEY", "google")
    return ChatGoogleGenerativeAI(model=model, temperature=temperature)


def _build_ollama(model: str, temperature: float) -> BaseChatModel:
    from langchain_ollama import ChatOllama

    # No required credential — a local Ollama server has none. OLLAMA_BASE_URL
    # defaults to Ollama's own default, matching its out-of-the-box `ollama serve`.
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    return ChatOllama(model=model, temperature=temperature, base_url=base_url)


_BUILDERS = {
    "openai": _build_openai,
    "anthropic": _build_anthropic,
    "google": _build_google,
    "ollama": _build_ollama,
}


def get_llm(temperature: float = 0.0) -> BaseChatModel:
    """Return a configured chat model client for the active provider.

    Provider is read from AI_ETL_LLM_PROVIDER (default: openai — unchanged behavior
    for existing deployments). Model is read from AI_ETL_LLM_MODEL, defaulting per
    provider (see _DEFAULT_MODEL_BY_PROVIDER). Raises RuntimeError immediately (never
    silently falls back to OpenAI) if AI_ETL_LLM_PROVIDER names an unsupported
    provider or a required provider credential is missing — see ADR-014 §3.
    """
    provider = get_provider()
    builder = _BUILDERS.get(provider)
    if builder is None:
        supported = ", ".join(sorted(_BUILDERS))
        raise RuntimeError(
            f"Unsupported AI_ETL_LLM_PROVIDER={provider!r}. Supported providers: {supported}."
        )
    return builder(get_model_name(), temperature)


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
