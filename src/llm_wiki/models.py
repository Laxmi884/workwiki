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

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict

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


def make_chat_model(spec: ModelSpec, **kwargs: Any) -> BaseChatModel:
    """Build the chat model; extra kwargs (temperature, ...) go to the provider class."""
    return init_chat_model(spec.model, model_provider=spec.provider, **kwargs)
