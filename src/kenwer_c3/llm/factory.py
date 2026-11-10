"""Resolve ReasoningProvider from settings - mock by default."""

from __future__ import annotations

from kenwer_c3.llm.base import ReasoningProvider, ReasoningProviderError
from kenwer_c3.llm.mock_provider import MockReasoningProvider
from kenwer_c3.llm.openai_provider import OpenAICompatibleReasoningProvider
from kenwer_c3.settings import Settings, get_settings


def get_reasoning_provider(settings: Settings | None = None) -> ReasoningProvider:
    settings = settings or get_settings()
    provider = (settings.llm_provider or "mock").lower()
    if provider in ("mock", "none", ""):
        return MockReasoningProvider()
    if provider in ("openai", "openai_compatible", "production"):
        if not settings.llm_api_key:
            # Safe degrade: never require external key for core learning path
            return MockReasoningProvider()
        return OpenAICompatibleReasoningProvider(settings)
    raise ReasoningProviderError(f"Unknown LLM_PROVIDER: {provider}")
