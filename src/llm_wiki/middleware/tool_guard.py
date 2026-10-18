"""Each agent's tools are a list in code: the model sees only those, and calls to others are refused.

Writes to the vault are already blocked without this: no write tools are registered, the
backends are read only, edit paths are typed and every change is reviewed by digest. What
was left to prompts was the rest of the tool surface. Deep Agents always registers `task`
(start a subagent), and the ingest agent was only *told* not to call it; a subagent it
started gets all of ingest's tools, and its calls stage edits that never show in the
agent's stats or refusals. Framework upgrades add and remove tools too: `write_todos` was
a default until Deep Agents 0.7. An allow-list turns both into code: a tool nobody listed
is hidden and refused.

A refused call answers the model with an error `ToolMessage`, so it can carry on. With
`refusals_key`, it is also recorded in that state list, so ingest's review counts it with
the other refused calls.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command


def tool_name(tool: Any) -> str | None:
    """Return a tool's name: a `BaseTool`'s `.name`, or a provider dict's name."""
    if isinstance(tool, dict):
        return tool.get("name") or tool.get("function", {}).get("name")
    return getattr(tool, "name", None)


class ToolGuardMiddleware(AgentMiddleware[Any, Any, Any]):
    """Show the model only `allowed` tools; refuse calls to any other name.

    State: with `refusals_key`, each refusal is appended to that key, so the agent's
    state must have it as a list with an add reducer (ingest's `IngestAgentState.refusals`).
    The key belongs to the agent, not this middleware, so there is no `state_schema` here.
    """

    name = "ToolGuard"

    def __init__(self, allowed: Iterable[str], refusals_key: str | None = None) -> None:
        """Allow exactly `allowed`; record refusals in `refusals_key` if given."""
        super().__init__()
        self.allowed = frozenset(allowed)
        self.refusals_key = refusals_key

    def _visible(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        kept = [t for t in request.tools if tool_name(t) in self.allowed]
        return (
            request if len(kept) == len(request.tools) else request.override(tools=kept)
        )

    def _refusal(self, request: ToolCallRequest) -> ToolMessage | Command[Any] | None:
        name = request.tool_call["name"]
        if name in self.allowed:
            return None
        reason = f"{name} is not one of your tools; use only: {', '.join(sorted(self.allowed))}"
        message = ToolMessage(
            content=f"REFUSED: {reason}",
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )
        if self.refusals_key is None:
            return message
        return Command(
            update={"messages": [message], self.refusals_key: [f"{name}: not allowed"]}
        )

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """Call the model with only the allowed tools bound."""
        return handler(self._visible(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Async twin of `wrap_model_call` (the server runs agents async)."""
        return await handler(self._visible(request))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Run an allowed call; answer any other with a refusal (unknown names too)."""
        refusal = self._refusal(request)
        return refusal if refusal is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Async twin of `wrap_tool_call`."""
        refusal = self._refusal(request)
        return refusal if refusal is not None else await handler(request)
