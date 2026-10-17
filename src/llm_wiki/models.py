"""Pick a chat model from a "provider:model" spec, local (Ollama) or cloud.

Examples:
    anthropic:claude-haiku-5-5          (alias: claude:claude-haiku-5-5)
    ollama:llama3.1:8b                  (local; OLLAMA_HOST for a non-default server)
    openrouter:meta-llama/llama-3.3-70b-instruct:free

Any provider `init_chat_model` supports works once its package is installed
(`uv add langchain-<provider>`). API keys come from each provider's own env var
(ANTHROPIC_API_KEY, OPENROUTER_API_KEY, ...), never from this module.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict

# OpenRouter's `reasoning.effort` values, most to least; "none" turns reasoning off.
# https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
ReasoningEffort = Literal["max", "xhigh", "high", "medium", "low", "minimal", "none"]

# Friendly names → init_chat_model provider names.
_PROVIDER_ALIASES = {
    "claude": "anthropic",
    "google": "google_genai",
    "gemini": "google_genai",
}


class ModelSpec(BaseModel):
    """A parsed "provider:model" choice.

    Attributes:
        provider: init_chat_model provider, e.g. "anthropic", "ollama", "openrouter".
        model: Provider's model name; may itself contain ":" or "/".
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str

    @classmethod
    def parse(cls, spec: str) -> ModelSpec:
        """Parse "provider:model", splitting on the first ":" only.

        Raises:
            ValueError: No provider or no model name.
        """
        provider, sep, model = spec.strip().partition(":")
        provider, model = provider.strip().lower(), model.strip()
        if not sep or not provider or not model:
            raise ValueError(
                f"model must look like 'provider:model' "
                f"(e.g. 'ollama:llama3.1:8b'), got {spec!r}"
            )
        return cls(provider=_PROVIDER_ALIASES.get(provider, provider), model=model)

    @property
    def spec(self) -> str:
        """Return the normalised "provider:model" string."""
        return f"{self.provider}:{self.model}"


def choose_model_spec(override: str | None, default: str | None) -> ModelSpec:
    """Return the run's model: `override` (run context) wins over `default` (WIKI_MODEL).

    Raises:
        ValueError: Neither is set, or the chosen one is malformed.
    """
    for value in (override, default):
        if value is not None and value.strip():
            return ModelSpec.parse(value)
    raise ValueError(
        "no model chosen: set WIKI_MODEL in .env or `model` in the run context"
    )


def timeout_kwargs(provider: str, seconds: float | None) -> dict[str, Any]:
    """Return the provider's request-timeout argument for `seconds`, or {} for none.

    OpenRouter's `timeout` is in milliseconds; the other cloud providers take seconds.
    Local Ollama gets no timeout: a slow local model is still working, not hung.
    """
    if seconds is None or provider == "ollama":
        return {}
    if provider == "openrouter":
        return {"timeout": int(seconds * 1000)}
    return {"timeout": seconds}


def reasoning_kwargs(provider: str, effort: ReasoningEffort | None) -> dict[str, Any]:
    """Return the provider's reasoning-effort argument for `effort`, or {} for none.

    Only OpenRouter gets one: `ChatOpenRouter(reasoning=...)` is sent as the request's
    `reasoning` object, and OpenRouter maps the effort for models without a native
    effort control (e.g. to a token budget). Anthropic's `reasoning_effort` field also
    switches adaptive thinking on, so it is not mapped; Ollama already runs with
    reasoning off. Unset leaves the provider's default.
    """
    if effort is None or provider != "openrouter":
        return {}
    return {"reasoning": {"effort": effort}}


def without_sdk_retries(chat: BaseChatModel) -> BaseChatModel:
    """Turn off the OpenRouter SDK's own retries; return `chat`.

    The SDK quietly retries timeouts, connection errors and 5xx with backoff: for up
    to `max_retries` × 150 s, or a full hour when `max_retries=0` (no config means its
    default). One hung provider then stalls a run for many minutes with nothing in
    the logs. With "none", each request is sent once and a failure reaches our retry
    middleware (`ingest.agent.model_middleware`), which logs and backs off.
    """
    from openrouter.utils.retries import BackoffStrategy, RetryConfig  # noqa: PLC0415

    sdk = getattr(getattr(chat, "client", None), "sdk_configuration", None)
    if sdk is not None:
        sdk.retry_config = RetryConfig("none", BackoffStrategy(500, 500, 1.0, 0), False)
    return chat


def make_chat_model(spec: ModelSpec, **kwargs: Any) -> BaseChatModel:
    """Build the chat model; extra kwargs (temperature, ...) go to the provider class."""
    return init_chat_model(spec.model, model_provider=spec.provider, **kwargs)
