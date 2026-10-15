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
def model(monkeypatch):
    scripted = BySection(dict(ANSWERS), SectionFacts())
    monkeypatch.setattr(nodes, "chat_model", lambda runtime: scripted)
    return scripted


@pytest.fixture
def runner(fixture_vault, model):
    return IngestRunner.in_memory(context={"vault_path": str(fixture_vault)})


def test_start_pauses_at_the_review(runner):
    result = runner.start(NOTE)
    assert result.status == "review"
    review = result.review
    assert review.note == NOTE
    assert [(f.id, f.path) for f in review.files] == [
        ("f1", "00_raw/daily/2026-10-12.md"),
        ("f2", "05_generated/inbox.md"),
        ("f3", "30_decisions/2026/decisions-2026-q4.md"),
        ("f4", "90_meta/ingest-ledger.json"),
        ("f5", "90_meta/tasks.json"),
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


DECISION_LOG = "30_decisions/2026/decisions-2026-q4.md"
DECISION_PAGE = "30_decisions/decision-keep-the-old-bi-tool-until-q1.md"


def _file(result, path):
    (found,) = [f for f in result.review.files if f.path == path]
    return found


def test_revise_reads_the_note_again_with_the_feedback(runner, model):
    started = runner.start(NOTE)
    first_calls = len(model.calls)
    assert started.review.can_revise and started.review.revisions_left == 3

    model.answers["Meetings"] = SectionFacts(
        decisions=[Decision(line=23, title="Keep the old BI tool until Q2")]
    )
    again = runner.resume(
        started.thread_id, {"action": "revise", "feedback": "It was Q2, not Q1"}
    )
    assert again.status == "review" and again.review.revisions_left == 2
    assert len(model.calls) == 2 * first_calls  # every prose section, once more
    revise_calls = model.calls[first_calls:]  # parallel: check each, not the last
    assert all(any("It was Q2, not Q1" in m.content for m in c) for c in revise_calls)
    assert [d.title for d in again.review.decisions] == [
        "Keep the old BI tool until Q2"
    ]
    assert "Q2" in _file(again, DECISION_LOG).after_text


def test_revise_limit(runner):
    result = runner.start(NOTE)
    for _ in range(3):
        result = runner.resume(result.thread_id, {"action": "revise", "feedback": "x"})
    assert result.review.revisions_left == 0
    result = runner.resume(result.thread_id, {"action": "revise", "feedback": "x"})
    assert result.review.error.startswith("revise limit reached (3)")


def test_make_page_reroutes_without_the_model(runner, model):
    started = runner.start(NOTE)
    calls = len(model.calls)
    again = runner.resume(
        started.thread_id, {"action": "make_page", "decisions": ["d1"]}
    )
    assert len(model.calls) == calls
    assert again.review.decisions[0].page
    assert _file(again, DECISION_PAGE).change == "new"
    assert "- **Page:** [[decision-keep-the-old-bi-tool-until-q1]]" in (
        _file(again, DECISION_LOG).after_text
    )


def test_edit_replaces_one_page_and_approval_covers_the_edited_text(runner, model):
    started = runner.start(NOTE)
    calls = len(model.calls)
    log = _file(started, DECISION_LOG)
    mine = log.after_text.replace("Not recorded in the note.", "Budget freeze.")
    edited = runner.resume(
        started.thread_id, {"action": "edit", "file": log.id, "text": mine}
    )
    assert len(model.calls) == calls
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
    # LangGraph's step limit (25) counts per invoke, not per thread: each resume is
    # one round, so a long review never hits it. 12 edits ≈ 31 steps in total.
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
