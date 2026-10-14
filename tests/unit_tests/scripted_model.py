"""A stand-in chat model for tests: replays scripted structured answers in order."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


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
