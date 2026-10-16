"""The assistant (0008): task lines from code, a read-only vault, a scripted run."""

import asyncio
import datetime

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from llm_wiki.assistant.agent import build_assistant
from llm_wiki.assistant.tools import make_find_tasks, task_lines
from llm_wiki.domain.brag import BragDraft, BragItem
from llm_wiki.domain.task_index import TaskIndex, TaskRecord
from llm_wiki.domain.tasks import TaskStatus
from llm_wiki.task_tracking import TASK_INDEX_PATH, dump_task_index

from .scripted_model import ToolScript, call

D = datetime.date


def _record(
    text, status, project=None, people=(), first=D(2026, 10, 12), last=None, tags=()
):
    return TaskRecord(
        text=text,
        status=status,
        project=project,
        mapped_by="link" if project else None,
        people=people,
        tags=tags,
        first_seen=first,
        last_seen=last or first,
        source=f"00_raw/daily/{last or first}.md",
        line=8,
    )


INDEX = TaskIndex(
    tasks={
        "t-aaaaaa": _record(
            "Ship beta",
            TaskStatus.OPEN,
            "project-atlas",
            ("person-alex-chen",),
            last=D(2026, 10, 13),
            tags=("blocked",),
        ),
        "t-bbbbbb": _record("Fix login", TaskStatus.DONE, "project-atlas"),
        "t-cccccc": _record(
            "Book 1:1",
            TaskStatus.IN_PROGRESS,
            None,
            ("person-sam-ortiz",),
            first=D(2026, 10, 11),
        ),
        "t-dddddd": _record("Old idea", TaskStatus.CANCELLED),
    }
)


def test_task_lines_format_and_order():
    assert task_lines(INDEX) == [
        "- [/] Book 1:1 (t-cccccc; no project; person-sam-ortiz; since 2026-10-11; "
        "[[2026-10-11]] line 8)",
        "- [ ] Ship beta (t-aaaaaa; project-atlas; person-alex-chen; since 2026-10-12; "
        "last seen 2026-10-13; #blocked; [[2026-10-13]] line 8)",
    ]


@pytest.mark.parametrize(
    ("filters", "ids"),
    [
        ({"project": "project-atlas", "status": "all"}, ["t-aaaaaa", "t-bbbbbb"]),
        ({"project": "none"}, ["t-cccccc"]),
        ({"person": "person-alex-chen"}, ["t-aaaaaa"]),
        ({"status": "done"}, ["t-bbbbbb"]),
        ({"status": "cancelled"}, ["t-dddddd"]),
        ({"since": D(2026, 10, 13)}, ["t-aaaaaa"]),
    ],
)
def test_task_filters(filters, ids):
    lines = task_lines(INDEX, **filters)
    assert [line.split("(")[1].split(";")[0] for line in lines] == ids


def test_find_tasks_tool_reads_the_index_and_explains_problems():
    files = {TASK_INDEX_PATH: dump_task_index(INDEX)}
    tool = make_find_tasks(files.get)
    out = tool.invoke({"project": "project-atlas", "status": "all"})
    assert out.startswith("2 tasks:") and "Fix login" in out
    assert tool.invoke({"person": "person-nobody"}) == "No tasks match."
    assert "YYYY-MM-DD" in tool.invoke({"since": "last week"})
    broken = make_find_tasks({TASK_INDEX_PATH: "{not json"}.get)
    assert broken.invoke({}).startswith("The task index can't be read")
    assert make_find_tasks({}.get).invoke({}) == "No tasks match."  # no index yet


def _snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_scripted_run_reads_but_never_writes(vault_copy):
    (vault_copy / TASK_INDEX_PATH).parent.mkdir(parents=True, exist_ok=True)
    (vault_copy / TASK_INDEX_PATH).write_text(dump_task_index(INDEX))
    (vault_copy / ".git").mkdir()
    (vault_copy / ".git/HEAD").write_text("ref: refs/heads/main\n")
    before = _snapshot(vault_copy)

    script = ToolScript(
        messages=iter(
            [
                call("find_tasks", project="project-atlas"),
                call("read_file", file_path="/vault/10_projects/project-atlas.md"),
                call("read_file", file_path="/vault/.git/HEAD"),
                call("write_file", file_path="/vault/x.md", content="nope"),
                AIMessage("Ship beta is open ([[project-atlas]], 2026-10-13)."),
            ]
        )
    )
    agent = build_assistant(vault_copy, script)
    result = agent.invoke({"messages": [{"role": "user", "content": "Atlas tasks?"}]})

    replies = [m.content for m in result["messages"] if isinstance(m, ToolMessage)]
    assert replies[0].startswith("1 tasks:") and "Ship beta" in replies[0]
    assert "Atlas" in replies[1]
    assert "denied" in replies[2].lower() or "permission" in replies[2].lower()
    assert "write_file" in replies[3] and "not a valid tool" in replies[3]
    assert result["messages"][-1].content.startswith("Ship beta is open")
    assert _snapshot(vault_copy) == before


IMPACT = "40_impact/2026/impact-2026-q4.md"
IMPACT_TEXT = (
    "---\ntype: impact\n---\n# Impact 2026 Q4\n\n## Wins\n"
    "- 2026-10-12: Found the login redirect root cause ([[project-atlas]]) "
    "([[2026-10-12]])\n"
)


def test_review_writer_returns_a_sourced_draft(vault_copy):
    (vault_copy / IMPACT).parent.mkdir(parents=True)
    (vault_copy / IMPACT).write_text(IMPACT_TEXT)
    draft = {
        "period": "2026 Q4",
        "sections": [
            {
                "project": "project-atlas",
                "items": [
                    {
                        "kind": "win",
                        "text": "Found the login redirect root cause",
                        "date": "2026-10-12",
                        "source": "[[2026-10-12]]",
                    }
                ],
            }
        ],
        "gaps": ["No metric recorded for the login redirect fix."],
    }
    script = ToolScript(
        messages=iter(
            [
                call(
                    "task",
                    description="Brag sheet for 2026 Q4",
                    subagent_type="review-writer",
                ),
                # the subagent (same scripted model): read, then the structured answer
                call("read_file", file_path=f"/vault/{IMPACT}"),
                call("BragDraft", **draft),
                AIMessage("Q4: login redirect fix ([[2026-10-12]]). Gap: no metric."),
            ]
        )
    )
    agent = build_assistant(vault_copy, script)
    result = agent.invoke({"messages": [{"role": "user", "content": "Q4 brag sheet"}]})

    (task_reply,) = [
        m for m in result["messages"] if isinstance(m, ToolMessage) and m.name == "task"
    ]
    returned = BragDraft.model_validate_json(task_reply.content)
    assert returned.sections[0].items[0].source == "[[2026-10-12]]"
    assert returned.gaps == ["No metric recorded for the login redirect fix."]


@pytest.mark.parametrize(
    ("field", "value"),
    [("source", "2026-10-12"), ("date", "Oct 12"), ("kind", "promotion")],
)
def test_brag_items_need_a_link_source_and_iso_date(field, value):
    item = {"kind": "win", "text": "x", "date": "2026-10-12", "source": "[[p]]"}
    with pytest.raises(ValueError):
        BragItem(**{**item, field: value})


def test_graph_factory_takes_vault_and_model_from_the_run(monkeypatch, vault_copy):
    from types import SimpleNamespace

    from llm_wiki.assistant import graph as assistant_graph

    asked = []
    monkeypatch.setattr(
        assistant_graph,
        "configured_model",
        lambda override: asked.append(override) or ToolScript(messages=iter([])),
    )
    monkeypatch.setattr(assistant_graph, "configured_fallbacks", lambda: [])
    context = {"vault_path": str(vault_copy), "model": "openrouter:x/y"}
    run = SimpleNamespace(execution_runtime=SimpleNamespace(context=context))
    assert asyncio.run(assistant_graph.make_graph(run)) is not None
    assert asked == ["openrouter:x/y"]
    assert assistant_graph.run_choice(context) == (
        vault_copy.resolve(),
        "openrouter:x/y",
    )


def test_graph_factory_does_not_block_the_servers_event_loop(monkeypatch, vault_copy):
    """`langgraph dev` refuses blocking calls on its loop (blockbuster); resolving
    the vault path is one, so the factory must build the graph off the loop."""
    from types import SimpleNamespace

    from blockbuster import blockbuster_ctx

    from llm_wiki.assistant import graph as assistant_graph

    monkeypatch.setattr(
        assistant_graph, "configured_model", lambda _: ToolScript(messages=iter([]))
    )
    monkeypatch.setattr(assistant_graph, "configured_fallbacks", lambda: [])
    context = {"vault_path": str(vault_copy)}
    run = SimpleNamespace(execution_runtime=SimpleNamespace(context=context))
    with blockbuster_ctx():
        assert asyncio.run(assistant_graph.make_graph(run)) is not None
