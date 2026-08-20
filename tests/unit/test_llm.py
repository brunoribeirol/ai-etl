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

from ai_etl.core.llm import get_llm, get_model_name, get_provider


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
