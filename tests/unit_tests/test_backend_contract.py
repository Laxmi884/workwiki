"""The `Backend` contract (decision 0010): every backend must pass these tests.

`make(vault)` returns a backend on `vault`; backends made by one `make` share their
thread store, as the review page and the chat do. Local always runs; the server
backend runs against a `langgraph dev` this module starts, opt-in:

    WIKI_SERVER_TESTS=1 uv run pytest tests/unit_tests/test_backend_contract.py

The server loads `tests/server/graphs.py` (the real graphs, scripted models), so
neither backend calls a model.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from llm_wiki.backend import BackendError, NoOpenReview, NoteNotFound, UnknownThread
from llm_wiki.backend import local as local_backend
from llm_wiki.backend.local import LocalBackend
from llm_wiki.backend.server import ServerBackend
from llm_wiki.ingest import nodes

from .scripted_model import AgentRounds, EchoModel, bi_tool_decision

NOTE = "00_raw/daily/2026-10-12.md"
LOG = "30_decisions/2026/decisions-2026-q4.md"
ROOT = Path(__file__).resolve().parents[2]
SERVER_GRAPHS = ROOT / "tests" / "server" / "graphs.py"


@pytest.fixture
def scripted_models(monkeypatch):
    """The same scripted models `tests/server/graphs.py` gives the server."""
    monkeypatch.setattr(nodes, "chat_model", AgentRounds([bi_tool_decision()]))
    monkeypatch.setattr(local_backend, "configured_model", lambda model: EchoModel())
    monkeypatch.setattr(local_backend, "configured_fallbacks", lambda: [])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server_url(tmp_path_factory):
    """Start `langgraph dev` on the scripted graphs; stop it after the session."""
    if not os.environ.get("WIKI_SERVER_TESTS"):
        pytest.skip("server contract tests are opt-in: set WIKI_SERVER_TESTS=1")
    work = tmp_path_factory.mktemp(
        "server"
    )  # cwd: the server keeps .langgraph_api here
    config = {
        "dependencies": [str(ROOT)],  # paths are absolute: dev resolves them from cwd
        "graphs": {
            "ingest": f"{SERVER_GRAPHS}:ingest",
            "assistant": f"{SERVER_GRAPHS}:assistant",
        },
    }
    (work / "langgraph.json").write_text(json.dumps(config))
    port = _free_port()
    log_path = work / "server.log"
    with log_path.open("w") as log:
        server = subprocess.Popen(
            [
                Path(sys.executable).parent / "langgraph",
                "dev",
                "--config",
                work / "langgraph.json",
                "--port",
                str(port),
                "--no-browser",
                "--no-reload",
            ],
            cwd=work,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "LANGSMITH_TRACING": "false"},
        )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 90
    while True:
        try:
            if httpx.get(f"{url}/ok", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if server.poll() is not None or time.monotonic() > deadline:
            server.kill()
            pytest.fail(f"langgraph dev did not start:\n{log_path.read_text()[-3000:]}")
        time.sleep(0.5)
    yield url
    server.terminate()
    server.wait(timeout=10)


@pytest.fixture(params=["local", "server"])
def make(request, tmp_path, scripted_models):
    if request.param == "server":
        url = request.getfixturevalue("server_url")
        return lambda vault: ServerBackend(url, vault)
    store = tmp_path / "checkpoints.sqlite"
    return lambda vault: LocalBackend.sqlite(vault, path=store)


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


def test_ask_answers_with_the_earlier_chat(backend):
    history = [("user", "What is Atlas?"), ("assistant", "A risk analytics project.")]
    answer = backend.ask("Is it on track?", history)
    # EchoModel replies with the user and assistant turns it was sent
    assert answer == "What is Atlas? | A risk analytics project. | Is it on track?"


def test_an_unreachable_server_is_a_backend_error(vault_copy):
    from langgraph_sdk.client import SyncLangGraphClient

    url = f"http://127.0.0.1:{_free_port()}"
    # the SDK's own client retries a refused connection 5 times; this one fails at once
    client = SyncLangGraphClient(httpx.Client(base_url=url))
    backend = ServerBackend(url, vault_copy, client=client)
    with pytest.raises(BackendError, match="failed: ConnectError"):
        backend.pending()
