import asyncio
import shutil

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from llm_wiki.assistant.agent import build_assistant
from llm_wiki.ingest import nodes
from llm_wiki.ingest.agent import build_ingest_agent
from llm_wiki.ingest.graph import builder as agent_builder
from llm_wiki.middleware.tool_guard import ToolGuardMiddleware, tool_name
from llm_wiki.runner import IngestRunner

from .scripted_model import ToolScript, call


@tool
def lookup(x: int) -> str:
    """Look x up."""
    return f"found {x}"


@tool
def delete_all() -> str:
    """Delete everything."""
    return "deleted"


class Offered(ToolScript):
    """Keeps the tool names bound on each model call."""

    offered: list[list[str]] = []

    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        self.offered.append(sorted(filter(None, map(tool_name, tools))))
        return self


def test_tool_name_reads_tools_and_provider_dicts():
    assert tool_name(lookup) == "lookup"
    assert tool_name({"name": "a"}) == "a"
    assert tool_name({"type": "function", "function": {"name": "b"}}) == "b"


def test_unlisted_tools_are_hidden_and_refused():
    model = Offered(
        offered=[],
        messages=iter(
            [call("lookup", x=1), call("delete_all"), call("made_up"), AIMessage("end")]
        ),
    )
    agent = create_agent(
        model, tools=[lookup, delete_all], middleware=[ToolGuardMiddleware(["lookup"])]
    )
    out = agent.invoke({"messages": [("user", "hi")]})
    assert model.offered[0] == ["lookup"]
    replies = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert replies[0].content == "found 1"
    assert replies[1].status == "error"
    assert replies[1].content.startswith("REFUSED: delete_all is not one of your tools")
    assert replies[2].content.startswith("REFUSED: made_up is not one of your tools")


def test_the_async_path_refuses_too():
    model = Offered(offered=[], messages=iter([call("delete_all"), AIMessage("end")]))
    agent = create_agent(
        model, tools=[lookup, delete_all], middleware=[ToolGuardMiddleware(["lookup"])]
    )
    out = asyncio.run(agent.ainvoke({"messages": [("user", "hi")]}))
    assert out["messages"][-2].content.startswith("REFUSED: delete_all")
    assert model.offered[0] == ["lookup"]


def test_ingest_agent_is_not_offered_task(fixture_vault):
    model = Offered(offered=[], messages=iter([AIMessage("ok")]))
    build_ingest_agent(fixture_vault, model).invoke({"messages": [("user", "hi")]})
    assert model.offered[0] == sorted(
        ["ask_user", "file_fact", "glob", "grep", "ls", "map_task", "propose_page"]
        + ["read_file", "update_summary", "withdraw_fact"]
    )


def test_ingest_cannot_start_a_subagent_to_stage_facts(vault_copy, monkeypatch):
    script = ToolScript(
        messages=iter(
            [
                call(
                    "task",
                    description="File the Sam line",
                    subagent_type="general-purpose",
                ),
                # had `task` run, this would be the subagent filing the fact
                call(
                    "file_fact",
                    target="person-sam-ortiz",
                    section="Notes",
                    text="Wants to own the search feature.",
                    line=29,
                ),
                AIMessage("Filed 1 fact."),
            ]
        )
    )
    monkeypatch.setattr(nodes, "chat_model", lambda runtime: script)
    monkeypatch.setattr(nodes, "fallback_models", lambda runtime: [])
    runner = IngestRunner.in_memory(
        context={"vault_path": str(vault_copy)}, graph_builder=agent_builder
    )
    result = runner.start("00_raw/daily/2026-10-12.md")
    stats = runner.graph.get_state(runner._config(result.thread_id)).values[
        "agent_stats"
    ]
    assert stats["refused"] == ["task: not allowed"]
    # the main agent filed the fact itself, so it counts as its own call
    assert stats["calls_by_tool"] == {"task": 1, "file_fact": 1}


@pytest.mark.parametrize("subagent", ["review-writer", "general-purpose"])
def test_assistant_keeps_task_and_its_subagents_keep_their_tools(vault_copy, subagent):
    shutil.rmtree(vault_copy / ".git", ignore_errors=True)
    answer = (
        call("BragDraft", period="2026 Q4", sections=[], gaps=[])
        if subagent == "review-writer"
        else AIMessage("sub done")
    )
    model = Offered(
        offered=[],
        messages=iter(
            [
                call("task", description="x", subagent_type=subagent),
                answer,
                AIMessage("ok"),
            ]
        ),
    )
    out = build_assistant(vault_copy, model).invoke({"messages": [("user", "hi")]})
    reads = ["find_tasks", "glob", "grep", "ls", "read_file"]
    assert model.offered[0] == sorted([*reads, "task"])
    assert set(reads) <= set(model.offered[1]) and "task" not in model.offered[1]
    task_reply = next(m for m in out["messages"] if isinstance(m, ToolMessage))
    assert not task_reply.content.startswith("REFUSED")
