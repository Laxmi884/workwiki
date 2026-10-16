"""The real ingest and assistant graphs with scripted models, for `langgraph dev` in tests.

The contract tests (`test_backend_contract.py`, opt-in `WIKI_SERVER_TESTS=1`) start a dev
server on `tests/server/langgraph.json`. The server runs graphs in its own process, so
tests can't monkeypatch its models: this module installs them when the server loads it.
No model is called and nothing is spent.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from langgraph_sdk.runtime import ServerRuntime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/, for unit_tests

from unit_tests.scripted_model import AgentRounds, EchoModel, bi_tool_decision

from llm_wiki.assistant.agent import build_assistant
from llm_wiki.assistant.graph import AssistantContext, run_choice
from llm_wiki.ingest import nodes
from llm_wiki.ingest_agent.graph import builder

nodes.chat_model = AgentRounds([bi_tool_decision()])
nodes.fallback_models = lambda runtime: []

ingest = builder.compile(name="ingest")


def _build_assistant(context: Any) -> Any:
    vault, _ = run_choice(context)
    return build_assistant(vault, EchoModel(), context_schema=AssistantContext)


async def assistant(runtime: ServerRuntime) -> Any:
    """The assistant over the run's vault, answering with `EchoModel`.

    As in `assistant/graph.py:make_graph`: the server passes the runtime only to a
    parameter annotated `ServerRuntime`, and building runs off the event loop.
    """
    ert = runtime.execution_runtime
    return await asyncio.to_thread(
        _build_assistant, ert.context if ert is not None else None
    )
