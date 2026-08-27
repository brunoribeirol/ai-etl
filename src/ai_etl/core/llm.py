"""LLM wrapper — multi-provider client factory with retry logic and model config.

Supports OpenAI, Anthropic, Google (Gemini), and local models via Ollama, selected
via AI_ETL_LLM_PROVIDER (default: openai, preserving existing behavior). See
docs/adr/ADR-014-multi-provider-llm.md for the full rationale, the env var contract,
and what's explicitly out of scope (no automatic cross-provider fallback).

Also provides `invoke_llm()`, a per-provider circuit breaker wrapping
`BaseChatModel.invoke()` — see ADR-041 and this module's own "Circuit breaker"
section below.
"""

import os
import threading
import time
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

# Sprint 30 (ADR-031) — the allowlist backing the new per-`saved_pipeline`
# provider/model selection endpoint (`api/routers/pipelines.py`), same pattern as
# `core/scheduling.SCHEDULABLE_SOURCE_TYPES` (Sprint 13, ADR-016) validating
# `saved_pipelines.source_type`. Kept here (not in `api/`) so it stays the single
# source of truth alongside `_DEFAULT_MODEL_BY_PROVIDER` and
# `core/pricing.MODEL_PRICING_USD_PER_MILLION_TOKENS` — every model listed here
# must have a pricing entry, or its cost silently reads as "unknown" (see
# `pricing.compute_cost_usd`'s docstring). A client picking a provider/model
# outside this allowlist is rejected at the API layer before it ever reaches
# `saved_pipelines`, the same defense-in-depth posture `_validate_source_type`
# already applies to `source_type`.
ALLOWED_MODELS_BY_PROVIDER: dict[str, frozenset[str]] = {
    "openai": frozenset({"gpt-4o-mini", "gpt-4o"}),
    "anthropic": frozenset({"claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"}),
    "google": frozenset({"gemini-2.0-pro", "gemini-2.0-flash"}),
    "ollama": frozenset({"llama3.1", "llama3.3", "mistral", "qwen2.5"}),
}


class UnsupportedProviderOrModelError(ValueError):
    """Raised by `validate_provider_and_model` — a provider outside `_BUILDERS` or a
    model outside `ALLOWED_MODELS_BY_PROVIDER[provider]`. Distinct from `RuntimeError`
    (used elsewhere in this module for missing credentials/unsupported provider at
    LLM-construction time) because this one is a client input-validation error the API
    layer maps to `HTTPException(400, ...)`, not a deployment/environment misconfiguration.
    """


def validate_provider_and_model(provider: str, model: str) -> None:
    """Raise `UnsupportedProviderOrModelError` unless `provider` is a supported
    provider and `model` is in that provider's `ALLOWED_MODELS_BY_PROVIDER` allowlist.

    Used by `api/routers/pipelines.py`'s per-pipeline LLM config endpoint (Sprint 30,
    ADR-031) before persisting a tenant's choice — never called from `get_llm()`
    itself, which stays permissive (any model string reaches the provider SDK
    unvalidated, unchanged from ADR-014) since a deployment-level
    `AI_ETL_LLM_MODEL` override is an operator decision, not untrusted client input.
    """
    allowed_models = ALLOWED_MODELS_BY_PROVIDER.get(provider)
    if allowed_models is None:
        supported = ", ".join(sorted(ALLOWED_MODELS_BY_PROVIDER))
        raise UnsupportedProviderOrModelError(
            f"Unsupported provider {provider!r}. Supported providers: {supported}."
        )
    if model not in allowed_models:
        raise UnsupportedProviderOrModelError(
            f"Model {model!r} is not allowed for provider {provider!r}. "
            f"Allowed models: {sorted(allowed_models)}."
        )


def get_provider() -> str:
    """Return the configured LLM provider (AI_ETL_LLM_PROVIDER env var, default: openai)."""
    return os.getenv("AI_ETL_LLM_PROVIDER", "openai").strip().lower()


def is_llm_review_enabled() -> bool:
    """ADR-037 (Sprint 21 follow-up) — whether the second-pass LLM output review
    (`agents/analysis/reviewer.py`) runs after a successful Gold/Science sub-task.

    Opt-in, default off: this roughly doubles Gold/Science LLM cost per sub-task
    when enabled. One global env var, not a per-pipeline setting — see ADR-037
    Decision 1 for why.
    """
    return os.getenv("AI_ETL_LLM_REVIEW_ENABLED", "").strip().lower() in ("1", "true", "yes")


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


# Real bug found 2026-08-23 running a live 6-model comparison: `claude-opus-5` and
# `claude-sonnet-5` reject `temperature` outright (`400 - "temperature is deprecated
# for this model"`) — confirmed against the real Anthropic API, not documentation
# alone. Sampling params were removed for this model generation; `claude-haiku-4-5`
# (an older-tier model) still accepts it. Kept as an explicit set rather than a
# version-string heuristic, matching this module's own "explicit allowlist over
# pattern-matching" convention (see ALLOWED_MODELS_BY_PROVIDER above).
_ANTHROPIC_MODELS_WITHOUT_TEMPERATURE = frozenset({"claude-opus-5", "claude-sonnet-5"})


def _build_anthropic(model: str, temperature: float) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    _require_env("ANTHROPIC_API_KEY", "anthropic")
    # timeout/stop have no defaults in ChatAnthropic's generated __init__ signature
    # (mypy --strict flags them as missing without an explicit value) — None matches
    # the library's own runtime default for both.
    kwargs: dict[str, Any] = {"model_name": model, "timeout": None, "stop": None}
    if model not in _ANTHROPIC_MODELS_WITHOUT_TEMPERATURE:
        kwargs["temperature"] = temperature
    return ChatAnthropic(**kwargs)


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


def get_llm(
    temperature: float = 0.0,
    provider: str | None = None,
    model: str | None = None,
) -> BaseChatModel:
    """Return a configured chat model client for the active provider.

    Provider is read from AI_ETL_LLM_PROVIDER (default: openai — unchanged behavior
    for existing deployments). Model is read from AI_ETL_LLM_MODEL, defaulting per
    provider (see _DEFAULT_MODEL_BY_PROVIDER). Raises RuntimeError immediately (never
    silently falls back to OpenAI) if AI_ETL_LLM_PROVIDER names an unsupported
    provider or a required provider credential is missing — see ADR-014 §3.

    Args:
        provider: Optional per-execution override, closing the gap ADR-031 §5 flagged
            as deferred — a `saved_pipeline`'s `llm_provider`/`llm_model` (Sprint 30),
            resolved once by `services/pipeline_service.py` and threaded through
            `PipelineState["llm_provider_override"]`/callers' own parameters. `None`
            (the default) preserves ADR-014 behavior exactly: every existing call site
            that doesn't pass this keeps reading `AI_ETL_LLM_PROVIDER`. Not
            re-validated against `ALLOWED_MODELS_BY_PROVIDER` here — by the time an
            override reaches this function it was already validated once, at the API
            boundary that persisted it (`validate_provider_and_model`, called from
            `PUT /pipelines/{id}/llm-config`), matching this module's established
            "caller validates, this layer trusts" posture (see
            `test_provider_connectivity`'s docstring for the same split).
        model: Optional per-execution model override, paired with `provider` — see
            above. If `provider` is given without `model` (should not normally happen,
            since `llm_provider`/`llm_model` are always persisted together, both-or-
            neither — ADR-031 §3), falls back to that provider's own default model
            rather than a possibly-foreign `AI_ETL_LLM_MODEL` value.
    """
    resolved_provider = provider.strip().lower() if provider else get_provider()
    builder = _BUILDERS.get(resolved_provider)
    if builder is None:
        supported = ", ".join(sorted(_BUILDERS))
        var_label = "provider override" if provider else "AI_ETL_LLM_PROVIDER"
        raise RuntimeError(
            f"Unsupported {var_label}={resolved_provider!r}. Supported providers: {supported}."
        )
    if model:
        resolved_model = model
    elif provider:
        resolved_model = _DEFAULT_MODEL_BY_PROVIDER.get(
            resolved_provider, _DEFAULT_MODEL_BY_PROVIDER["openai"]
        )
    else:
        resolved_model = get_model_name()
    return builder(resolved_model, temperature)


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


def test_provider_connectivity(provider: str, model: str) -> dict[str, Any]:
    """Make one real, minimal `.invoke()` call against `provider`/`model` and report
    whether it succeeded — Sprint 30 (ADR-031), closing the gap ADR-014 left open
    ("provider selection is verified with mocked env vars... never by making a live
    API call"). Backs `POST /llm/test-connectivity`.

    Deliberately independent of `get_llm()`/`AI_ETL_LLM_PROVIDER`: this builds a
    client for the caller-supplied `provider`/`model` pair directly via `_BUILDERS`,
    so a tenant can test connectivity for a provider that isn't this deployment's
    active global one. `provider`/`model` should already be `ALLOWED_MODELS_BY_PROVIDER`-
    validated by the caller (`validate_provider_and_model`) — this function does not
    re-validate them, matching the rest of this module's "caller validates, this
    layer trusts" split.

    Never raises: every failure mode (missing credential, unreachable provider,
    invalid model id, network error) is caught and reported in the returned dict
    instead, since this is meant to be called from an API endpoint that always wants
    a 200 with a pass/fail body, not a 500 on the very "is this configured right?"
    check it exists to answer.
    """
    started = time.monotonic()
    try:
        builder = _BUILDERS.get(provider)
        if builder is None:
            raise UnsupportedProviderOrModelError(f"Unsupported provider {provider!r}.")
        llm = builder(model, 0.0)
        llm.invoke("Reply with a single word: ok")
        latency_ms = round((time.monotonic() - started) * 1000, 1)
        return {
            "ok": True,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — connectivity probe, every failure is data
        latency_ms = round((time.monotonic() - started) * 1000, 1)
        return {
            "ok": False,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "error": str(exc),
        }


# --- Circuit breaker (ADR-041, Wave 3 / X-PRO.ai gap analysis) ---------------------
#
# Narrowly scoped to LLM provider calls only, not a general resilience/bulkhead
# framework (that broader scope was deliberately deferred — see ADR-041). Before
# this, a provider outage (rate limit storm, regional incident) was invisible to
# `invoke_llm()`'s callers except as N individually-slow, individually-failing
# `.invoke()` calls — every one of Transformer/Orchestrator/Analyst/Planner/
# Advisor/Reviewer/Science's own retry-and-repair loops would each independently
# re-hit the same downed provider, and so would every other concurrent tenant's
# run. This adds a fail-fast path per provider once failures repeat, without
# touching any of those retry loops' own logic — they still see a raised
# exception (LLMCircuitOpenError IS a RuntimeError) and behave exactly as before.

_CIRCUIT_FAILURE_THRESHOLD_ENV = "AI_ETL_LLM_CIRCUIT_FAILURE_THRESHOLD"
_CIRCUIT_COOLDOWN_SECONDS_ENV = "AI_ETL_LLM_CIRCUIT_COOLDOWN_SECONDS"
_CIRCUIT_FAILURE_THRESHOLD_DEFAULT = 5
_CIRCUIT_COOLDOWN_SECONDS_DEFAULT = 60.0


class LLMCircuitOpenError(RuntimeError):
    """Raised by `invoke_llm()` instead of making a network call, when the given
    provider has failed `AI_ETL_LLM_CIRCUIT_FAILURE_THRESHOLD` times in a row and
    the `AI_ETL_LLM_CIRCUIT_COOLDOWN_SECONDS` cooldown hasn't elapsed since the
    circuit opened. A RuntimeError subclass (not a new exception family) so every
    existing `except Exception`/`except RuntimeError` retry loop already in
    Transformer/Orchestrator/Analyst/Planner/Advisor/Reviewer/Science keeps
    working unmodified — this only makes those loops fail faster against a
    provider already known to be down, it never changes what they catch.
    """


class _CircuitState:
    """Per-provider mutable state. Not a dataclass/TypedDict — this is intentionally
    mutated in place under `_circuit_lock`, never replaced wholesale."""

    __slots__ = ("consecutive_failures", "opened_at")

    def __init__(self) -> None:
        self.consecutive_failures = 0
        self.opened_at: float | None = None


_circuit_lock = threading.Lock()
_circuit_state: dict[str, _CircuitState] = {}


def _circuit_failure_threshold() -> int:
    return int(os.getenv(_CIRCUIT_FAILURE_THRESHOLD_ENV, str(_CIRCUIT_FAILURE_THRESHOLD_DEFAULT)))


def _circuit_cooldown_seconds() -> float:
    return float(os.getenv(_CIRCUIT_COOLDOWN_SECONDS_ENV, str(_CIRCUIT_COOLDOWN_SECONDS_DEFAULT)))


def reset_circuit_breakers() -> None:
    """Clear all per-provider circuit state. Test-only — module-level state would
    otherwise leak between test cases (and between pytest-xdist workers sharing a
    process), same reason `_clean_llm_env`'s autouse fixture resets env vars.
    """
    with _circuit_lock:
        _circuit_state.clear()


def invoke_llm(llm: BaseChatModel, prompt: str, provider: str | None = None) -> Any:
    """Call `llm.invoke(prompt)` through a per-provider circuit breaker.

    `provider` is resolved exactly like `get_llm()` resolves it (explicit override,
    else `AI_ETL_LLM_PROVIDER`) — used only as the circuit breaker's state key, it
    does not affect `llm` itself (already fully configured by whoever built it via
    `get_llm()`).

    Behavior:
    - Circuit CLOSED (default): calls pass straight through. A successful call
      resets `consecutive_failures` to 0. A failed call increments it and, once it
      reaches the threshold, opens the circuit and re-raises the original
      exception unchanged (callers' existing error handling/retry loops are
      unaffected on the call that trips it).
    - Circuit OPEN: calls fail fast with `LLMCircuitOpenError`, no network call
      made, until the cooldown elapses.
    - After cooldown: the next call is let through as a probe (half-open); success
      closes the circuit, failure re-opens it and restarts the cooldown.

    Deliberately NOT used by `test_provider_connectivity()` — that function exists
    specifically to let a tenant diagnose/fix a broken provider config, so it must
    always make a real call rather than fail fast on stale circuit state.
    """
    resolved_provider = provider.strip().lower() if provider else get_provider()

    with _circuit_lock:
        state = _circuit_state.setdefault(resolved_provider, _CircuitState())
        if state.opened_at is not None:
            elapsed = time.monotonic() - state.opened_at
            cooldown = _circuit_cooldown_seconds()
            if elapsed < cooldown:
                raise LLMCircuitOpenError(
                    f"Circuit open for LLM provider {resolved_provider!r} after "
                    f"{state.consecutive_failures} consecutive failures. "
                    f"Retry in {cooldown - elapsed:.0f}s."
                )
            # Cooldown elapsed: let this call through as a half-open probe. Outcome
            # decides below whether the circuit closes or re-opens.

    try:
        response = llm.invoke(prompt)
    except Exception:
        with _circuit_lock:
            state = _circuit_state.setdefault(resolved_provider, _CircuitState())
            state.consecutive_failures += 1
            if state.consecutive_failures >= _circuit_failure_threshold():
                state.opened_at = time.monotonic()
        raise
    else:
        with _circuit_lock:
            state = _circuit_state.setdefault(resolved_provider, _CircuitState())
            state.consecutive_failures = 0
            state.opened_at = None
        return response
