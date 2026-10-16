"""The 0004 pipeline graph through the runner (pinned explicitly; deleted in cut-over step 3)."""

import json
import shutil

import pytest

from llm_wiki.domain.facts import (
    Decision,
    Learning,
    NameScan,
    PersonMention,
    PersonNote,
    SectionFacts,
)
from llm_wiki.ingest import nodes
from llm_wiki.ingest.graph import builder as pipeline
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
def runner(vault_copy, model):
    return IngestRunner.in_memory(
        context={"vault_path": str(vault_copy)}, graph_builder=pipeline
    )


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
    runner = IngestRunner.in_memory(
        context={"vault_path": str(vault)}, graph_builder=pipeline
    )
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


# --- questions: people and wins (3c-2) ----------------------------------------------

NAMES_NOTE = "00_raw/daily/2026-10-14.md"
NAMES_TEXT = (
    "---\ndate: 2026-10-14\n---\n"  # 1-3
    "## Tasks\n"  # 4
    "- [ ] Report changes for Eugene\n"  # 5
    "- [x] Risk culture docs and prod ticket\n"  # 6
    "- [x] Fix login redirect for [[project-atlas]]\n"  # 7
    "## Meetings\n"  # 8
    "### 1:1 with Joanna\n"  # 9
    "- Joanna wants a demo of the AI use cases\n"  # 10
    "- [ ] Give heat maps to Joanna\n"  # 11
)


@pytest.fixture
def names_runner(tmp_path, fixture_vault, monkeypatch):
    vault = tmp_path / "vault"
    shutil.copytree(fixture_vault, vault)
    (vault / NAMES_NOTE).write_text(NAMES_TEXT)
    model = BySection(
        {
            "Meetings": SectionFacts(
                person_notes=[
                    PersonNote(line=10, person="Joanna", note="Wants an AI demo.")
                ]
            )
        },
        SectionFacts(),
        names=NameScan(
            people=[
                PersonMention(name="Eugene", lines=[5]),
                PersonMention(name="Joanna", lines=[9, 11]),
            ]
        ),
    )
    monkeypatch.setattr(nodes, "chat_model", lambda runtime: model)
    runner = IngestRunner.in_memory(
        context={"vault_path": str(vault)}, graph_builder=pipeline
    )
    return runner, model


def _tasks(result):
    text = _file(result, "90_meta/tasks.json").diff
    added = "".join(
        line[1:] + "\n"
        for line in text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    return {t["line"]: t for t in json.loads(added)["tasks"].values()}


def test_unknown_names_and_done_tasks_become_questions(names_runner):
    runner, model = names_runner
    started = runner.start(NAMES_NOTE)
    review = started.review
    assert [(p.id, p.name, p.lines, p.context) for p in review.people] == [
        ("p1", "Eugene", (5,), "Report changes for Eugene"),
        ("p2", "Joanna", (9, 10, 11), "### 1:1 with Joanna"),
    ]
    assert [(w.id, w.line, w.project, w.chosen) for w in review.wins] == [
        ("w1", 6, None, False),
        ("w2", 7, "project-atlas", False),
    ]
    assert len(model.name_calls) == 1


def test_answer_makes_pages_links_tasks_and_adds_wins_without_the_model(names_runner):
    runner, model = names_runner
    started = runner.start(NAMES_NOTE)
    calls = (len(model.calls), len(model.name_calls))
    answer = {"action": "answer", "people": {"p1": "new", "p2": "new"}, "wins": ["w2"]}
    again = runner.resume(started.thread_id, answer)

    assert (len(model.calls), len(model.name_calls)) == calls  # no model call
    review = again.review
    assert review.people == ()  # answered names are known now
    assert [w.chosen for w in review.wins] == [False, True]
    paths = [f.path for f in review.files]
    assert "05_generated/inbox.md" not in paths  # Joanna's note found her page
    eugene = _file(again, "20_people/person-eugene.md")
    assert eugene.change == "new" and "aliases:\n- Eugene\n" in eugene.after_text
    joanna = _file(again, "20_people/person-joanna.md").after_text
    assert "## Notes\n- 2026-10-14 · 1:1 with Joanna: Wants an AI demo." in joanna
    tasks = _tasks(again)
    assert (tasks[5]["people"], tasks[11]["people"]) == (
        ["person-eugene"],
        ["person-joanna"],
    )
    impact = _file(again, "40_impact/2026/impact-2026-q4.md").after_text
    assert "Fix login redirect for [[project-atlas]]" in impact
    assert "Risk culture" not in impact


def test_answer_with_an_existing_slug_adds_an_alias(names_runner):
    runner, _ = names_runner
    started = runner.start(NAMES_NOTE)
    again = runner.resume(
        started.thread_id, {"action": "answer", "people": {"p1": "person-sam-ortiz"}}
    )
    assert [p.name for p in again.review.people] == ["Joanna"]
    sam = _file(again, "20_people/person-sam-ortiz.md").after_text
    assert "- Eugene\n" in sam.split("---")[1]
    assert _tasks(again)[5]["people"] == ["person-sam-ortiz"]


def test_ignored_and_unknown_answers(names_runner):
    runner, _ = names_runner
    started = runner.start(NAMES_NOTE)
    bad = runner.resume(
        started.thread_id, {"action": "answer", "people": {"p1": "person-nobody"}}
    )
    assert bad.review.error.startswith("p1: 'person-nobody' is not a person page")
    again = runner.resume(
        started.thread_id, {"action": "answer", "people": {"p1": "ignore"}}
    )
    assert [p.name for p in again.review.people] == ["Joanna"]
    assert all("eugene" not in f.path for f in again.review.files)
