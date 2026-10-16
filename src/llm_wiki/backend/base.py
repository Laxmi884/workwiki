"""The one interface every surface uses to reach the agents (decision 0010).

    review page · chat box · Telegram · CLI      → only call Backend
    Backend: start_ingest · pending · review · answer · ask
      LocalBackend  (IngestRunner + SQLite; no server)
      ServerBackend (langgraph-sdk over HTTP)

A backend works on one vault: it starts ingests there, lists only that vault's open
reviews, and treats another vault's threads as unknown. Calls block until the run
pauses or ends (no streaming yet; 0010 rule 4).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from llm_wiki.domain.review import ReviewAnswer, ReviewRequest

ChatTurn = tuple[Literal["user", "assistant"], str]


class RunResult(BaseModel):
    """Where an ingest thread stands after a backend call.

    Attributes:
        thread_id: Pass it to `review` and `answer`.
        status: "review" (waiting for an answer), "skipped" (nothing to do) or "done".
        review: What to show the user while status is "review".
        action: The accepted answer once done: "approve" or "reject".
        approved: Digests of approved diffs once done.
        written: Vault paths written by apply once done.
        commit: The vault commit holding them (None: not a git repo, or nothing new).
        warnings: Everything the run reported.
    """

    model_config = ConfigDict(frozen=True)

    thread_id: str
    status: Literal["review", "skipped", "done"]
    review: ReviewRequest | None = None
    action: str | None = None
    approved: tuple[str, ...] = ()
    written: tuple[str, ...] = ()
    commit: str | None = None
    warnings: tuple[str, ...] = ()


class BackendError(Exception):
    """A call the backend refused; the message is safe to show the user."""


class NoteNotFound(BackendError):
    """No note at that path in the backend's vault."""


class UnknownThread(BackendError):
    """No ingest thread with this id on this backend's vault."""


class NoOpenReview(BackendError):
    """The thread exists but nothing is waiting for an answer."""


class Backend(Protocol):
    """What surfaces may ask of the agents; both backends pass the same contract tests."""

    @property
    def vault(self) -> Path:
        """The vault this backend reads and writes (absolute)."""
        ...

    def start_ingest(self, note_path: str) -> RunResult:
        """Ingest a vault-relative note until its review pauses (or it ends early).

        Raises:
            NoteNotFound: No note at `note_path` in this vault.
        """
        ...

    def pending(self) -> list[RunResult]:
        """Return this vault's open reviews (status "review"), newest first."""
        ...

    def review(self, thread_id: str) -> RunResult:
        """Return where an ingest thread stands, without running anything.

        Raises:
            UnknownThread: No such thread on this vault.
        """
        ...

    def answer(
        self, thread_id: str, answer: ReviewAnswer | dict[str, Any] | str
    ) -> RunResult:
        """Send the user's answer and run on until the next pause or the end.

        An invalid answer comes back as a new review with `review.error` set.

        Raises:
            UnknownThread: No such thread on this vault.
            NoOpenReview: Nothing is waiting for an answer on it.
        """
        ...

    def ask(self, question: str, history: Sequence[ChatTurn] = ()) -> str:
        """Return the assistant's answer; `history` is the earlier chat, oldest first.

        Nothing is saved: the surface keeps the conversation.
        """
        ...
