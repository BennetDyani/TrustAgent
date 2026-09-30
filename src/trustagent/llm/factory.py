"""One place that builds chat models, chosen by config.

Callers ask for a *role* ("generator" or "judge"), not a vendor. Switching
Gemini for Claude is a config change, not a code change. Every provider error
surfaces as ``LLMUnavailable`` so callers have exactly one failure to handle
(the graceful-degradation path).
"""

import logging
from functools import cache
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from trustagent.config import Provider, Settings, get_settings

Role = Literal["generator", "judge"]


class _DropAFCNotice(logging.Filter):
    """google-genai warns once that LangChain calls ``generate_content`` directly (its internal
    choice, not ours). Drop only that message; every other SDK warning still shows."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.getMessage().startswith("Direct use of automatic function calling")


logging.getLogger("google_genai.models").addFilter(_DropAFCNotice())


class LLMUnavailable(RuntimeError):
    """The model could not be reached or returned something unusable."""


class LLMNotConfigured(LLMUnavailable):
    """No API key for the configured provider."""


def provider_api_key(settings: Settings, provider: Provider) -> str:
    secret = {
        "gemini": settings.google_api_key,
        "groq": settings.groq_api_key,
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
    }[provider]
    if secret is None or not secret.get_secret_value():
        env = {"gemini": "GOOGLE_API_KEY"}.get(provider, f"{provider.upper()}_API_KEY")
        raise LLMNotConfigured(f"{env} is not set; add it to .env to use the {provider} provider.")
    return secret.get_secret_value()


@cache
def _rate_limiter(provider: Provider, per_minute: float) -> InMemoryRateLimiter:
    """One limiter per provider, shared by every model instance in this process."""
    return InMemoryRateLimiter(requests_per_second=per_minute / 60, check_every_n_seconds=0.1)


def model_for(role: Role, settings: Settings | None = None) -> tuple[Provider, str]:
    s = settings or get_settings()
    return (s.llm_provider, s.llm_model) if role == "generator" else (s.judge_provider, s.judge_model)


def get_chat_model(
    role: Role = "generator",
    *,
    provider: Provider | None = None,
    model: str | None = None,
    settings: Settings | None = None,
) -> BaseChatModel:
    """Build a chat model. ``provider``/``model`` override the role's configured choice (used by evals)."""
    s = settings or get_settings()
    default_provider, default_model = model_for(role, s)
    provider = provider or default_provider
    model = model or default_model
    # `timeout` is accepted by all four classes (as a field name or alias); checked against installed versions.
    common = {"temperature": s.llm_temperature, "max_retries": s.llm_max_retries, "timeout": s.llm_timeout_seconds}
    if per_minute := s.llm_requests_per_minute.get(provider):
        common["rate_limiter"] = _rate_limiter(provider, per_minute)

    # Imports are local so a missing optional provider package only matters if it is selected.
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        # Gemini 3.x models use fixed sampling and ignore temperature (the SDK warns), so
        # we don't pretend to control it. Run-to-run variance is measured in evals (ADR-025).
        common.pop("temperature")
        return ChatGoogleGenerativeAI(model=model, google_api_key=provider_api_key(s, provider), **common)
    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=model, api_key=provider_api_key(s, provider), **common)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, api_key=provider_api_key(s, provider), **common)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, api_key=provider_api_key(s, provider), **common)
    raise ValueError(f"Unknown provider: {provider}")


def structured(llm: BaseChatModel, schema: type[BaseModel], *, include_raw: bool = False) -> Runnable:
    """``llm.with_structured_output(schema)`` using each provider's most reliable method.

    Gemini and Groq use native JSON-schema decoding (Groq in strict mode, which
    guarantees the output matches the schema). Other providers use their default.
    """
    name = type(llm).__name__
    if name == "ChatGoogleGenerativeAI":
        return llm.with_structured_output(schema, method="json_schema", include_raw=include_raw)
    if name == "ChatGroq":
        return llm.with_structured_output(schema, method="json_schema", strict=True, include_raw=include_raw)
    return llm.with_structured_output(schema, include_raw=include_raw)
