"""The chat models a run uses, built from settings (decision 0003) for both agents.

`WIKI_MODEL` (or a run's `model` override) and the `WIKI_FALLBACK_MODELS` backups, each
with the request timeout (`WIKI_MODEL_TIMEOUT`) and, for OpenRouter, the reasoning
effort (`WIKI_REASONING_EFFORT`) and the SDK's own retries off (see
`models.without_sdk_retries`).
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from llm_wiki.config import get_settings
from llm_wiki.models import (
    ModelSpec,
    ReasoningEffort,
    choose_model_spec,
    make_chat_model,
    reasoning_kwargs,
    timeout_kwargs,
    without_sdk_retries,
)


def build_model(
    spec: ModelSpec,
    timeout: float | None = None,
    reasoning_effort: ReasoningEffort | None = None,
) -> BaseChatModel:
    """Return the chat model for `spec`, set up for agent runs (temperature 0).

    `reasoning_effort` reaches OpenRouter models only (`models.reasoning_kwargs`).
    """
    if spec.provider == "ollama":
        # Local models: no hidden "thinking" (minutes per call). Ollama's default
        # 4096-token window is too small for prompt + detailed answer; the output cap
        # is large and only stops a runaway answer.
        return make_chat_model(
            spec, temperature=0, reasoning=False, num_ctx=32768, num_predict=8192
        )
    chat = make_chat_model(
        spec,
        temperature=0,
        **timeout_kwargs(spec.provider, timeout),
        **reasoning_kwargs(spec.provider, reasoning_effort),
    )
    return without_sdk_retries(chat) if spec.provider == "openrouter" else chat


def configured_model(override: str | None = None) -> BaseChatModel:
    """Return the run's model: `override` ("provider:model"), else `WIKI_MODEL`."""
    settings = get_settings()
    return build_model(
        choose_model_spec(override, settings.model),
        settings.model_timeout,
        settings.reasoning_effort,
    )


def configured_fallbacks() -> list[BaseChatModel]:
    """Return the backup models (`WIKI_FALLBACK_MODELS`), tried in order on failure."""
    settings = get_settings()
    return [
        build_model(
            ModelSpec.parse(s), settings.model_timeout, settings.reasoning_effort
        )
        for s in settings.fallback_models
    ]
