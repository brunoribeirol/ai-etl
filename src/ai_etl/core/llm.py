"""LLM wrapper — OpenAI client with retry logic and model configuration."""

import os
from typing import Any

from langchain_openai import ChatOpenAI


def get_llm(temperature: float = 0.0) -> ChatOpenAI:
    """Return a configured ChatOpenAI instance.

    Model is read from AI_ETL_LLM_MODEL env var (default: gpt-4o-mini).
    Use gpt-4o-mini for development; gpt-4o for the final case study.
    """
    model = os.getenv("AI_ETL_LLM_MODEL", "gpt-4o-mini")
    return ChatOpenAI(model=model, temperature=temperature)
