"""The runner: start → review pause → resume, without Studio (in-memory checkpointer)."""

import json
import shutil

import pytest

from llm_wiki.domain.facts import Decision, Learning, SectionFacts
from llm_wiki.ingest import nodes
from llm_wiki.runner import IngestRunner

from .scripted_model import BySection

NOTE = "00_raw/daily/2026-10-12.md"
ANSWERS = {
    "Meetings": SectionFacts(
        decisions=[Decision(line=23, title="Keep the old BI tool until Q1")]
    ),
    "Learned": SectionFacts(
        learnings=[Learning(line=47, topic="Cookies", summary="Must match.")]
    ),
}


@pytest.fixture
def runner(fixture_vault, monkeypatch):
    model = BySection(ANSWERS, SectionFacts())
    monkeypatch.setattr(nodes, "chat_model", lambda runtime: model)
    return IngestRunner.in_memory(context={"vault_path": str(fixture_vault)})


def test_start_pauses_at_the_review(runner):
    result = runner.start(NOTE)
    assert result.status == "review"
    review = result.review
    assert review.note == NOTE
    assert [(f.id, f.path) for f in review.files] == [
        ("f1", "00_raw/daily/2026-10-12.md"),
        ("f2", "90_meta/tasks.json"),
        ("f3", "30_decisions/2026/decisions-2026-q4.md"),
        ("f4", "05_generated/inbox.md"),
        ("f5", "90_meta/ingest-ledger.json"),
    ]
    assert [d.title for d in review.decisions] == ["Keep the old BI tool until Q1"]
    assert "learning (Cookies): Must match." in review.inbox[0].text
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


def test_unchanged_note_is_skipped(tmp_path, fixture_vault, monkeypatch):
    vault = tmp_path / "vault"
    shutil.copytree(fixture_vault, vault)
    monkeypatch.setattr(
        nodes, "chat_model", lambda runtime: BySection(ANSWERS, SectionFacts())
    )
    runner = IngestRunner.in_memory(context={"vault_path": str(vault)})
    started = runner.start(NOTE)
    values = runner.graph.get_state(runner._config(started.thread_id)).values
    for diff in values["diffs"]:  # as if all approved and applied (3d)
        target = vault / diff.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(diff.after_text)

    again = runner.start(NOTE)
    assert again.status == "skipped"
    assert any("already ingested and unchanged" in w for w in again.warnings)


def test_checkpoints_restore_our_types_without_warnings(runner, caplog):
    started = runner.start(NOTE)
    runner.resume(started.thread_id, "approve")
    assert not [r for r in caplog.records if "unregistered type" in r.getMessage()]
