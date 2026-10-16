"""The `Backend` contract (decision 0010): every backend must pass these tests.

`make(vault)` returns a backend on `vault`; backends made by one `make` share their
thread store, as the review page and the chat do. Local runs always; the server
backend joins in S2 (opt-in, against `langgraph dev`).
"""

import shutil

import pytest
from langchain_core.messages import AIMessage

from llm_wiki.backend import NoOpenReview, NoteNotFound, UnknownThread
from llm_wiki.backend import local as local_backend
from llm_wiki.backend.local import LocalBackend
from llm_wiki.ingest import nodes

from .scripted_model import AgentRounds, ToolScript, call

NOTE = "00_raw/daily/2026-10-12.md"
LOG = "30_decisions/2026/decisions-2026-q4.md"


@pytest.fixture
def ingest_model(monkeypatch):
    decision = call(
        "file_fact",
        target="decision-log",
        section="Decisions",
        text="Not recorded in the note.",
        line=23,
        title="Keep the old BI tool until Q1",
    )
    monkeypatch.setattr(nodes, "chat_model", AgentRounds([decision]))


@pytest.fixture
def assistant_inputs(monkeypatch):
    """Answer every question with "Atlas is on track."; record what the model saw."""
    seen: list[list] = []

    class Recording(ToolScript):
        def _generate(self, messages, *args, **kwargs):  # type: ignore[override]
            seen.append(list(messages))
            return super()._generate(messages, *args, **kwargs)

    monkeypatch.setattr(
        local_backend,
        "configured_model",
        lambda model: Recording(messages=iter([AIMessage("Atlas is on track.")])),
    )
    monkeypatch.setattr(local_backend, "configured_fallbacks", lambda: [])
    return seen


@pytest.fixture(params=["local"])
def make(request, tmp_path, ingest_model):
    store = tmp_path / "checkpoints.sqlite"
    if request.param == "local":
        return lambda vault: LocalBackend.sqlite(vault, path=store)
    raise NotImplementedError(request.param)


@pytest.fixture
def backend(make, vault_copy):
    return make(vault_copy)


def test_start_pauses_at_a_review_that_holds_everything_the_page_shows(backend):
    started = backend.start_ingest(NOTE)
    assert started.status == "review"
    review = started.review
    assert review.note == NOTE and review.error is None
    assert LOG in {f.path for f in review.files}
    (fact,) = [f for f in review.facts if f.path == LOG]
    assert (fact.section, fact.line, fact.text) == (
        "## Decisions",
        23,
        "Not recorded in the note.",
    )
    assert fact.quote == "Decided to keep the old BI tool until Q1 #decision"
    assert str(review.note_date) == "2026-10-12"
    assert review.task_ids_added > 0
    assert backend.review(started.thread_id) == started


def test_pending_lists_open_reviews_newest_first(backend):
    first = backend.start_ingest(NOTE)
    second = backend.start_ingest(NOTE)
    assert [r.thread_id for r in backend.pending()] == [
        second.thread_id,
        first.thread_id,
    ]
    backend.answer(first.thread_id, "reject")
    assert [r.thread_id for r in backend.pending()] == [second.thread_id]


def test_approve_writes_once_then_nothing_is_waiting(backend):
    started = backend.start_ingest(NOTE)
    done = backend.answer(started.thread_id, "approve")
    assert (done.status, done.action) == ("done", "approve")
    assert NOTE in done.written and LOG in done.written
    assert "^t-" in (backend.vault / NOTE).read_text()
    assert backend.review(started.thread_id) == done
    assert backend.pending() == []
    with pytest.raises(NoOpenReview):
        backend.answer(started.thread_id, "approve")


def test_reject_writes_nothing(backend):
    before = (backend.vault / NOTE).read_text()
    started = backend.start_ingest(NOTE)
    done = backend.answer(started.thread_id, "reject")
    assert (done.status, done.action, done.written) == ("done", "reject", ())
    assert (backend.vault / NOTE).read_text() == before


def test_a_bad_answer_comes_back_as_the_same_review_with_an_error(backend):
    started = backend.start_ingest(NOTE)
    again = backend.answer(started.thread_id, {"action": "approve", "reject": ["f99"]})
    assert again.status == "review"
    assert again.review.error.startswith("unknown file ids ['f99']")
    assert [f.digest for f in again.review.files] == [
        f.digest for f in started.review.files
    ]


def test_an_unchanged_note_is_skipped(backend):
    started = backend.start_ingest(NOTE)
    backend.answer(started.thread_id, "approve")
    again = backend.start_ingest(NOTE)
    assert again.status == "skipped"
    assert any("already ingested and unchanged" in w for w in again.warnings)


def test_a_missing_note_is_refused(backend):
    with pytest.raises(NoteNotFound, match="2030-01-01"):
        backend.start_ingest("00_raw/daily/2030-01-01.md")
    assert backend.pending() == []


def test_unknown_threads_are_refused(backend):
    with pytest.raises(UnknownThread):
        backend.review("ingest-nope")
    with pytest.raises(UnknownThread):
        backend.answer("ingest-nope", "approve")


def test_another_vaults_threads_are_unknown(make, vault_copy, tmp_path):
    other_vault = tmp_path / "other"
    shutil.copytree(vault_copy, other_vault)
    mine, theirs = make(vault_copy), make(other_vault)
    started = theirs.start_ingest(NOTE)
    assert mine.pending() == []
    with pytest.raises(UnknownThread):
        mine.review(started.thread_id)
    with pytest.raises(UnknownThread):
        mine.answer(started.thread_id, "approve")
    assert theirs.review(started.thread_id).status == "review"


def test_ask_answers_with_the_earlier_chat(backend, assistant_inputs):
    history = [("user", "What is Atlas?"), ("assistant", "A risk analytics project.")]
    answer = backend.ask("Is it on track?", history)
    assert answer == "Atlas is on track."
    texts = [m.content for m in assistant_inputs[0]]
    assert texts[-3:] == [
        "What is Atlas?",
        "A risk analytics project.",
        "Is it on track?",
    ]
