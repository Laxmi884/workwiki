"""Which failures are worth trying again: model calls and the ingest graph's `stage` node.

A retry helps only when the failure is the network's or the provider's and may pass:
a dropped connection, a timeout, a rate limit (429), an overloaded or failing server
(5xx). It never helps with a bad key (401), no credits (402), a bad request (400, 404,
422) or our own checks and bugs (`ValueError`, `KeyError`, ...): those fail again after
the wait. Failing fast matters twice over here: the model middleware then moves on to
the fallback models at once, and a `stage` retry reruns the whole agent.

    is_transient(TooManyRequestsResponseError(...))          # True: 429
    is_transient(PaymentRequiredResponseError(...))          # False: 402, out of credits
    is_transient(ValueError("OpenRouter API returned an error: ... (code: 503)"))  # True

LangGraph's own default (`RetryPolicy().retry_on`) retries every error type it doesn't
know, so 401/402 too, and skips every `OSError`, so Python's `TimeoutError` too.
"""

from __future__ import annotations

import re

import httpx
from langchain_core.exceptions import ModelError

# langchain-openrouter turns errors inside a response body into ValueErrors:
# "OpenRouter API returned an error[ during streaming]: <message> (code: <status>)"
_BODY_ERROR = re.compile(
    r"^OpenRouter API returned an error\b.*\(code: (\d{3})\)\s*$", re.S
)
_PASSING = re.compile(
    r"^OpenRouter API returned a response with no choices"
    r"|overloaded|rate[ -]?limit",
    re.I,
)
# Connection-level errors of SDKs we don't import (anthropic, openai): no status code.
_CONNECTION_ERRORS = {"APIConnectionError", "APITimeoutError", "NoResponseError"}


def transient_status(status: int) -> bool:
    """Return whether an HTTP status says "try again later"."""
    return status in (408, 429) or 500 <= status < 600


def _status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)  # openrouter, anthropic, ollama errors
    if status is None and isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
    return status if isinstance(status, int) and status > 0 else None


def _transient(exc: BaseException) -> bool | None:
    """Return the verdict for `exc` itself, or None when it says nothing either way."""
    if isinstance(exc, ConnectionError | TimeoutError | httpx.TransportError):
        return True
    if isinstance(exc, ModelError):
        return exc.is_retryable
    if (status := _status(exc)) is not None:
        return transient_status(status)
    if type(exc).__name__ in _CONNECTION_ERRORS:
        return True
    if isinstance(exc, ValueError):
        message = str(exc)
        if match := _BODY_ERROR.match(message):
            return transient_status(int(match[1]))
        if _PASSING.search(message):
            return True
    return None


def is_transient(exc: BaseException) -> bool:
    """Return whether `exc` (or an error it wraps) may pass if the call is tried again."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if (verdict := _transient(current)) is not None:
            return verdict
        current = current.__cause__ or current.__context__
    return False
