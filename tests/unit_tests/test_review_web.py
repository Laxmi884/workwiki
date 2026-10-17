"""The review page (0009 R1) and chat box (0010 S3) on a `Backend`."""

import json

import pytest
from starlette.testclient import TestClient

from llm_wiki.backend import BackendError
from llm_wiki.backend.local import LocalBackend
from llm_wiki.ingest import nodes
from llm_wiki.review_web import app as web

from .scripted_model import AgentRounds, call

NOTE = "00_raw/daily/2026-10-12.md"
LOG = "30_decisions/2026/decisions-2026-q4.md"


@pytest.fixture
def setup(vault_copy, monkeypatch):
    decision = call(
        "file_fact",
        target="decision-log",
        section="Decisions",
        text="Not recorded in the note.",
        line=23,
        title="Keep the old BI tool until Q1",
    )
    monkeypatch.setattr(nodes, "chat_model", AgentRounds([decision]))
    backend = LocalBackend.in_memory(vault_copy)
    monkeypatch.setattr(web, "backend", lambda: backend)
    thread = backend.start_ingest(NOTE).thread_id
    return TestClient(web.app), backend, thread, vault_copy


def test_page_shows_files_diffs_and_checkboxes(setup):
    client, backend, thread, _ = setup
    page = client.get(f"/review/{thread}").text
    review = backend.review(thread).review
    for f in review.files:
        assert f'value="{f.id}" data-path="{f.path}" checked' in page
    # the decision log card: the filed text next to the note line it cites
    assert "Decided to keep the old BI tool until Q1 #decision</mark>" in page
    assert "Not recorded in the note." in page and "Show the exact change" in page
    assert 'id="approve"' in page and 'id="reject"' in page
    listing = client.get("/review").text
    assert NOTE in listing and "Waiting for you" in listing


def test_approve_some_files_then_the_page_shows_the_result(setup):
    client, backend, thread, vault = setup
    log_id = next(f.id for f in backend.review(thread).review.files if f.path == LOG)
    res = client.post(
        f"/review/{thread}/answer",
        json={"answer": {"action": "approve", "reject": [log_id]}},
    )
    assert res.json() == {"status": "done", "error": None}
    assert not (vault / LOG).exists()  # left out
    assert "^t-" in (vault / NOTE).read_text()  # the rest written
    page = client.get(f"/review/{thread}").text
    assert "Written to your wiki" in page and NOTE in page

    again = client.post(f"/review/{thread}/answer", json={"answer": "approve"})
    assert again.status_code == 409 and "no review is waiting" in again.json()["error"]


def test_a_bad_answer_is_shown_on_the_page(setup):
    client, _, thread, _ = setup
    res = client.post(
        f"/review/{thread}/answer",
        json={"answer": {"action": "approve", "reject": ["f99"]}},
    )
    assert res.json()["status"] == "review"
    assert "unknown file ids" in res.json()["error"]
    page = client.get(f"/review/{thread}").text
    assert "wasn't accepted" in page and "unknown file ids" in page


def test_unknown_review(setup):
    client, *_ = setup
    assert "No review here" in client.get("/review/ingest-nope").text
    assert (
        client.post(
            "/review/ingest-nope/answer", json={"answer": "approve"}
        ).status_code
        == 404
    )


def test_view_pairs_each_fact_with_its_note_line(setup):
    from llm_wiki.review import note_line
    from llm_wiki.review_web.view import build_view

    _, backend, thread, _ = setup
    review = backend.review(thread).review
    view = build_view(review)
    (log,) = [c for c in view.pages if c.file.path == LOG]
    ((section, (row,)),) = log.sections
    assert (section, row.line, row.text) == (
        "Decisions",
        23,
        "Not recorded in the note.",
    )
    assert row.quote == "Decided to keep the old BI tool until Q1 #decision"
    assert view.title == "Daily note, Monday 12 October 2026"
    assert {f.path for f in view.bookkeeping} >= {NOTE, "90_meta/tasks.json"}
    assert view.task_ids == review.task_ids_added > 0
    from llm_wiki.daily_syntax import parse_daily_note

    note = parse_daily_note(NOTE, (backend.vault / NOTE).read_text())
    assert note_line(note, 8) == "Review Q3 budget draft for [[project-atlas]]"


def test_the_index_lists_only_open_reviews(setup):
    client, backend, thread, _ = setup
    assert f"/review/{thread}" in client.get("/review").text
    backend.answer(thread, "reject")
    assert "Nothing to review" in client.get("/review").text


def test_chat_box_answers_commands_and_reports_backend_failures(setup, monkeypatch):
    client, backend, thread, _ = setup
    assert 'id="text"' in client.get("/chat").text
    res = client.post("/chat/send", json={"message": "/review", "history": []})
    assert res.json()["reply"].startswith(f"**Review: {NOTE}**")
    assert f"(/review/{thread})" in res.json()["reply"]
    assert client.post("/chat/send", json={"message": "  "}).status_code == 400

    def down(*args, **kwargs):
        raise BackendError(
            "the Agent Server at http://127.0.0.1:2024 failed: ConnectError"
        )

    monkeypatch.setattr(backend, "pending", down)
    res = client.post("/chat/send", json={"message": "/review", "history": []})
    assert res.status_code == 502 and "ConnectError" in res.json()["error"]


# R2 (0009): every control is a `ReviewAnswer`; the page reloads with the new diffs.


def _answer(client, thread, answer):
    res = client.post(f"/review/{thread}/answer", json={"answer": answer})
    assert res.json() == {"status": "review", "error": None}, res.json()


def test_page_offers_task_win_edit_and_revise_controls(setup):
    client, backend, thread, _ = setup
    review = backend.review(thread).review
    page = client.get(f"/review/{thread}").text
    # one dropdown per task: "No project" plus the review's own project list
    assert [(p.slug, p.title) for p in review.projects] == [("project-atlas", "Atlas")]
    for t in review.tasks:
        assert f'data-task="{t.id}"' in page
    # one per task dropdown, plus the page list of "Remember a name"
    assert page.count('<option value="project-atlas">Atlas</option>') == (
        len(review.tasks) + 1
    )
    for w in review.wins:  # enabled now
        assert f'class="win" value="{w.id}" >' in page
    (log,) = [f for f in review.files if f.path == LOG]
    assert f'id="edit-{log.id}" data-file="{log.id}" hidden' in page
    assert "Keep the old BI tool until Q1" in page  # its full text, to edit
    assert 'id="revise"' in page and "3 revisions left" in page
    assert "const edited = [];" in page


def test_a_task_answer_from_the_page_updates_the_task_index(setup):
    client, backend, thread, _ = setup
    task = backend.review(thread).review.tasks[0]
    _answer(client, thread, {"action": "answer", "tasks": {task.id: "project-atlas"}})
    review = backend.review(thread).review
    (again,) = [t for t in review.tasks if t.id == task.id]
    assert (again.project, again.by) == ("project-atlas", "user")
    index = next(f for f in review.files if f.path == "90_meta/tasks.json")
    assert '"mapped_by": "user"' in index.diff
    page = client.get(f"/review/{thread}").text
    assert '<option value="project-atlas" selected>Atlas</option>' in page
    assert "Your choice" in page

    res = client.post(  # a project that isn't in the list is refused, and shown
        f"/review/{thread}/answer",
        json={"answer": {"action": "answer", "tasks": {task.id: "project-zed"}}},
    )
    assert "not a project page" in res.json()["error"]


def test_picking_a_win_from_the_page_adds_the_impact_page(setup):
    client, backend, thread, _ = setup
    win = backend.review(thread).review.wins[0]
    _answer(client, thread, {"action": "answer", "wins": [win.id]})
    review = backend.review(thread).review
    assert [w.chosen for w in review.wins] == [True, False]
    impact = next(f for f in review.files if f.path.startswith("40_impact/"))
    assert win.text in impact.after_text
    page = client.get(f"/review/{thread}").text
    assert f'class="win" value="{win.id}" checked>' in page
    assert "Counted as a win" in page

    _answer(client, thread, {"action": "answer", "wins": []})  # and back
    assert not any(
        f.path.startswith("40_impact/") for f in backend.review(thread).review.files
    )


def test_an_edit_from_the_page_is_marked_and_later_answers_warn(setup):
    client, backend, thread, _ = setup
    (log,) = [f for f in backend.review(thread).review.files if f.path == LOG]
    text = log.after_text.replace(
        "Not recorded in the note.", "Until Q1, then revisit."
    )
    _answer(client, thread, {"action": "edit", "file": log.id, "text": text})
    review = backend.review(thread).review
    (log,) = [f for f in review.files if f.path == LOG]
    assert review.edited == (LOG,) and "Until Q1, then revisit." in log.after_text
    page = client.get(f"/review/{thread}").text
    assert "decisions, your edit" in page
    assert 'const edited = ["Decisions 2026 Q4"];' in page  # the page asks first


def test_revise_from_the_page_runs_another_round(setup):
    client, backend, thread, _ = setup
    _answer(client, thread, {"action": "revise", "feedback": "Also note the beta date"})
    review = backend.review(thread).review
    assert review.revisions_left == 2
    assert "2 revisions left" in client.get(f"/review/{thread}").text


# --- remember (0009 R3) ------------------------------------------------------------

ATLAS = "10_projects/project-atlas.md"
RULES = "90_meta/rules.md"


def test_remembered_rule_and_alias_are_proposed_then_written_on_approve(setup):
    client, backend, thread, vault = setup
    before = backend.review(thread).review
    timeline = next(t for t in before.tasks if "stakeholders" in t.text)
    assert "project-atlas" in [p.slug for p in before.pages]

    _answer(
        client,
        thread,
        {
            "action": "answer",
            "rules": ["Don't file lunch plans"],
            "aliases": {"stakeholders": "project-atlas"},
        },
    )
    review = backend.review(thread).review
    assert review.rules == ("Don't file lunch plans",)
    files = {f.path: f for f in review.files}
    assert files[RULES].change == "new"
    assert files[RULES].after_text.endswith("- Don't file lunch plans\n")
    # one line changes: the name is added in the page's own list style
    assert (
        "+aliases: [Atlas, atlas beta, BI migration, stakeholders]\n"
        in files[ATLAS].diff
    )
    assert files[ATLAS].diff.count("\n+") == 2  # the +++ header and that line
    # the alias maps the task in this review already (rule 3), so it isn't asked
    assert timeline.line not in [t.line for t in review.tasks]
    assert '"project": "project-atlas"' in files["90_meta/tasks.json"].diff

    page = client.get(f"/review/{thread}").text
    assert "Remember for next time" in page
    assert "<span>Don&#39;t file lunch plans</span>" in page
    assert "“stakeholders” means Atlas" in page
    assert "Also known as <strong>stakeholders</strong>" in page
    assert "The rules you asked to remember" in page

    res = client.post(f"/review/{thread}/answer", json={"answer": "approve"})
    assert res.json()["status"] == "done"
    assert (vault / RULES).read_text().endswith("## Rules\n- Don't file lunch plans\n")
    assert "aliases: [Atlas, atlas beta, BI migration, stakeholders]" in (
        (vault / ATLAS).read_text()
    )
    index = json.loads((vault / "90_meta/tasks.json").read_text())["tasks"]
    (record,) = [t for t in index.values() if "stakeholders" in t["text"]]
    assert (record["project"], record["mapped_by"]) == ("project-atlas", "alias")


def test_removing_what_was_remembered_drops_its_files(setup):
    client, backend, thread, _ = setup
    _answer(client, thread, {"action": "answer", "rules": ["Skip lunch plans"]})
    _answer(client, thread, {"action": "answer", "rules": []})
    review = backend.review(thread).review
    assert review.rules == () and RULES not in [f.path for f in review.files]


def test_an_alias_another_page_has_is_refused(setup):
    client, _, thread, _ = setup
    res = client.post(
        f"/review/{thread}/answer",
        json={"answer": {"action": "answer", "aliases": {"Sam": "project-atlas"}}},
    )
    assert "'Sam' already names person-sam-ortiz" in res.json()["error"]


def test_revise_can_also_remember_the_feedback(setup):
    client, backend, thread, _ = setup
    page = client.get(f"/review/{thread}").text
    assert 'name="remember"' in page  # the "also remember" tick
    _answer(
        client,
        thread,
        {"action": "revise", "feedback": "Skip lunch plans", "remember": True},
    )
    review = backend.review(thread).review
    assert review.rules == ("Skip lunch plans",)
    assert RULES in [f.path for f in review.files]
