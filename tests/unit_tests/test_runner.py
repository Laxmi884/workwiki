"""The runner on the ingest agent graph: start → review pause → resume (in memory)."""

import json
import shutil

import pytest

from llm_wiki.ingest import nodes
from llm_wiki.runner import IngestRunner

from .scripted_model import AgentRounds, call

NOTE = "00_raw/daily/2026-10-12.md"
DECISION_LOG = "30_decisions/2026/decisions-2026-q4.md"


def decision(title):
    return call(
        "file_fact",
        target="decision-log",
        section="Decisions",
        text="Not recorded in the note.",
        line=23,
        title=title,
    )


@pytest.fixture
def model(monkeypatch):
    rounds = AgentRounds([decision("Keep the old BI tool until Q1")])
    monkeypatch.setattr(nodes, "chat_model", rounds)
    return rounds


@pytest.fixture
def runner(vault_copy, model):
    return IngestRunner.in_memory(context={"vault_path": str(vault_copy)})


def _file(result, path):
    (found,) = [f for f in result.review.files if f.path == path]
    return found


def test_start_pauses_at_the_review(runner, model):
    result = runner.start(NOTE)
    assert result.status == "review"
    review = result.review
    assert review.note == NOTE
    assert [(f.id, f.path) for f in review.files] == [
        ("f1", NOTE),
        ("f2", DECISION_LOG),
        ("f3", "90_meta/ingest-ledger.json"),
        ("f4", "90_meta/tasks.json"),
    ]
    assert "### Keep the old BI tool until Q1" in _file(result, DECISION_LOG).after_text
    assert "   [task t-q7m2xa; open; project project-atlas]" in model.briefs[0]
    json.dumps(review.model_dump(mode="json"))  # the interrupt payload stays JSON


def test_bad_answer_asks_again_then_approve_some(runner):
    started = runner.start(NOTE)
    again = runner.resume(started.thread_id, {"action": "approve", "reject": ["f99"]})
    assert again.status == "review"
    assert again.review.error.startswith("unknown file ids ['f99']")
    assert [f.digest for f in again.review.files] == [
        f.digest for f in started.review.files
    ]  # same changes, nothing re-run

    done = runner.resume(started.thread_id, {"action": "approve", "reject": ["f2"]})
    assert done.status == "done" and done.action == "approve"
    assert list(done.approved) == [
        f.digest for f in started.review.files if f.id != "f2"
    ]


def test_reject_approves_nothing_and_cannot_be_answered_twice(runner):
    started = runner.start(NOTE)
    done = runner.resume(started.thread_id, "reject")
    assert (done.status, done.action, done.approved) == ("done", "reject", ())
    with pytest.raises(ValueError, match="no review is waiting"):
        runner.resume(started.thread_id, "approve")


def test_threads_are_independent(runner):
    first, second = runner.start(NOTE), runner.start(NOTE)
    assert first.thread_id != second.thread_id
    runner.resume(first.thread_id, "reject")
    assert runner.status(second.thread_id).status == "review"


def test_unchanged_note_is_skipped(tmp_path, fixture_vault, model):
    vault = tmp_path / "vault"
    shutil.copytree(fixture_vault, vault)
    runner = IngestRunner.in_memory(context={"vault_path": str(vault)})
    started = runner.start(NOTE)
    assert runner.resume(started.thread_id, "approve").status == "done"

    again = runner.start(NOTE)
    assert again.status == "skipped"
    assert any("already ingested and unchanged" in w for w in again.warnings)


def test_revise_gives_the_agent_the_feedback_and_its_filing(vault_copy, monkeypatch):
    rounds = AgentRounds(
        [decision("Keep the old BI tool until Q1")],
        [
            call("withdraw_fact", line=23, target="decision-log"),
            decision("Keep the old BI tool until Q2"),
        ],
    )
    monkeypatch.setattr(nodes, "chat_model", rounds)
    runner = IngestRunner.in_memory(context={"vault_path": str(vault_copy)})
    started = runner.start(NOTE)
    assert started.review.can_revise and started.review.revisions_left == 3

    again = runner.resume(
        started.thread_id, {"action": "revise", "feedback": "It was Q2, not Q1"}
    )
    assert again.status == "review" and again.review.revisions_left == 2
    assert len(rounds.briefs) == 2
    assert "It was Q2, not Q1" in rounds.briefs[1]
    assert "line 23 → 30_decisions" in rounds.briefs[1]  # its filing so far
    log = _file(again, DECISION_LOG).after_text
    assert "### Keep the old BI tool until Q2" in log
    assert "### Keep the old BI tool until Q1" not in log  # withdrawn


def test_revise_limit(runner):
    result = runner.start(NOTE)
    for _ in range(3):
        result = runner.resume(result.thread_id, {"action": "revise", "feedback": "x"})
    assert result.review.revisions_left == 0
    result = runner.resume(result.thread_id, {"action": "revise", "feedback": "x"})
    assert result.review.error.startswith("revise limit reached (3)")


def test_edit_replaces_one_page_and_approval_covers_the_edited_text(runner, model):
    started = runner.start(NOTE)
    log = _file(started, DECISION_LOG)
    mine = log.after_text.replace("Not recorded in the note.", "Budget freeze.")
    edited = runner.resume(
        started.thread_id, {"action": "edit", "file": log.id, "text": mine}
    )
    assert len(model.briefs) == 1  # no model call
    assert edited.review.edited == (DECISION_LOG,)
    new_log = _file(edited, DECISION_LOG)
    assert new_log.after_text == mine and new_log.digest != log.digest

    done = runner.resume(started.thread_id, "approve")
    assert new_log.digest in done.approved and log.digest not in done.approved


def test_revise_drops_edits_and_says_so(runner):
    started = runner.start(NOTE)
    log = _file(started, DECISION_LOG)
    runner.resume(
        started.thread_id, {"action": "edit", "file": log.id, "text": "# Mine\n"}
    )
    again = runner.resume(started.thread_id, {"action": "revise", "feedback": "x"})
    assert again.review.edited == ()
    assert f"your edit of {DECISION_LOG} was dropped by revise" in again.review.warnings
    assert _file(again, DECISION_LOG).after_text != "# Mine\n"


def test_many_rounds_stay_under_the_recursion_limit(runner):
    # LangGraph's step limit counts per invoke, not per thread: each resume is one
    # round, so a long review never hits it.
    result = runner.start(NOTE)
    log = _file(result, DECISION_LOG)
    for i in range(12):
        result = runner.resume(
            result.thread_id, {"action": "edit", "file": log.id, "text": f"# v{i}\n"}
        )
    assert _file(result, DECISION_LOG).after_text == "# v11\n"


def test_editing_a_code_owned_file_is_refused(runner):
    started = runner.start(NOTE)
    tasks = _file(started, "90_meta/tasks.json")
    again = runner.resume(
        started.thread_id, {"action": "edit", "file": tasks.id, "text": "{}"}
    )
    assert "only code writes" in again.review.error


def test_checkpoints_restore_our_types_without_warnings(runner, caplog):
    started = runner.start(NOTE)
    runner.resume(started.thread_id, "approve")
    assert not [r for r in caplog.records if "unregistered type" in r.getMessage()]


def test_a_review_is_answered_only_for_its_own_vault(runner, vault_copy, tmp_path):
    started = runner.start(NOTE)
    assert runner.vault_of(started.thread_id) == vault_copy.resolve()
    other = IngestRunner(runner.graph.checkpointer, {"vault_path": str(tmp_path)})
    with pytest.raises(ValueError, match="reviews .* not "):
        other.resume(started.thread_id, "approve")
    assert runner.resume(started.thread_id, "reject").status == "done"
