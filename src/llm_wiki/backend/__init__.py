"""How surfaces reach the agents: one `Backend` interface, local or server (0010).

Surfaces call `get_backend()` and never import a backend class, the runner or
`langgraph_sdk` themselves (0010 rule 3).
"""

from __future__ import annotations

from pathlib import Path

from llm_wiki.backend.base import (
    Backend,
    BackendError,
    ChatTurn,
    Finished,
    NoOpenReview,
    NoteNotFound,
    ReviewBusy,
    RunResult,
    StreamEvent,
    UnknownThread,
)

__all__ = [
    "Backend",
    "BackendError",
    "ChatTurn",
    "Finished",
    "NoOpenReview",
    "NoteNotFound",
    "ReviewBusy",
    "RunResult",
    "StreamEvent",
    "UnknownThread",
    "get_backend",
]


def get_backend(vault: Path | str | None = None, model: str | None = None) -> Backend:
    """Return the backend `WIKI_BACKEND` names, for `vault` (default `WIKI_VAULT_PATH`).

    `model` overrides `WIKI_MODEL` for both agents.
    """
    from llm_wiki.config import get_settings  # noqa: PLC0415  (imports stay light)

    settings = get_settings()
    if settings.backend == "server":
        from llm_wiki.backend.server import ServerBackend  # noqa: PLC0415

        return ServerBackend(settings.server_url, vault, model)
    from llm_wiki.backend.local import LocalBackend  # noqa: PLC0415

    return LocalBackend.sqlite(vault, model)
