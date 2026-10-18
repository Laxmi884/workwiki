"""`ServerBackend`: the agents on an Agent Server, reached over HTTP (decision 0010).

The same graphs as `LocalBackend`, but the server owns the threads, checkpoints and
runs (`langgraph dev` now; `langgraph up` / build later). Ingest threads carry their
vault in metadata, so `pending` and the vault check are server-side searches.

    backend = ServerBackend("http://127.0.0.1:2024", Path("~/WorkDev"))
    result = backend.start_ingest("00_raw/daily/2026-10-12.md")     # status "review"
    backend.answer(result.thread_id, "approve")

What the server does differently (learned against `langgraph dev`, 2026-10-16):
a failed run doesn't raise but returns `{"__error__": {"error": <exception class>,
...}}`; a thread id that is not a UUID is a 422, an unknown UUID a 404.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn, TypeVar

import httpx
from langgraph_sdk import get_sync_client
from langgraph_sdk.client import SyncLangGraphClient
from langgraph_sdk.errors import NotFoundError, UnprocessableEntityError
from langgraph_sdk.schema import StreamMode

from llm_wiki.backend.base import (
    BackendError,
    ChatTurn,
    Finished,
    NoOpenReview,
    NoteNotFound,
    RunResult,
    StreamEvent,
    UnknownThread,
    chat_messages,
    run_result,
)
from llm_wiki.config import get_settings
from llm_wiki.domain.review import ReviewAnswer
from llm_wiki.progress import answer_plan, follow, message_text, settle, step_plan

INGEST = "ingest"  # graph ids in langgraph.json
ASSISTANT = "assistant"
MAX_PENDING = 50
INGEST_MODES: list[StreamMode] = ["updates", "custom"]
# messages-tuple parts arrive as type "messages", data [message, metadata]
ASK_MODES: list[StreamMode] = ["updates", "messages-tuple"]


F = TypeVar("F", bound=Callable[..., Any])


def _reachable(method: F) -> F:
    """Turn HTTP failures that escape `method` (server down, a 5xx) into `BackendError`.

    Surfaces catch `BackendError` only; they never import httpx or langgraph_sdk.
    """

    @functools.wraps(method)
    def wrapper(self: ServerBackend, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except httpx.HTTPError as exc:
            raise BackendError(
                f"the Agent Server at {self.url} failed: {type(exc).__name__}"
            ) from exc

    return wrapper  # type: ignore[return-value]


def _reachable_stream(method: F) -> F:
    """`_reachable` for generators.

    A generator's body runs after the call has returned, so HTTP failures surface
    while iterating; they are converted there.
    """

    @functools.wraps(method)
    def wrapper(self: ServerBackend, *args: Any, **kwargs: Any) -> Any:
        try:
            yield from method(self, *args, **kwargs)
        except httpx.HTTPError as exc:
            raise BackendError(
                f"the Agent Server at {self.url} failed: {type(exc).__name__}"
            ) from exc

    return wrapper  # type: ignore[return-value]


def _errors_raised(
    parts: Iterator[Any], fail: Callable[[Mapping[str, Any]], NoReturn]
) -> Iterator[Any]:
    """Pass parts through; on the server's "error" part call `fail` (which raises)."""
    for part in parts:
        if part.get("type") == "error":
            fail(part.get("data") or {})
        yield part


def _ingest_error(error: Mapping[str, Any]) -> NoReturn:
    raise BackendError(f"ingest failed on the server: {error.get('error')}")


def _assistant_error(error: Mapping[str, Any]) -> NoReturn:
    raise BackendError(f"the assistant failed on the server: {error.get('error')}")


def _failure(output: Any) -> Mapping[str, Any] | None:
    """Return the run's error, if `runs.wait` reported one instead of the state."""
    if isinstance(output, Mapping):
        return output.get("__error__")
    return None


class ServerBackend:
    """`Backend` on an Agent Server through `langgraph-sdk`."""

    def __init__(
        self,
        url: str | None = None,
        vault: Path | str | None = None,
        model: str | None = None,
        client: SyncLangGraphClient | None = None,
    ):
        """Talk to the server at `url` (default `WIKI_SERVER_URL`) about `vault`.

        `vault` defaults to `WIKI_VAULT_PATH`; it must be the path the server sees
        (the same on this machine; a mounted volume under `langgraph up`).
        """
        self.url = url or get_settings().server_url
        self._client = client or get_sync_client(url=self.url)
        self._vault = (
            Path(vault).expanduser().resolve() if vault else get_settings().vault_path
        )
        self._model = model

    @property
    def vault(self) -> Path:
        """The vault this backend reads and writes."""
        return self._vault

    def _context(self) -> dict[str, Any]:
        model = {"model": self._model} if self._model else {}
        return {"vault_path": str(self._vault)} | model

    def _metadata(self) -> dict[str, Any]:
        return {"vault_path": str(self._vault), "graph_id": INGEST}

    @_reachable
    def start_ingest(self, note_path: str) -> RunResult:
        """Ingest `note_path` in a new server thread until its review pauses."""
        thread_id = self._new_thread()
        output = self._client.runs.wait(
            thread_id, INGEST, input={"note_path": note_path}, context=self._context()
        )
        if (error := _failure(output)) is not None:
            self._ingest_failed(error, note_path, thread_id)
        return self._status(thread_id)

    def _new_thread(self) -> str:
        return self._client.threads.create(
            metadata={"vault_path": str(self._vault)}, graph_id=INGEST
        )["thread_id"]

    def _ingest_failed(
        self, error: Mapping[str, Any], note_path: str, thread_id: str
    ) -> NoReturn:
        """Raise for a failed ingest run (a missing note deletes its new thread)."""
        if error.get("error") == "FileNotFoundError":  # from load_note
            self._client.threads.delete(thread_id)
            raise NoteNotFound(f"no note at {note_path} in {self._vault}")
        _ingest_error(error)

    @_reachable
    def pending(self) -> list[RunResult]:
        """Return this vault's open reviews, newest first."""
        threads = self._client.threads.search(
            metadata=self._metadata(),
            status="interrupted",
            sort_by="updated_at",
            sort_order="desc",
            limit=MAX_PENDING,
        )
        return [self._status(t["thread_id"]) for t in threads]

    @_reachable
    def review(self, thread_id: str) -> RunResult:
        """Return where `thread_id` stands (another vault's thread counts as unknown)."""
        try:
            thread = self._client.threads.get(thread_id)
        except (NotFoundError, UnprocessableEntityError) as exc:
            raise UnknownThread(f"no ingest thread {thread_id!r}") from exc
        if (thread.get("metadata") or {}).get("vault_path") != str(self._vault):
            raise UnknownThread(f"no ingest thread {thread_id!r} for {self._vault}")
        return self._status(thread_id)

    def _status(self, thread_id: str) -> RunResult:
        state = self._client.threads.get_state(thread_id)
        interrupts = state.get("interrupts") or []
        interrupt = interrupts[0]["value"] if interrupts else None
        values = state.get("values")
        return run_result(
            thread_id, values if isinstance(values, Mapping) else {}, interrupt
        )

    @_reachable
    def answer(
        self, thread_id: str, answer: ReviewAnswer | dict[str, Any] | str
    ) -> RunResult:
        """Resume the thread with the user's answer and wait for the next pause."""
        if self.review(thread_id).status != "review":
            raise NoOpenReview(f"no review is waiting on thread {thread_id!r}")
        if isinstance(answer, ReviewAnswer):
            answer = answer.model_dump(mode="json")
        output = self._client.runs.wait(
            thread_id,
            INGEST,
            command={"resume": answer},
            context=self._context(),
        )
        if (error := _failure(output)) is not None:
            _ingest_error(error)
        return self._status(thread_id)

    @_reachable
    def ask(self, question: str, history: Sequence[ChatTurn] = ()) -> str:
        """Return the assistant's answer from a run without a thread (nothing kept)."""
        output = self._client.runs.wait(
            None,
            ASSISTANT,
            input={"messages": chat_messages(question, history)},
            context=self._context(),
        )
        if (error := _failure(output)) is not None:
            _assistant_error(error)
        assert isinstance(output, Mapping)
        return message_text(output["messages"][-1]["content"])

    @_reachable_stream
    def stream_ingest(self, note_path: str) -> Iterator[StreamEvent]:
        """Ingest `note_path` in a new server thread, yielding progress."""
        thread_id = self._new_thread()
        progress = step_plan("ingest")
        yield progress
        parts = self._client.runs.stream(
            thread_id,
            INGEST,
            input={"note_path": note_path},
            context=self._context(),
            stream_mode=INGEST_MODES,
            stream_subgraphs=True,
            version="v2",
            on_disconnect="continue",  # the default; see base.py on stopping early
        )
        progress = yield from follow(
            progress,
            _errors_raised(
                parts, lambda error: self._ingest_failed(error, note_path, thread_id)
            ),
        )
        result = self._status(thread_id)
        yield Finished(progress=settle(progress, result.status), result=result)

    @_reachable_stream
    def stream_answer(
        self, thread_id: str, answer: ReviewAnswer | dict[str, Any] | str
    ) -> Iterator[StreamEvent]:
        """Resume the thread with the user's answer, yielding progress."""
        if self.review(thread_id).status != "review":
            raise NoOpenReview(f"no review is waiting on thread {thread_id!r}")
        if isinstance(answer, ReviewAnswer):
            answer = answer.model_dump(mode="json")
        progress = answer_plan(answer)
        yield progress
        parts = self._client.runs.stream(
            thread_id,
            INGEST,
            command={"resume": answer},
            context=self._context(),
            stream_mode=INGEST_MODES,
            stream_subgraphs=True,
            version="v2",
            on_disconnect="continue",  # the default; see base.py on stopping early
        )
        progress = yield from follow(progress, _errors_raised(parts, _ingest_error))
        result = self._status(thread_id)
        yield Finished(progress=settle(progress, result.status), result=result)

    @_reachable_stream
    def stream_ask(
        self, question: str, history: Sequence[ChatTurn] = ()
    ) -> Iterator[StreamEvent]:
        """Answer from a threadless run, yielding progress with the text so far."""
        progress = step_plan("ask")
        yield progress
        parts = self._client.runs.stream(
            None,
            ASSISTANT,
            input={"messages": chat_messages(question, history)},
            context=self._context(),
            stream_mode=ASK_MODES,
            stream_subgraphs=True,
            version="v2",
            on_disconnect="continue",  # the default; see base.py on stopping early
        )
        progress = yield from follow(progress, _errors_raised(parts, _assistant_error))
        yield Finished(progress=settle(progress, "done"), answer=progress.text)
