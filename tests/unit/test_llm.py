"""Unit tests for core/llm.py's multi-provider factory (ADR-014).

No real credentials are used — provider selection is verified by mocking env vars
and asserting the correct LangChain class is instantiated with the correct model,
never by making a live API call. See ADR-014's Consequences section for why.
"""

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from ai_etl.core.llm import (
    ALLOWED_MODELS_BY_PROVIDER,
    UnsupportedProviderOrModelError,
    get_llm,
    get_model_name,
    get_provider,
    is_llm_review_enabled,
    validate_provider_and_model,
)

# Aliased on import: a bare `test_provider_connectivity` import would be a
# module-level `test_*` callable pytest tries to collect and call with no args —
# see ADR-031's own note on this in the PR description.
from ai_etl.core.llm import test_provider_connectivity as check_provider_connectivity


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with a clean slate for all provider-related env vars."""
    for var in (
        "AI_ETL_LLM_PROVIDER",
        "AI_ETL_LLM_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OLLAMA_BASE_URL",
        "AI_ETL_LLM_REVIEW_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)


class TestGetProvider:
    def test_defaults_to_openai(self) -> None:
        assert get_provider() == "openai"

    def test_reads_env_var_case_insensitively(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_ETL_LLM_PROVIDER", "Anthropic")
        assert get_provider() == "anthropic"


class TestGetModelName:
    def test_openai_default(self) -> None:
        assert get_model_name() == "gpt-4o-mini"

    def test_anthropic_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_ETL_LLM_PROVIDER", "anthropic")
        assert get_model_name() == "claude-sonnet-5"

    def test_google_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_ETL_LLM_PROVIDER", "google")
        assert get_model_name() == "gemini-2.0-flash"

    def test_ollama_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_ETL_LLM_PROVIDER", "ollama")
        assert get_model_name() == "llama3.1"

    def test_explicit_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_ETL_LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("AI_ETL_LLM_MODEL", "claude-opus-5")
        assert get_model_name() == "claude-opus-5"


class TestGetLlmProviderSelection:
    """Each provider must instantiate the correct LangChain BaseChatModel subclass
    with the expected model — this is the "switching AI_ETL_LLM_PROVIDER really
    switches the instantiated class" proof the task's Definition of Done requires.
    """

    def test_openai_is_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        llm = get_llm()
        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == "gpt-4o-mini"

    def test_anthropic_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_ETL_LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        llm = get_llm()
        assert isinstance(llm, ChatAnthropic)
        assert llm.model == "claude-sonnet-5"

    def test_anthropic_sonnet_and_opus_omit_temperature(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Real bug found 2026-08-23 (confirmed against a real Anthropic API call,
        not documentation alone): claude-opus-5/claude-sonnet-5 reject `temperature`
        outright (`400 - "temperature is deprecated for this model"`). get_llm() must
        not pass it through for these two models."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        for model in ("claude-opus-5", "claude-sonnet-5"):
            llm = get_llm(provider="anthropic", model=model)
            assert isinstance(llm, ChatAnthropic)
            assert llm.model == model
            assert llm.temperature is None

    def test_anthropic_haiku_still_accepts_temperature(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        llm = get_llm(provider="anthropic", model="claude-haiku-4-5", temperature=0.3)
        assert isinstance(llm, ChatAnthropic)
        assert llm.temperature == 0.3

    def test_google_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_ETL_LLM_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
        llm = get_llm()
        assert isinstance(llm, ChatGoogleGenerativeAI)
        assert llm.model == "gemini-2.0-flash"

    def test_ollama_provider_no_credential_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_ETL_LLM_PROVIDER", "ollama")
        llm = get_llm()
        assert isinstance(llm, ChatOllama)
        assert llm.model == "llama3.1"

    def test_ollama_respects_custom_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_ETL_LLM_PROVIDER", "ollama")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.internal:11434")
        llm = get_llm()
        assert llm.base_url == "http://ollama.internal:11434"

    def test_temperature_is_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        llm = get_llm(temperature=0.7)
        assert llm.temperature == 0.7


class TestGetLlmOverride:
    """Sprint 30/gap-closing (ADR-031 §5) — get_llm()'s optional provider/model
    override, closing the gap ADR-031 §5 flagged: a saved pipeline's configured
    provider/model must actually reach the LLM client construction, not just the
    deployment-global AI_ETL_LLM_PROVIDER/AI_ETL_LLM_MODEL env vars.
    """

    def test_no_override_preserves_env_var_behavior(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_ETL_LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("AI_ETL_LLM_MODEL", "claude-opus-5")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        llm = get_llm()
        assert isinstance(llm, ChatAnthropic)
        assert llm.model == "claude-opus-5"

    def test_provider_override_wins_over_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_ETL_LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        llm = get_llm(provider="anthropic", model="claude-haiku-4-5")
        assert isinstance(llm, ChatAnthropic)
        assert llm.model == "claude-haiku-4-5"

    def test_model_override_wins_over_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        monkeypatch.setenv("AI_ETL_LLM_MODEL", "gpt-4o-mini")
        llm = get_llm(model="gpt-4o")
        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == "gpt-4o"

    def test_provider_override_without_model_uses_that_providers_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should not normally happen (llm_provider/llm_model are always persisted
        together — ADR-031 §3), but falls back safely rather than reusing a
        possibly-foreign AI_ETL_LLM_MODEL value."""
        monkeypatch.setenv("AI_ETL_LLM_MODEL", "gpt-4o-mini")  # an OpenAI model id
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        llm = get_llm(provider="anthropic")
        assert isinstance(llm, ChatAnthropic)
        assert llm.model == "claude-sonnet-5"  # anthropic's own default, not gpt-4o-mini

    def test_unsupported_provider_override_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(RuntimeError, match="Unsupported provider override"):
            get_llm(provider="not-a-real-provider", model="whatever")

    def test_missing_credential_for_override_still_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            get_llm(provider="anthropic", model="claude-sonnet-5")


class TestGetLlmFailFast:
    """Missing credentials or an unknown provider must fail immediately and clearly
    — never silently fall back to OpenAI (ADR-014 §3).
    """

    def test_anthropic_without_api_key_raises_clear_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AI_ETL_LLM_PROVIDER", "anthropic")
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            get_llm()

    def test_google_without_api_key_raises_clear_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AI_ETL_LLM_PROVIDER", "google")
        with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
            get_llm()

    def test_unknown_provider_raises_immediately(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_ETL_LLM_PROVIDER", "not-a-real-provider")
        with pytest.raises(RuntimeError, match="Unsupported AI_ETL_LLM_PROVIDER"):
            get_llm()


class TestValidateProviderAndModel:
    """Sprint 30 (ADR-031) — the allowlist backing `PUT /pipelines/{id}/llm-config`."""

    def test_every_provider_has_an_allowed_models_entry(self) -> None:
        assert set(ALLOWED_MODELS_BY_PROVIDER) == {"openai", "anthropic", "google", "ollama"}

    def test_accepts_allowed_pair(self) -> None:
        validate_provider_and_model("openai", "gpt-4o-mini")  # must not raise

    def test_rejects_unsupported_provider(self) -> None:
        with pytest.raises(UnsupportedProviderOrModelError, match="Unsupported provider"):
            validate_provider_and_model("not-a-provider", "gpt-4o-mini")

    def test_rejects_model_outside_providers_allowlist(self) -> None:
        with pytest.raises(UnsupportedProviderOrModelError, match="not allowed for provider"):
            validate_provider_and_model("anthropic", "gpt-4o-mini")


class TestProviderConnectivity:
    """Sprint 30 (ADR-031) — closes the gap ADR-014 left open: a real `.invoke()`
    call, never mocked at the LangChain-class level (only the underlying HTTP/SDK
    call itself is faked here, same as the rest of this test module does for
    credential presence).
    """

    def test_success_reports_ok_with_latency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")

        class _FakeResponse:
            content = "ok"

        monkeypatch.setattr(ChatOpenAI, "invoke", lambda self, *_a, **_kw: _FakeResponse())

        result = check_provider_connectivity("openai", "gpt-4o-mini")

        assert result["ok"] is True
        assert result["provider"] == "openai"
        assert result["model"] == "gpt-4o-mini"
        assert result["error"] is None
        assert result["latency_ms"] >= 0

    def test_missing_credential_reports_ok_false_not_a_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No ANTHROPIC_API_KEY set — _build_anthropic's _require_env raises
        # RuntimeError, which test_provider_connectivity must catch and report,
        # never propagate.
        result = check_provider_connectivity("anthropic", "claude-sonnet-5")

        assert result["ok"] is False
        assert result["error"] is not None
        assert "ANTHROPIC_API_KEY" in result["error"]

    def test_invoke_failure_reports_ok_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")

        def _raise(*_a: object, **_kw: object) -> None:
            raise ConnectionError("network unreachable")

        monkeypatch.setattr(ChatOpenAI, "invoke", _raise)

        result = check_provider_connectivity("openai", "gpt-4o-mini")

        assert result["ok"] is False
        assert "network unreachable" in result["error"]

    def test_unsupported_provider_reports_ok_false_not_a_raise(self) -> None:
        result = check_provider_connectivity("not-a-provider", "some-model")

        assert result["ok"] is False
        assert result["error"] is not None


class TestIsLlmReviewEnabled:
    """ADR-037 (Sprint 21 follow-up) — opt-in, default off."""

    def test_defaults_to_false(self) -> None:
        assert is_llm_review_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "True", "yes", "YES"])
    def test_truthy_values_enable_it(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("AI_ETL_LLM_REVIEW_ENABLED", value)
        assert is_llm_review_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", ""])
    def test_falsy_values_keep_it_disabled(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("AI_ETL_LLM_REVIEW_ENABLED", value)
        assert is_llm_review_enabled() is False
