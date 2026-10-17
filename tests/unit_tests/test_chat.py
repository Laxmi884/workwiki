"""The chat (0010 S3): commands drive ingest, other messages ask; on a `Backend`."""

import datetime

import pytest
from langchain_core.messages import AIMessage

from llm_wiki.backend import local as local_backend
from llm_wiki.backend.local import LocalBackend
from llm_wiki.chat.commands import parse_command
from llm_wiki.chat.session import questions_only, reply
from llm_wiki.ingest import nodes

from .scripted_model import AgentRounds, EchoModel, ToolScript, call

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
        "/remember",
        "/alias Sammy",
        "/alias = person-sam-ortiz",
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
    """The chat over a local backend on a vault copy: scripted ingest, scripted answers."""
    ingest = AgentRounds(
        [_decision("Keep the old BI tool until Q1")],
        [
            call("withdraw_fact", line=23, target="decision-log"),
            _decision("Keep the old BI tool until Q2"),
        ],
    )
    monkeypatch.setattr(nodes, "chat_model", ingest)
    answers = iter(
        [
            "Atlas beta moved to Oct 20 ([[project-atlas]], 2026-10-12).",
            "Sam wants to own search ([[person-sam-ortiz]], 2026-10-12).",
        ]
    )
    monkeypatch.setattr(
        local_backend,
        "configured_model",
        lambda model: ToolScript(messages=iter([AIMessage(next(answers))])),
    )
    monkeypatch.setattr(local_backend, "configured_fallbacks", lambda: [])
    backend = LocalBackend.in_memory(vault_copy)
    history: list[tuple[str, str]] = []

    def say(text):
        answer = reply(backend, text, history, today=TODAY)
        history.extend([("user", text), ("assistant", answer)])
        return answer

    return say, ingest, backend


def test_review_waits_while_you_ask_questions_then_revise_and_approve(chat, vault_copy):
    say, ingest, backend = chat
    reply_ = say("/ingest 2026-10-12")
    assert reply_.startswith(f"**Review: {NOTE}**")
    (open_review,) = backend.pending()
    assert f"(/review/{open_review.thread_id})" in reply_  # link to the review page
    assert "Keep the old BI tool until Q1" in reply_ and "`/approve`" in reply_

    # no time to review: ask questions instead; the review stays open
    reply_ = say("When is the Atlas beta?")
    assert reply_.startswith("Atlas beta moved to Oct 20")
    assert f"A review is still open for {NOTE}" in reply_
    assert say("What does Sam want?").startswith("Sam wants to own search")

    # back to it: show it again, revise, then approve
    assert say("/review").startswith(f"**Review: {NOTE}**")
    reply_ = say("/revise It was Q2, not Q1")
    assert "until Q2" in reply_ and "### Keep the old BI tool until Q1" not in reply_
    assert "It was Q2, not Q1" in ingest.briefs[1]
    assert say("/approve").startswith("Applied")
    assert backend.pending() == []
    log = (vault_copy / "30_decisions/2026/decisions-2026-q4.md").read_text()
    assert "### Keep the old BI tool until Q2" in log

    assert say("/approve").startswith("No review is open")


def test_one_review_at_a_time_and_bad_answers_are_explained(chat):
    say, _, _ = chat
    say("/ingest 2026-10-12")
    assert say("/ingest 2026-10-13").startswith("A review is open for")
    reply_ = say("/approve except f99")
    assert "Not accepted: unknown file ids ['f99']" in reply_
    assert say("/reject").startswith("Done. Nothing was written.")
    assert say("/ingest 2026-01-01").startswith("No note at 00_raw/daily/2026-01-01.md")


def test_with_two_reviews_open_the_chat_lists_them_instead_of_guessing(chat):
    say, _, backend = chat
    first = backend.start_ingest(NOTE)  # e.g. started from Studio
    second = backend.start_ingest(NOTE)
    listing = say("/approve")
    assert listing.startswith("2 reviews are open")
    assert f"(/review/{first.thread_id})" in listing
    assert f"(/review/{second.thread_id})" in listing
    assert len(backend.pending()) == 2  # nothing was answered


def test_the_assistant_sees_questions_and_answers_but_not_commands(
    vault_copy, monkeypatch
):
    monkeypatch.setattr(local_backend, "configured_model", lambda model: EchoModel())
    monkeypatch.setattr(local_backend, "configured_fallbacks", lambda: [])
    backend = LocalBackend.in_memory(vault_copy)
    history = [
        ("user", "What is Atlas?"),
        ("assistant", "A risk analytics project."),
        ("user", "/help"),
        ("assistant", "Commands: ..."),
    ]
    assert questions_only(history) == history[:2]
    # EchoModel replies with the turns it was sent
    assert reply(backend, "Is it on track?", history) == (
        "What is Atlas? | A risk analytics project. | Is it on track?"
    )


def test_parse_remember_commands():
    rule = parse_command("/remember Don't file\nlunch plans", TODAY)
    assert (rule.kind, rule.rule) == ("remember", "Don't file lunch plans")
    alias = parse_command("/alias Sammy O = person-sam-ortiz", TODAY)
    assert (alias.kind, alias.alias) == ("remember", ("Sammy O", "person-sam-ortiz"))


def test_remember_in_chat_adds_to_what_the_review_remembers(chat, vault_copy):
    say, _, backend = chat
    say("/ingest 2026-10-12")
    say("/remember Don't file lunch plans")
    reply_ = say("/alias stakeholders = project-atlas")
    assert "- rule: Don't file lunch plans" in reply_  # the first one is kept
    assert "- “stakeholders” means `project-atlas`" in reply_
    assert "Not accepted: 'Sam' already names" in say("/alias Sam = project-atlas")
    assert say("/approve").startswith("Applied")
    assert "- Don't file lunch plans" in (vault_copy / "90_meta/rules.md").read_text()
