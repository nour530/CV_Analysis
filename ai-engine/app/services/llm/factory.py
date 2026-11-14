from __future__ import annotations

from app.config import LLMSettings
from app.services.llm.base import LLMProvider
from app.services.llm.exceptions import LLMProviderNotFoundError
from app.services.llm.mock_provider import MockLLMProvider
from app.services.llm.openai_provider import OpenAIProvider


def create_llm_provider(settings: LLMSettings | None = None) -> LLMProvider:
    resolved = settings or LLMSettings.from_env()
    if resolved.provider == "mock":
        return MockLLMProvider()
    if resolved.provider == "openai":
        return OpenAIProvider(resolved)
    raise LLMProviderNotFoundError(f"Unknown LLM provider: {resolved.provider}")
