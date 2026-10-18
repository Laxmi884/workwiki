"""Tell the model today's date. "Last week" or "this quarter" are otherwise guesses.

A model's sense of "now" is its training data, so without this a question like "what did
I do last week?" gets a window in the wrong year. Code works out the weekday and the
quarter too: they are arithmetic a model gets wrong, and the vault's logs are quarterly.

The date is the machine's local date, because daily notes are named by the user's day; a
container on UTC needs `TZ` set. It is read on every model call, not baked into the
prompt: the server builds the graph once and a chat can run past midnight. It goes into
the system message, which isn't saved in the thread, so a thread resumed tomorrow gets
tomorrow's date instead of a stale one in its history.

Only the assistant uses it. The ingest agent's "today" is the note's date, already in
its brief; the wall-clock date would be wrong for an old note.
"""

from __future__ import annotations

import datetime
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def date_line(day: datetime.date) -> str:
    """Return the line the model sees, e.g. "Today is Saturday, 2026-10-17 (2026 Q4). ..."."""
    quarter = (day.month - 1) // 3 + 1
    return (
        f"Today is {WEEKDAYS[day.weekday()]}, {day.isoformat()} ({day.year} Q{quarter}). "
        'Resolve relative dates ("yesterday", "last week", "this quarter") from it.'
    )


def with_line(system: SystemMessage | None, line: str) -> SystemMessage:
    """Return `system` with `line` appended as a text block (`system` is unchanged).

    The LangChain docs' way: `content_blocks` reads string or list content as blocks,
    and a new block keeps the existing ones as they are (`cache_control` included), so
    the text before the line is unchanged and a provider's prompt cache still matches.
    """
    block = {"type": "text", "text": line}
    blocks: list[Any] = [*system.content_blocks] if system is not None else []
    return SystemMessage(content=[*blocks, block])


class CurrentDateMiddleware(AgentMiddleware[Any, Any, Any]):
    """Append today's date to the system message on every model call."""

    name = "CurrentDate"

    def __init__(
        self, today: Callable[[], datetime.date] = datetime.date.today
    ) -> None:
        """Read the date from `today` (local time unless a test passes a clock)."""
        super().__init__()
        self._today = today

    def _stamp(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        line = date_line(self._today())
        return request.override(system_message=with_line(request.system_message, line))

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """Call the model with the date appended to its system message."""
        return handler(self._stamp(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Async twin of `wrap_model_call` (the server runs agents async)."""
        return await handler(self._stamp(request))
