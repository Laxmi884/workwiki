"""A stand-in chat model for tests: replays scripted structured answers in order."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from llm_wiki.domain.facts import NameScan


class ScriptedModel:
    """Supports `with_structured_output(schema, include_raw=True).invoke(messages)`.

    Each scripted answer is a model instance (parsed) or an Exception (a parsing
    error). Every prompt is kept in `calls` so tests can check what was sent.
    """

    def __init__(self, *answers: BaseModel | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[list[Any]] = []

    def with_structured_output(self, schema: type, *, include_raw: bool = False):
        assert include_raw, "extraction relies on the include_raw envelope"
        return self

    def invoke(self, messages: list[Any]) -> dict[str, Any]:
        self.calls.append(list(messages))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            return {"raw": None, "parsed": None, "parsing_error": answer}
        return {"raw": None, "parsed": answer, "parsing_error": None}


class BySection:
    """Answers by section heading (parallel extracts arrive in any order).

    `answers` maps a heading ("Meetings") to its facts; other sections get `default`.
    The name scan (`NameScan` schema) gets `names` and is recorded in `name_calls`, so
    `calls` stays the extraction calls only.
    """

    def __init__(
        self,
        answers: dict[str, BaseModel],
        default: BaseModel,
        names: NameScan | None = None,
    ) -> None:
        self.answers = answers
        self.default = default
        self.names = names or NameScan()
        self.calls: list[list[Any]] = []
        self.name_calls: list[list[Any]] = []

    def with_structured_output(self, schema: type, *, include_raw: bool = False):
        assert include_raw, "extraction relies on the include_raw envelope"
        if schema is NameScan:
            return _NameScanner(self)
        return self

    def invoke(self, messages: list[Any]) -> dict[str, Any]:
        self.calls.append(list(messages))
        heading = messages[-1].content.split("\n", 1)[0].removeprefix("Section: ")
        answer = self.answers.get(heading, self.default)
        return {"raw": None, "parsed": answer, "parsing_error": None}


class _NameScanner:
    def __init__(self, owner: BySection) -> None:
        self.owner = owner

    def invoke(self, messages: list[Any]) -> dict[str, Any]:
        self.owner.name_calls.append(list(messages))
        return {"raw": None, "parsed": self.owner.names, "parsing_error": None}


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
