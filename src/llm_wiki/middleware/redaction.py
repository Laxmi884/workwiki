"""Secrets never reach the model: every request is cleaned on its way out.

Daily notes are scratchpads: a token pasted while setting up CI, a password, a database
URL. Agents send what they read to the model provider, so `RedactionMiddleware` replaces
those values with `[REDACTED_SECRET]` in the request itself (`wrap_model_call`): the
system message (rules, skills) and every message (ingest's brief carries the whole note;
the user's question; tool results). Whatever the state holds, the model gets cleaned text.
The thread keeps the user's own text as written; it stays on this machine. The model
never sees a value, so it can't copy one into a wiki page.

LangChain's `PIIMiddleware` (rewrite the state in `before_model`) was the first try, and
inside a deep agent it leaks: Deep Agents' messages reducer appends a rewritten message
that has no ID instead of replacing the original, so the model got both copies. A request
filter doesn't depend on message IDs.

Every agent runs this middleware, the assistant's general-purpose subagent too (it is our
own spec for that reason). What is caught is credential-shaped text and the values of
this process's own keys (`secret_values`); a password written as a plain word is not.
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any, NamedTuple

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

REDACTED = "[REDACTED_SECRET]"


class Match(NamedTuple):
    """One secret found in a text: `text[start:end] == value`."""

    start: int
    end: int
    value: str


# Each pattern's group 1, if any, is the secret; otherwise the whole match is.
PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
        re.S,
    ),
    re.compile(
        r"\bsk-(?:ant-|or-v1-|proj-)?[A-Za-z0-9_-]{20,}"
    ),  # Anthropic, OpenRouter, OpenAI
    re.compile(
        r"\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{22,}"
    ),  # GitHub
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),  # GitLab
    re.compile(
        r"\bAKCp[A-Za-z0-9]{50,}|\bcmVmdGtu[A-Za-z0-9]{40,}"
    ),  # JFrog key, reference token
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),  # Google
    re.compile(r"\blsv2_(?:pt|sk)_[A-Za-z0-9_]{20,}"),  # LangSmith
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    ),  # JWT
    re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/-]{16,}=*)"),
    re.compile(r"://[^/\s:@]+:([^/\s@]+)@"),  # password in a URL
    # key = value, when the value looks generated: 8+ characters with a digit, no spaces
    re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\b"
        r"\s*[:=]\s*['\"]?((?=[^\s'\"]*\d)[^\s'\"]{8,})"
    ),
)

_KEY_NAMES = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)$")


def secret_values(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return this process's own secrets: values of `*_KEY`/`*_TOKEN`/... variables.

    Only values of 8+ characters, so a short setting can't blank out ordinary words.
    """
    env = os.environ if environ is None else environ
    return tuple(
        v for k, v in env.items() if _KEY_NAMES.search(k.upper()) and len(v) >= 8
    )


def find_secrets(text: str, values: Iterable[str] = ()) -> list[Match]:
    """Return the secrets in `text` as non-overlapping matches, in order."""
    spans: list[tuple[int, int]] = []
    for pattern in PATTERNS:
        for m in pattern.finditer(text):
            group = 1 if m.re.groups and m.group(1) is not None else 0
            spans.append(m.span(group))
    for value in values:
        start = text.find(value)
        while start != -1:
            spans.append((start, start + len(value)))
            start = text.find(value, start + 1)
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [Match(s, e, text[s:e]) for s, e in merged]


def redact(text: str, values: Iterable[str] = ()) -> str:
    """Return `text` with every secret replaced by `[REDACTED_SECRET]`."""
    out, last = [], 0
    for m in find_secrets(text, values):
        out += [text[last : m.start], REDACTED]
        last = m.end
    return "".join([*out, text[last:]])


def redact_content(content: Any, values: Iterable[str] = ()) -> Any:
    """Return message content with secrets redacted: a string, or each text block's text.

    Returns `content` itself when nothing changed, so callers can tell by identity.
    """
    if isinstance(content, str):
        cleaned = redact(content, values)
        return content if cleaned == content else cleaned
    if not isinstance(content, list):
        return content
    blocks, changed = [], False
    for block in content:
        text = (
            block
            if isinstance(block, str)
            else block.get("text")
            if isinstance(block, dict)
            else None
        )
        if isinstance(text, str) and (cleaned := redact(text, values)) != text:
            block = cleaned if isinstance(block, str) else {**block, "text": cleaned}
            changed = True
        blocks.append(block)
    return blocks if changed else content


class RedactionMiddleware(AgentMiddleware[Any, Any, Any]):
    """Redact secrets in the system message and every message of each model request."""

    name = "Redaction"

    def __init__(self, values: Iterable[str] = ()) -> None:
        """Catch credential-shaped text plus these exact `values` (see `secret_values`)."""
        super().__init__()
        self._values = tuple(values)

    def _clean(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        changes: dict[str, Any] = {}
        messages = []
        for m in request.messages:
            content = redact_content(m.content, self._values)
            messages.append(
                m if content is m.content else m.model_copy(update={"content": content})
            )
        if any(a is not b for a, b in zip(messages, request.messages, strict=True)):
            changes["messages"] = messages
        system = request.system_message
        if system is not None:
            content = redact_content(system.content, self._values)
            if content is not system.content:
                changes["system_message"] = system.model_copy(
                    update={"content": content}
                )
        return request.override(**changes) if changes else request

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """Call the model with every secret in the request redacted."""
        return handler(self._clean(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Async twin of `wrap_model_call` (the server runs agents async)."""
        return await handler(self._clean(request))
