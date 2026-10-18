"""Phase 5d: oversized context never floods the model, with Deep Agents' built-in offload.

No middleware of ours: `FilesystemMiddleware` moves a tool result over 20,000 tokens, or a
user message over 50,000, into its in-memory store and leaves a preview with the path.
These tests pin what we rely on, so a Deep Agents upgrade that changes it fails here.
"""

import datetime
import re

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from llm_wiki.assistant.tools import MAX_TASKS, make_find_tasks
from llm_wiki.domain.task_index import TaskIndex, TaskRecord
from llm_wiki.domain.tasks import TaskStatus
from llm_wiki.ingest import nodes
from llm_wiki.ingest.graph import builder as agent_builder
from llm_wiki.runner import IngestRunner
from llm_wiki.task_tracking import TASK_INDEX_PATH, dump_task_index

from .scripted_model import ToolScript, call

TOKEN = "glpat-" + "Ab3dEf6hIj9kLm2nOp5q"


def test_find_tasks_caps_its_own_output():
    day = datetime.date(2026, 10, 14)
    index = TaskIndex(
        tasks={
            f"t-{i:06d}": TaskRecord(
                text=f"Task {i}",
                status=TaskStatus.OPEN,
                first_seen=day,
                last_seen=day,
                source="00_raw/daily/2026-10-14.md",
                line=5,
            )
            for i in range(MAX_TASKS + 25)
        }
    )
    reply = make_find_tasks({TASK_INDEX_PATH: dump_task_index(index)}.get).invoke({})
    lines = reply.splitlines()
    assert lines[0] == f"{MAX_TASKS + 25} tasks:"
    assert len(lines) == 1 + MAX_TASKS + 1
    assert lines[-1] == "... and 25 more; narrow the filters"


class _ReadsTheOffloadedBrief(ToolScript):
    """First call: find the path in the stub and read it all back. Then finish."""

    seen: list[list[tuple[str, str]]] = []

    def _generate(self, messages, *args, **kwargs):  # type: ignore[override]
        self.seen.append([(m.type, str(m.content)) for m in messages])
        if len(self.seen) == 1:
            stub = self.seen[0][-1][1]
            path = re.search(r"saved to the filesystem at: (\S+)", stub)
            reply = call(
                "read_file", file_path=path.group(1) if path else "/none", limit=7000
            )
        else:
            reply = AIMessage("Filed nothing.")
        return ChatResult(generations=[ChatGeneration(message=reply)])


def test_a_huge_note_is_offloaded_read_back_and_still_redacted(vault_copy, monkeypatch):
    note = vault_copy / "00_raw/daily/2026-10-12.md"
    log = "\n".join(f"    10:{i % 60:02d} INFO build step {i} ok" for i in range(6000))
    note.write_text(
        note.read_text()
        + f"\n## Notes\n- Pasted CI log, token {TOKEN}\n```\n{log}\n```\n"
    )
    model = _ReadsTheOffloadedBrief(seen=[], messages=iter([]))
    monkeypatch.setattr(nodes, "chat_model", lambda runtime: model)
    monkeypatch.setattr(nodes, "fallback_models", lambda runtime: [])
    runner = IngestRunner.in_memory(
        context={"vault_path": str(vault_copy)}, graph_builder=agent_builder
    )
    before = set(vault_copy.rglob("*"))

    assert runner.start("00_raw/daily/2026-10-12.md").status == "review"

    (brief,) = [text for kind, text in model.seen[0] if kind == "human"]
    assert (
        len(brief) < 5_000
        and "saved to the filesystem at: /conversation_history/" in brief
    )
    (back,) = [text for kind, text in model.seen[1] if kind == "tool"]
    assert "Pasted CI log, token [REDACTED_SECRET]" in back  # all of it, redacted
    assert not any(TOKEN in text for call_ in model.seen for _, text in call_)
    assert set(vault_copy.rglob("*")) == before  # the offload never lands in the vault
