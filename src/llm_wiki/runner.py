"""Run ingest without Studio or a server: start a note, get the review, answer it.

Surfaces reach it only through `LocalBackend` (decision 0010); none of them touches
the runner or the graph directly. The runner owns the checkpointer and the thread IDs:
`start` runs until the review pauses, `resume` sends the answer and runs on.

    runner = IngestRunner.sqlite(context={"vault_path": "~/WorkDev"})   # survives restarts
    result = runner.start("00_raw/daily/2026-10-12.md")   # status "review"
    result = runner.resume(result.thread_id, {"action": "approve"})
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph
from langgraph.types import Command, StreamMode

from llm_wiki.backend.base import RunResult, run_result
from llm_wiki.config import get_settings
from llm_wiki.domain.review import ReviewAnswer
from llm_wiki.ingest.graph import builder
from llm_wiki.ingest.state import IngestContext

STREAM_MODES: list[StreamMode] = ["updates", "custom"]  # what `llm_wiki.progress` reads


def checkpoint_serializer() -> JsonPlusSerializer:
    """Return a strict serializer: it restores only LangGraph's safe built-in types.

    Ingest state is stored as plain JSON (`ingest/state.py`, `STORED_AS_JSON`), so none
    of our classes needs restoring; a class that slips into state comes back as a plain
    dict and fails loudly in tests (`test_state_json.py`) instead of warning in prod.
    """
    return JsonPlusSerializer(allowed_msgpack_modules=None)


class IngestRunner:
    """The ingest graph compiled with a checkpointer, behind three plain calls."""

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver,
        context: IngestContext | None = None,
        graph_builder: StateGraph | None = None,
    ) -> None:
        """Compile the graph with `checkpointer`; `context` is passed to every run.

        `graph_builder` defaults to the ingest agent graph (decision 0006).
        """
        self.graph = (graph_builder or builder).compile(
            checkpointer=checkpointer, name="ingest"
        )
        self.context: IngestContext = context or {}

    @classmethod
    def in_memory(
        cls,
        context: IngestContext | None = None,
        graph_builder: StateGraph | None = None,
    ) -> IngestRunner:
        """Return a runner whose state lives only in this process (tests, quick tries)."""
        return cls(InMemorySaver(serde=checkpoint_serializer()), context, graph_builder)

    @classmethod
    def sqlite(
        cls,
        path: Path | None = None,
        context: IngestContext | None = None,
        graph_builder: StateGraph | None = None,
    ) -> IngestRunner:
        """Return a runner whose threads survive restarts (decision 0005).

        `path` defaults to `WIKI_STATE_DIR/checkpoints.sqlite`, outside the vault.
        """
        if path is None:
            path = get_settings().state_dir / "checkpoints.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        saver = SqliteSaver(conn, serde=checkpoint_serializer())
        return cls(saver, context, graph_builder)

    def _config(self, thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    def start(self, note_path: str, thread_id: str | None = None) -> RunResult:
        """Ingest a note until the review pauses (or it ends early)."""
        thread_id = thread_id or self.new_thread_id()
        self.graph.invoke(
            {"note_path": note_path},
            self._config(thread_id),
            context=self.context,
        )
        return self.status(thread_id)

    def resume(
        self, thread_id: str, answer: ReviewAnswer | dict[str, Any] | str
    ) -> RunResult:
        """Send the user's answer.

        An invalid answer comes back as a new review with `review.error` set.

        Raises:
            ValueError: Nothing is waiting for an answer on this thread, or the thread
                belongs to another vault than this runner's.
        """
        self.graph.invoke(
            self._resume_command(thread_id, answer),
            self._config(thread_id),
            context=self.context,
        )
        return self.status(thread_id)

    @staticmethod
    def new_thread_id() -> str:
        """Return a fresh ingest thread id."""
        return f"ingest-{uuid.uuid4().hex[:12]}"

    def stream_start(self, note_path: str, thread_id: str) -> Iterator[dict[str, Any]]:
        """Ingest a note, yielding LangGraph v2 stream parts until the review pauses."""
        yield from self.graph.stream(
            {"note_path": note_path},
            self._config(thread_id),
            context=self.context,
            stream_mode=STREAM_MODES,
            subgraphs=True,
            version="v2",
        )

    def stream_resume(
        self, thread_id: str, answer: ReviewAnswer | dict[str, Any] | str
    ) -> Iterator[dict[str, Any]]:
        """Send the user's answer, yielding v2 stream parts (same checks as `resume`)."""
        command = self._resume_command(thread_id, answer)
        yield from self.graph.stream(
            command,
            self._config(thread_id),
            context=self.context,
            stream_mode=STREAM_MODES,
            subgraphs=True,
            version="v2",
        )

    def _resume_command(
        self, thread_id: str, answer: ReviewAnswer | dict[str, Any] | str
    ) -> Command[Any]:
        """Check the thread can take an answer here; return the resume command."""
        if self.status(thread_id).status != "review":
            raise ValueError(f"no review is waiting on thread {thread_id!r}")
        self._check_vault(thread_id)
        if isinstance(answer, ReviewAnswer):
            answer = answer.model_dump(mode="json")
        return Command(resume=answer)

    def vault_of(self, thread_id: str) -> Path | None:
        """Return the vault a thread's run works on (None: not started here)."""
        values = self.graph.get_state(self._config(thread_id)).values
        return Path(values["vault_path"]) if values.get("vault_path") else None

    def _check_vault(self, thread_id: str) -> None:
        """Refuse to resume a review for another vault (threads share one SQLite file)."""
        theirs = self.vault_of(thread_id)
        mine = self.context.get("vault_path")
        mine_path = (
            Path(mine).expanduser().resolve() if mine else get_settings().vault_path
        )
        if theirs is not None and theirs != mine_path:
            raise ValueError(
                f"thread {thread_id!r} reviews {theirs}, not {mine_path}; answer it "
                "with that vault"
            )

    def status(self, thread_id: str) -> RunResult:
        """Read where the thread stands from the checkpointer (no graph run)."""
        snapshot = self.graph.get_state(self._config(thread_id))
        interrupt = snapshot.interrupts[0].value if snapshot.interrupts else None
        return run_result(thread_id, snapshot.values, interrupt, snapshot.next)
