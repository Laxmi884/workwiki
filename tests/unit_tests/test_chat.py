"""The chat front door (4d): commands drive ingest, other messages ask the assistant."""

import datetime

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from llm_wiki.chat import graph as chat_graph
from llm_wiki.chat.commands import parse_command
from llm_wiki.ingest import nodes
from llm_wiki.runner import IngestRunner

from .scripted_model import AgentRounds, ToolScript, call

NOTE = "00_raw/daily/2026-10-12.md"
TODAY = datetime.date(2026, 10, 16)


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        ("/ingest", "ingest", "00_raw/daily/2026-10-16.md"),
        ("/ingest 2026-10-12", "ingest", NOTE),
        ("/approve", "answer", "approve"),
        (
            "/approve except f2 f5",
            "answer",
            {"action": "approve", "reject": ["f2", "f5"]},
        ),
        ("/reject", "answer", "reject"),
        (
            "/revise line 9 is Atlas work",
            "answer",
            {"action": "revise", "feedback": "line 9 is Atlas work"},
        ),
        (
            "/edit f3\n# Atlas\nnew text",
            "answer",
            {"action": "edit", "file": "f3", "text": "# Atlas\nnew text\n"},
        ),
        (
            "/tasks m1=project-x m2=none",
            "answer",
            {"action": "answer", "tasks": {"m1": "project-x", "m2": "none"}},
        ),
        ("/wins none", "answer", {"action": "answer", "wins": []}),
        ("/review", "review", None),
    ],
)
def test_parse_commands(text, kind, value):
    parsed = parse_command(text, TODAY)
    assert parsed.kind == kind
    got = parsed.note_path if kind == "ingest" else parsed.answer
    assert got == value


@pytest.mark.parametrize(
    "text",
    [
        "/ingest last tuesday",
        "/approve f2",
        "/revise",
        "/edit f3",
        "/tasks m1",
        "/wins",
        "/frobnicate",
    ],
)
def test_bad_commands_explain_themselves(text):
    parsed = parse_command(text, TODAY)
    assert parsed.kind == "error" and "Commands:" in parsed.message


def _decision(title):
    return call(
        "file_fact",
        target="decision-log",
        section="Decisions",
        text="Not recorded in the note.",
        line=23,
        title=title,
    )


@pytest.fixture
def chat(vault_copy, monkeypatch):
    """The wiki graph over a vault copy: scripted ingest rounds, scripted answers."""
    ingest = AgentRounds(
        [_decision("Keep the old BI tool until Q1")],
        [
            call("withdraw_fact", line=23, target="decision-log"),
            _decision("Keep the old BI tool until Q2"),
        ],
    )
    monkeypatch.setattr(nodes, "chat_model", ingest)
    runner = IngestRunner.in_memory(context={"vault_path": str(vault_copy)})
    monkeypatch.setattr(chat_graph, "ingest_runner", lambda vault, model: runner)
    answers = iter(
        [
            "Atlas beta moved to Oct 20 ([[project-atlas]], 2026-10-12).",
            "Sam wants to own search ([[person-sam-ortiz]], 2026-10-12).",
        ]
    )
    monkeypatch.setattr(
        chat_graph,
        "configured_model",
        lambda model: ToolScript(messages=iter([AIMessage(next(answers))])),
    )
    monkeypatch.setattr(chat_graph, "configured_fallbacks", lambda: [])
    graph = chat_graph.builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "chat-1"}}
    context = {"vault_path": str(vault_copy)}

    def say(text):
        out = graph.invoke(
            {"messages": [{"role": "user", "content": text}]},
            config=config,
            context=context,
        )
        return out["messages"][-1].content, out.get("ingest_thread")

    return say, ingest


def test_review_waits_while_you_ask_questions_then_revise_and_approve(chat, vault_copy):
    say, ingest = chat
    reply, thread = say("/ingest 2026-10-12")
    assert reply.startswith(f"**Review: {NOTE}**") and thread
    assert "Keep the old BI tool until Q1" in reply and "`/approve`" in reply

    # no time to review: ask questions instead; the review stays open
    reply, still = say("When is the Atlas beta?")
    assert reply.startswith("Atlas beta moved to Oct 20") and still == thread
    assert "A review is still open" in reply
    reply, still = say("What does Sam want?")
    assert reply.startswith("Sam wants to own search") and still == thread

    # back to it: show it again, revise, then approve
    assert say("/review")[0].startswith(f"**Review: {NOTE}**")
    reply, still = say("/revise It was Q2, not Q1")
    assert (
        still == thread
        and "until Q2" in reply
        and "### Keep the old BI tool until Q1" not in reply
    )
    assert "It was Q2, not Q1" in ingest.briefs[1]
    reply, done = say("/approve")
    assert reply.startswith("Applied") and done is None
    log = (vault_copy / "30_decisions/2026/decisions-2026-q4.md").read_text()
    assert "### Keep the old BI tool until Q2" in log

    assert say("/approve")[0].startswith("No review is open")


def test_one_review_at_a_time_and_bad_answers_are_explained(chat):
    say, _ = chat
    say("/ingest 2026-10-12")
    assert say("/ingest 2026-10-13")[0].startswith("A review is open for")
    reply, thread = say("/approve except f99")
    assert "Not accepted: unknown file ids ['f99']" in reply and thread
    assert say("/reject")[0].startswith("Done. Nothing was written.")
    assert say("/ingest 2026-01-01")[0].startswith("No note at")
