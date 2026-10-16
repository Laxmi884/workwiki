"""The review page (0009 R1) and chat box (0010 S3) on a `Backend`."""

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
        assert f'value="{f.id}" checked' in page and f.path in page
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
