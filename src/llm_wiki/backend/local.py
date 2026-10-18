"""`LocalBackend`: the agents in this process, threads in our own SQLite (0005, 0010).

No server and no account: ingest runs through `IngestRunner` (its checkpointer keeps
review threads across restarts), and `ask` builds the assistant for one answer.

    backend = LocalBackend.sqlite(Path("~/WorkDev"))
    result = backend.start_ingest("00_raw/daily/2026-10-12.md")     # status "review"
    backend.answer(result.thread_id, "approve")
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from llm_wiki.assistant.agent import build_assistant
from llm_wiki.backend.base import (
    ChatTurn,
    Finished,
    NoOpenReview,
    NoteNotFound,
    ReviewBusy,
    RunResult,
    StreamEvent,
    UnknownThread,
    chat_messages,
)
from llm_wiki.config import get_settings
from llm_wiki.domain.review import ReviewAnswer
from llm_wiki.model_setup import configured_fallbacks, configured_model
from llm_wiki.progress import answer_plan, follow, message_text, settle, step_plan
from llm_wiki.runner import IngestRunner


def _context(vault: Path, model: str | None) -> dict[str, Any]:
    return {"vault_path": str(vault)} | ({"model": model} if model else {})


def _resolve(vault: Path | str | None) -> Path:
    return Path(vault).expanduser().resolve() if vault else get_settings().vault_path


class LocalBackend:
    """`Backend` on `IngestRunner` and an in-process assistant."""

    def __init__(self, runner: IngestRunner, vault: Path, model: str | None = None):
        """Use `runner` for ingest threads; `model` overrides `WIKI_MODEL` for both agents.

        The runner's context must name the same vault (the classmethods do this).
        """
        self._runner = runner
        self._vault = vault
        self._model = model
        self._answering: set[str] = set()  # threads with an answer running
        self._guard = threading.Lock()

    @classmethod
    def sqlite(
        cls,
        vault: Path | str | None = None,
        model: str | None = None,
        path: Path | None = None,
    ) -> LocalBackend:
        """Return a backend whose threads survive restarts.

        `vault` defaults to `WIKI_VAULT_PATH`; `path` to `WIKI_STATE_DIR/checkpoints.sqlite`
        (one file for every vault: each backend sees only its own vault's threads).
        """
        resolved = _resolve(vault)
        runner = IngestRunner.sqlite(path, context=_context(resolved, model))  # type: ignore[arg-type]
        return cls(runner, resolved, model)

    @classmethod
    def in_memory(
        cls, vault: Path | str | None = None, model: str | None = None
    ) -> LocalBackend:
        """Return a backend whose threads live only in this process (tests)."""
        resolved = _resolve(vault)
        runner = IngestRunner.in_memory(context=_context(resolved, model))  # type: ignore[arg-type]
        return cls(runner, resolved, model)

    @property
    def vault(self) -> Path:
        """The vault this backend reads and writes."""
        return self._vault

    def start_ingest(self, note_path: str) -> RunResult:
        """Ingest `note_path` until its review pauses (or it ends early)."""
        try:
            return self._runner.start(note_path)
        except FileNotFoundError as exc:  # raised by the graph's load_note
            raise NoteNotFound(f"no note at {note_path} in {self._vault}") from exc

    def pending(self) -> list[RunResult]:
        """Return this vault's open reviews, newest first."""
        open_reviews = []
        for thread in self._threads_newest_first():
            if self._runner.vault_of(thread) != self._vault:
                continue
            result = self._runner.status(thread)
            if result.status == "review":
                open_reviews.append(result)
        return open_reviews

    def _threads_newest_first(self) -> list[str]:
        """Ingest thread ids by their latest checkpoint, newest first."""
        latest: dict[str, str] = {}
        checkpointer = self._runner.graph.checkpointer
        for item in checkpointer.list(None):  # type: ignore[union-attr]
            thread = item.config["configurable"]["thread_id"]
            if thread.startswith("ingest-"):
                latest[thread] = max(latest.get(thread, ""), item.checkpoint["ts"])
        return sorted(latest, key=latest.__getitem__, reverse=True)

    def review(self, thread_id: str) -> RunResult:
        """Return where `thread_id` stands (another vault's thread counts as unknown)."""
        if self._runner.vault_of(thread_id) != self._vault:
            raise UnknownThread(f"no ingest thread {thread_id!r} for {self._vault}")
        return self._runner.status(thread_id)

    @contextmanager
    def _one_answer(self, thread_id: str) -> Iterator[None]:
        """Hold `thread_id` while an answer runs; a second one meanwhile is refused.

        The review page and the chat share this backend: without it, two answers
        could both see the review open and resume it twice. One process only (a CLI
        run beside the server isn't covered).
        """
        with self._guard:
            if thread_id in self._answering:
                raise ReviewBusy(f"review {thread_id!r} is already being answered")
            self._answering.add(thread_id)
        try:
            yield
        finally:
            with self._guard:
                self._answering.discard(thread_id)

    def answer(
        self, thread_id: str, answer: ReviewAnswer | dict[str, Any] | str
    ) -> RunResult:
        """Send the user's answer and run on until the next pause or the end."""
        with self._one_answer(thread_id):
            if self.review(thread_id).status != "review":
                raise NoOpenReview(f"no review is waiting on thread {thread_id!r}")
            return self._runner.resume(thread_id, answer)

    def ask(self, question: str, history: Sequence[ChatTurn] = ()) -> str:
        """Return the assistant's answer, given the earlier chat."""
        agent = build_assistant(
            self._vault, configured_model(self._model), fallbacks=configured_fallbacks()
        )
        result = agent.invoke({"messages": chat_messages(question, history)})
        return message_text(result["messages"][-1].content)

    def stream_ingest(self, note_path: str) -> Iterator[StreamEvent]:
        """Ingest `note_path`, yielding progress, until its review pauses."""
        thread_id = self._runner.new_thread_id()
        progress = step_plan("ingest")
        yield progress
        try:
            progress = yield from follow(
                progress, self._runner.stream_start(note_path, thread_id)
            )
        except FileNotFoundError as exc:  # raised by the graph's load_note
            raise NoteNotFound(f"no note at {note_path} in {self._vault}") from exc
        result = self._runner.status(thread_id)
        yield Finished(progress=settle(progress, result.status), result=result)

    def stream_answer(
        self, thread_id: str, answer: ReviewAnswer | dict[str, Any] | str
    ) -> Iterator[StreamEvent]:
        """Send the user's answer, yielding progress, until the next pause or the end."""
        with self._one_answer(thread_id):  # held until the stream ends or is closed
            if self.review(thread_id).status != "review":
                raise NoOpenReview(f"no review is waiting on thread {thread_id!r}")
            progress = answer_plan(answer)
            yield progress
            progress = yield from follow(
                progress, self._runner.stream_resume(thread_id, answer)
            )
            result = self._runner.status(thread_id)
            yield Finished(progress=settle(progress, result.status), result=result)

    def stream_ask(
        self, question: str, history: Sequence[ChatTurn] = ()
    ) -> Iterator[StreamEvent]:
        """Answer, yielding progress whose `text` is the answer so far."""
        agent = build_assistant(
            self._vault, configured_model(self._model), fallbacks=configured_fallbacks()
        )
        progress = step_plan("ask")
        yield progress
        parts = agent.stream(
            {"messages": chat_messages(question, history)},
            stream_mode=["updates", "messages"],
            subgraphs=True,
            version="v2",
        )
        progress = yield from follow(progress, parts)
        yield Finished(progress=settle(progress, "done"), answer=progress.text)
