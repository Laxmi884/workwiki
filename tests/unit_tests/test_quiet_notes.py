"""Quiet notes (spec 2026-10-18, "Agent skip"): no model call when nothing is new.

A rollover-only note holds only carried tasks, each with an ID already; a tick-only
edit changes only task lines that have IDs. Task tracking (code) records both, and
done tasks reach the review as wins; the agent has nothing to file, so `stage`
doesn't build it.
"""

import datetime

from langgraph.types import Command

from llm_wiki.ingest import nodes
from llm_wiki.ingest.graph import builder as agent_builder
from llm_wiki.rollover import roll_over
from llm_wiki.runner import IngestRunner

from .scripted_model import AgentRounds, bi_tool_decision

NOTE = "00_raw/daily/2026-10-12.md"
DAY = datetime.date(2026, 10, 14)


def _runner(vault, monkeypatch):
    rounds = AgentRounds([bi_tool_decision()])
    monkeypatch.setattr(nodes, "chat_model", rounds)
    runner = IngestRunner.in_memory(
        context={"vault_path": str(vault)}, graph_builder=agent_builder
    )
    return runner, rounds


def _values(runner, thread_id):
    return runner.graph.get_state({"configurable": {"thread_id": thread_id}}).values


def _ingested(runner):
    started = runner.start(NOTE)
    runner.resume(started.thread_id, "approve")


def test_a_rollover_only_note_runs_no_model(vault_copy, monkeypatch):
    runner, rounds = _runner(vault_copy, monkeypatch)
    _ingested(runner)
    rolled = roll_over(vault_copy, DAY, commit=False)
    assert rolled.created and rolled.carried

    result = runner.start(rolled.path)

    assert len(rounds.briefs) == 1, "only the first note's agent round"
    assert result.status == "review"
    stats = _values(runner, result.thread_id)["agent_stats"]
    assert (stats["skipped"], stats["model_calls"]) == ("nothing new to file", 0)


def test_a_tick_only_edit_runs_no_model_and_offers_the_win(vault_copy, monkeypatch):
    runner, rounds = _runner(vault_copy, monkeypatch)
    _ingested(runner)
    path = vault_copy / NOTE
    text = path.read_text()
    ticked = text.replace(
        "- [ ] Review Q3 budget draft", "- [x] Review Q3 budget draft"
    )
    assert ticked != text
    path.write_text(ticked)

    result = runner.start(NOTE)

    assert len(rounds.briefs) == 1
    assert any("Review Q3 budget draft" in w.text for w in result.review.wins)


def test_a_new_task_on_a_rollover_note_still_runs_the_agent(vault_copy, monkeypatch):
    runner, rounds = _runner(vault_copy, monkeypatch)
    _ingested(runner)
    rolled = roll_over(vault_copy, DAY, commit=False)
    note = vault_copy / rolled.path
    text = note.read_text()
    assert "## Tasks\n- [ ] \n" in text  # the template's empty task line
    note.write_text(text.replace("## Tasks\n- [ ] \n", "## Tasks\n- [ ] Call Sam\n"))

    runner.start(rolled.path)

    assert len(rounds.briefs) == 2


def test_new_words_on_a_known_task_still_run_the_agent(vault_copy, monkeypatch):
    """A tick is code's; new words on the task line may carry a fact (seen once only)."""
    runner, rounds = _runner(vault_copy, monkeypatch)
    _ingested(runner)
    rolled = roll_over(vault_copy, DAY, commit=False)
    note = vault_copy / rolled.path
    text = note.read_text()
    old = "- [ ] Review Q3 budget draft for [[project-atlas]] ^t-q7m2xa"
    assert old in text
    note.write_text(
        text.replace(
            old,
            "- [ ] Review Q3 budget draft for [[project-atlas]], CFO wants it Friday "
            "^t-q7m2xa",
        )
    )

    runner.start(rolled.path)

    assert len(rounds.briefs) == 2


def test_revise_on_a_quiet_note_runs_the_agent(vault_copy, monkeypatch):
    """The skip is for first rounds only: asking the agent to look again runs it."""
    runner, rounds = _runner(vault_copy, monkeypatch)
    _ingested(runner)
    rolled = roll_over(vault_copy, DAY, commit=False)
    started = runner.start(rolled.path)
    assert len(rounds.briefs) == 1

    again = runner.resume(
        started.thread_id, {"action": "revise", "feedback": "look again"}
    )

    assert len(rounds.briefs) == 2
    assert "look again" in rounds.briefs[-1]
    assert again.status == "review" and again.review.error is None


def test_the_graph_reports_steps_on_the_custom_stream(vault_copy, monkeypatch):
    runner, _ = _runner(vault_copy, monkeypatch)
    config = {"configurable": {"thread_id": "t1"}}
    context = {"vault_path": str(vault_copy)}

    def custom(graph_input):
        return [
            p["data"]
            for p in runner.graph.stream(
                graph_input, config, context=context, stream_mode="custom", version="v2"
            )
        ]

    assert custom({"note_path": NOTE}) == [
        {"step": "stage", "detail": "facts filed: 1", "state": "done"}
    ]
    # the fixture copy is not a git repo: nothing to commit into
    assert custom(Command(resume="approve")) == [
        {"step": "commit", "detail": "not committed", "state": "skipped"}
    ]
