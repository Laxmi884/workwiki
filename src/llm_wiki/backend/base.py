"""The one interface every surface uses to reach the agents (decision 0010).

    review page · chat box · Telegram · CLI      → only call Backend
    Backend: start_ingest · pending · review · answer · ask
             (+ stream_ingest · stream_answer · stream_ask)
      LocalBackend  (IngestRunner + SQLite; no server)
      ServerBackend (langgraph-sdk over HTTP)

A backend works on one vault: it starts ingests there, lists only that vault's open
reviews, and treats another vault's threads as unknown. Blocking calls return when
the run pauses or ends; `stream_*` calls yield `Progress` on the way
(`llm_wiki.progress`) and end with one `Finished`.

Stopping a `stream_*` iteration early (a surface gone) differs by backend: the server
finishes the run anyway (`on_disconnect="continue"`), so its review still opens;
locally the run stops after the current step and the thread is left unfinished.
Surfaces should drain the stream.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from llm_wiki.domain.review import ReviewAnswer, ReviewRequest
from llm_wiki.progress import Progress

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


class Finished(BaseModel):
    """The last event of a `stream_*` call: the final progress and the call's value.

    Attributes:
        progress: Settled: no step is left running.
        result: For `stream_ingest` / `stream_answer`: what the blocking call returns.
        answer: For `stream_ask`: the assistant's answer.
    """

    model_config = ConfigDict(frozen=True)

    progress: Progress
    result: RunResult | None = None
    answer: str | None = None


StreamEvent = Progress | Finished


def chat_messages(question: str, history: Sequence[ChatTurn]) -> list[Any]:
    """Return the chat as model messages: the earlier turns, then the question."""
    return [
        *({"role": r, "content": text} for r, text in history),
        {"role": "user", "content": question},
    ]


def run_result(
    thread_id: str, values: Mapping[str, Any], interrupt: Any | None
) -> RunResult:
    """Read a RunResult from an ingest thread's state values and its open interrupt.

    Both backends use it: the runner's checkpointer and the server's thread state
    hold the same keys.
    """
    warnings = tuple(values.get("warnings", []))
    if interrupt is not None:
        request = ReviewRequest.model_validate(interrupt)
        return RunResult(
            thread_id=thread_id, status="review", review=request, warnings=warnings
        )
    if values.get("skip_reason"):
        return RunResult(thread_id=thread_id, status="skipped", warnings=warnings)
    return RunResult(
        thread_id=thread_id,
        status="done",
        action=values.get("review_action"),
        approved=tuple(values.get("approved", [])),
        written=tuple(values.get("written", [])),
        commit=values.get("commit"),
        warnings=warnings,
    )


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

    def stream_ingest(self, note_path: str) -> Iterator[StreamEvent]:
        """Like `start_ingest`, yielding `Progress` as steps run, then one `Finished`.

        Raises (while iterating):
            NoteNotFound: No note at `note_path` in this vault.
        """
        ...

    def stream_answer(
        self, thread_id: str, answer: ReviewAnswer | dict[str, Any] | str
    ) -> Iterator[StreamEvent]:
        """Like `answer`, yielding `Progress`, then one `Finished`.

        Raises (while iterating):
            UnknownThread: No such thread on this vault.
            NoOpenReview: Nothing is waiting for an answer on it.
        """
        ...

    def stream_ask(
        self, question: str, history: Sequence[ChatTurn] = ()
    ) -> Iterator[StreamEvent]:
        """Like `ask`, yielding `Progress`, then one `Finished` with `answer`.

        Each `Progress.text` is the answer so far.
        """
        ...
