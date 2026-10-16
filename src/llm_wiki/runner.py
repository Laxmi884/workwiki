"""Run ingest without Studio or a server: start a note, get the review, answer it.

Surfaces reach it only through `LocalBackend` (decision 0010); none of them touches
the runner or the graph directly. The runner owns the checkpointer and the thread IDs:
`start` runs until the review pauses, `resume` sends the answer and runs on.

    runner = IngestRunner.sqlite(context={"vault_path": "~/WorkDev"})   # survives restarts
    result = runner.start("00_raw/daily/2026-10-12.md")   # status "review"
    result = runner.resume(result.thread_id, {"action": "approve"})
"""

from __future__ import annotations

import enum
import importlib
import inspect
import pkgutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph
from langgraph.types import Command
from pydantic import BaseModel

import llm_wiki.domain
from llm_wiki.backend.base import RunResult, run_result
from llm_wiki.config import get_settings
from llm_wiki.domain.review import ReviewAnswer
from llm_wiki.ingest.graph import builder
from llm_wiki.ingest.state import IngestContext


def checkpoint_types() -> list[tuple[str, str]]:
    """Return (module, class) for every model and enum in `llm_wiki.domain`.

    State holds these types; the checkpointer only restores types on this list (newer
    LangGraph versions refuse unlisted ones).
    """
    found: list[tuple[str, str]] = []
    for info in pkgutil.iter_modules(llm_wiki.domain.__path__):
        module = importlib.import_module(f"llm_wiki.domain.{info.name}")
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ == module.__name__ and issubclass(
                obj, BaseModel | enum.Enum
            ):
                found.append((module.__name__, name))
    return sorted(found)


def checkpoint_serializer() -> JsonPlusSerializer:
    """Return a serializer that restores our domain types (and the safe built-ins)."""
    return JsonPlusSerializer(allowed_msgpack_modules=checkpoint_types())


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
        thread_id = thread_id or f"ingest-{uuid.uuid4().hex[:12]}"
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
        if self.status(thread_id).status != "review":
            raise ValueError(f"no review is waiting on thread {thread_id!r}")
        self._check_vault(thread_id)
        if isinstance(answer, ReviewAnswer):
            answer = answer.model_dump(mode="json")
        self.graph.invoke(
            Command(resume=answer), self._config(thread_id), context=self.context
        )
        return self.status(thread_id)

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
        return run_result(thread_id, snapshot.values, interrupt)
