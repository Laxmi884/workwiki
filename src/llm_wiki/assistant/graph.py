"""The `assistant` graph for `langgraph dev` / Studio (decision 0008).

`make_graph` is a graph factory: the server calls it per run with the run's context,
so a Studio assistant (or `context=`) can set `vault_path` and `model`; missing keys
fall back to `WIKI_VAULT_PATH` and `WIKI_MODEL`. While the server only reads the
graph's schema there is no run, and the settings are used.

The server calls factories on its event loop, and building reads the disk (the vault
path, the skills), so `make_graph` is async and builds in a worker thread; `langgraph
dev` refuses blocking calls on the loop (`BlockingError`).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from typing_extensions import TypedDict

from llm_wiki.assistant.agent import build_assistant
from llm_wiki.config import get_settings
from llm_wiki.model_setup import configured_fallbacks, configured_model

if TYPE_CHECKING:
    from langgraph_sdk.runtime import ServerRuntime


class AssistantContext(TypedDict, total=False):
    """Per-run settings; every key is optional.

    Attributes:
        vault_path: Vault folder, e.g. "~/WorkDev".
        model: "provider:model", e.g. "openrouter:deepseek/deepseek-v4.1-flash".
    """

    vault_path: str
    model: str


def run_choice(context: AssistantContext | None) -> tuple[Path, str | None]:
    """Return (vault, model override) for a run's context."""
    context = context or {}
    vault = context.get("vault_path")
    path = Path(vault).expanduser().resolve() if vault else get_settings().vault_path
    return path, context.get("model")


def graph_for(context: AssistantContext | None) -> Any:
    """Build the assistant for a run's context (blocking: it reads the disk)."""
    vault, model = run_choice(context)
    return build_assistant(
        vault,
        configured_model(model),
        fallbacks=configured_fallbacks(),
        context_schema=AssistantContext,
    )


async def make_graph(runtime: ServerRuntime) -> Any:
    """Build the assistant for this run (the server passes its runtime)."""
    ert = runtime.execution_runtime
    context = ert.context if ert is not None else None
    return await asyncio.to_thread(graph_for, context)  # type: ignore[arg-type]
