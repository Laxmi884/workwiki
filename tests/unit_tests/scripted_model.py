"""Stand-in chat models for agent tests: scripted tool calls, one script per round."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


class ToolScript(GenericFakeChatModel):
    """A chat model for agents: replies with the scripted `AIMessage`s in order.

    `bind_tools` is accepted and ignored, so scripted tool calls go straight to the
    agent's tool node. Build calls with `call(name, **args)`.
    """

    def bind_tools(self, tools: Any, **kwargs: Any) -> ToolScript:
        return self


def call(name: str, **args: Any) -> AIMessage:
    """Return an AIMessage asking for one tool call."""
    tool_call = {"name": name, "args": args, "id": f"call-{uuid4().hex[:8]}"}
    return AIMessage(content="", tool_calls=[tool_call])


class AgentRounds:
    """Stands in for `nodes.chat_model` in agent-graph tests: one script per round.

    Each call (one per agent round: the first run, then each revise) returns a fresh
    `ToolScript` for the next scripted round; once they run out, the last round
    repeats. `briefs` keeps each round's first message (the brief code wrote), so
    tests can check what the agent was told, e.g. the user's revise feedback.
    """

    def __init__(self, *rounds: list[AIMessage]) -> None:
        self.rounds = [list(r) for r in rounds]
        self.briefs: list[str] = []

    def __call__(self, runtime: Any) -> ToolScript:
        del runtime
        n = min(len(self.briefs), len(self.rounds) - 1)
        self.briefs.append("")  # filled in by the first model call of this round
        owner, index = self, len(self.briefs) - 1

        class _Recording(ToolScript):
            def _generate(self, messages, *args, **kwargs):  # type: ignore[override]
                if not owner.briefs[index]:
                    owner.briefs[index] = str(messages[1].content)  # after the system
                return super()._generate(messages, *args, **kwargs)

        return _Recording(messages=iter([*self.rounds[n], AIMessage("Done.")]))


def bi_tool_decision() -> AIMessage:
    """The fixture note's line 23, filed on the decision log (one scripted round)."""
    return call(
        "file_fact",
        target="decision-log",
        section="Decisions",
        text="Not recorded in the note.",
        line=23,
        title="Keep the old BI tool until Q1",
    )


class EchoModel(GenericFakeChatModel):
    """A chat model whose reply lists the user and assistant turns it was sent.

    "What is Atlas? | A risk analytics project. | Is it on track?": tests (and a test
    server, where nothing can be recorded from outside) can check the history arrived.

    A question starting "search:" first says "Let me look." with an `ls` of the vault,
    then answers in content blocks (as Anthropic models do), so the answer streams
    after a tool call. It streams like a real model: a tool call as one chunk.
    """

    messages: Any = iter(())

    def bind_tools(self, tools: Any, **kwargs: Any) -> EchoModel:
        return self

    def _reply(self, messages: list[Any]) -> AIMessage:
        turns = [
            text
            for m in messages
            if m.type in ("human", "ai") and (text := _plain(m.content))
        ]
        reply = " | ".join(turns)
        question = next((m for m in reversed(messages) if m.type == "human"), None)
        if question is None or not _plain(question.content).startswith("search:"):
            return AIMessage(reply)
        if messages[-1].type != "tool":
            tool_call = {"name": "ls", "args": {"path": "/vault/"}, "id": "call-ls"}
            return AIMessage(content="Let me look.", tool_calls=[tool_call])
        return AIMessage(content=[{"type": "text", "text": reply}])

    def _generate(self, messages, *args, **kwargs):  # type: ignore[override]
        from langchain_core.outputs import ChatGeneration, ChatResult

        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    def _stream(self, messages, *args, **kwargs):  # type: ignore[override]
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        message = self._reply(messages)
        chunks = [
            {
                "name": c["name"],
                "args": json.dumps(c["args"]),
                "id": c["id"],
                "index": i,
            }
            for i, c in enumerate(message.tool_calls)
        ]
        content = message.content
        if isinstance(content, list):
            content = [{**block, "index": i} for i, block in enumerate(content)]
        yield ChatGenerationChunk(
            message=AIMessageChunk(content=content, tool_call_chunks=chunks)
        )


def _plain(content: Any) -> str:
    """Return message content as text (a string or text content blocks)."""
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))
