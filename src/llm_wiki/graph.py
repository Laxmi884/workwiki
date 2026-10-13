"""Scaffold graph: answers `user_query` with the chosen model.

Placeholder until the phase-2 ingest graph; it shows how a run picks its model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from typing_extensions import TypedDict

from llm_wiki.config import get_settings
from llm_wiki.models import choose_model_spec, make_chat_model


class Context(TypedDict, total=False):
    """Per-run settings. Set them on a Studio assistant or pass `context=` on invoke.

    Every key is optional; a missing key falls back to the `WIKI_*` setting.

    Attributes:
        vault_path: Vault folder (not used yet).
        model: "provider:model", e.g. "ollama:llama3.1:8b",
            "anthropic:claude-haiku-5-5", "openrouter:openai/gpt-5-mini".
    """

    vault_path: str
    model: str


@dataclass
class State:
    """Graph state.

    Attributes:
        user_query: The question.
        answer: The model's reply.
        model_used: The "provider:model" that answered.
    """

    user_query: str
    answer: str = ""
    model_used: str = ""


async def call_model(state: State, runtime: Runtime[Context]) -> dict[str, Any]:
    """Answer the query with the run's model (context `model`, else WIKI_MODEL)."""
    context = runtime.context or {}
    spec = choose_model_spec(context.get("model"), get_settings().model)
    reply = await make_chat_model(spec).ainvoke(state.user_query)
    return {"answer": reply.text, "model_used": spec.spec}


graph = (
    StateGraph(State, context_schema=Context)
    .add_node(call_model)
    .add_edge("__start__", "call_model")
    .compile(name="llm-wiki scaffold")
)
