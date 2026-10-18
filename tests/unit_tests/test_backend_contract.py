"""The `Backend` contract (decision 0010): every backend must pass these tests.

`make(vault)` returns a backend on `vault`; backends made by one `make` share their
thread store, as the review page and the chat do. Local always runs; the server
backend runs against a `langgraph dev` this module starts, opt-in:

    WIKI_SERVER_TESTS=1 uv run pytest tests/unit_tests/test_backend_contract.py

or against the production setup: the `langgraph build` image under our `compose.yml`
(Docker, Postgres, strict serde; the first build takes minutes):

    WIKI_SERVER_TESTS=docker uv run pytest tests/unit_tests/test_backend_contract.py

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

from llm_wiki.backend import (
    BackendError,
    Finished,
    NoOpenReview,
    NoteNotFound,
    UnknownThread,
)
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


def _wait_until_up(url: str, alive, deadline_s: float, log_path: Path) -> None:
    deadline = time.monotonic() + deadline_s
    while True:
        try:
            if httpx.get(f"{url}/ok", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if not alive() or time.monotonic() > deadline:
            pytest.fail(f"server did not start:\n{log_path.read_text()[-3000:]}")
        time.sleep(0.5)


# Strict: the servers restore only LangGraph's safe types, as a future langgraph-api will
# by default. Ingest state is plain JSON (ingest/state.py), so nothing of ours is needed.
SERVER_ENV = {
    "LANGSMITH_TRACING": "false",
    "LANGGRAPH_STRICT_MSGPACK": "true",
    "WIKI_SERVER_TOKEN": "test-token",  # conftest sets the same for the clients
}


def _dev_server(work: Path):
    """`langgraph dev` in a subprocess, on a config with absolute paths."""
    config = {
        "dependencies": [str(ROOT)],  # paths are absolute: dev resolves them from cwd
        "graphs": {
            "ingest": f"{SERVER_GRAPHS}:ingest",
            "assistant": f"{SERVER_GRAPHS}:assistant",
        },
        "auth": {"path": f"{ROOT}/src/llm_wiki/server_auth.py:auth"},
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
            cwd=work,  # the server keeps .langgraph_api here
            stdout=log,
            stderr=subprocess.STDOUT,
            # the tests' vaults are under pytest's temp folder (`resolve_vault`)
            env={**os.environ, **SERVER_ENV, "WIKI_VAULT_ROOTS": str(work.parent)},
        )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_up(url, lambda: server.poll() is None, 90, log_path)
        yield url
    finally:
        server.terminate()
        server.wait(timeout=10)


# Its own compose project and image tag, so `down --volumes` never touches a real stack.
DOCKER_PROJECT = "llm-wiki-contract"
DOCKER_IMAGE = "llm-wiki:contract"


def _docker_server(work: Path, shared: Path):
    """Our `compose.yml` on a fresh `langgraph build` image, scripted graphs swapped in.

    An extra compose file overrides the image's graph list (`LANGSERVE_GRAPHS`) and
    mounts a copy of `tests/` read-only (macOS doesn't let Docker mount from
    ~/Documents). `WIKI_VAULT_PATH` is `shared`, where the tests' vaults live, so
    compose.yml mounts it at the same path and the server and the tests see one vault.
    """
    tests = shutil.copytree(
        ROOT / "tests", shared / "tests", ignore=shutil.ignore_patterns("__pycache__")
    )
    in_image = "/deps/workspace/tests/server/graphs.py"
    override = {
        "services": {
            "api": {
                "environment": {
                    "LANGSERVE_GRAPHS": json.dumps(
                        {
                            "ingest": f"{in_image}:ingest",
                            "assistant": f"{in_image}:assistant",
                        }
                    ),
                    "LANGSMITH_TRACING": "false",
                    "WIKI_VAULT_ROOTS": str(shared),  # the tests' vaults are inside
                    "WIKI_SERVER_TOKEN": "test-token",
                },
                "volumes": [f"{tests}:/deps/workspace/tests:ro"],
            }
        }
    }
    (work / "compose.override.yml").write_text(json.dumps(override))  # JSON is YAML
    port = _free_port()
    log_path = work / "server.log"
    env = {
        **os.environ,
        "WIKI_IMAGE": DOCKER_IMAGE,
        "WIKI_PORT": str(port),
        "WIKI_VAULT_PATH": str(shared),
    }
    compose = ["docker", "compose", "-p", DOCKER_PROJECT, "-f", ROOT / "compose.yml"]
    with log_path.open("w") as log:
        steps = [
            [Path(sys.executable).parent / "langgraph", "build", "-t", DOCKER_IMAGE],
            [*compose, "-f", work / "compose.override.yml", "up", "-d", "--wait"],
        ]
        for step in steps:
            done = subprocess.run(
                step,
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=900,
            )
            if done.returncode != 0:
                break
    url = f"http://127.0.0.1:{port}"
    try:
        if done.returncode != 0:
            pytest.fail(f"docker setup failed:\n{log_path.read_text()[-3000:]}")
        _wait_until_up(url, lambda: True, 60, log_path)
        yield url
    finally:
        with log_path.open("a") as log:
            for args in (["logs", "api"], ["down", "--volumes"]):
                subprocess.run(
                    [*compose, *args], stdout=log, stderr=subprocess.STDOUT, env=env
                )


@pytest.fixture(scope="session")
def server_url(tmp_path_factory):
    """Start `langgraph dev` (or the Docker stack) on the scripted graphs; stop it after."""
    mode = os.environ.get("WIKI_SERVER_TESTS")
    if not mode:
        pytest.skip(
            "server contract tests are opt-in: set WIKI_SERVER_TESTS=1 (or =docker)"
        )
    work = tmp_path_factory.mktemp("server")
    if mode == "docker":
        yield from _docker_server(work, tmp_path_factory.getbasetemp().resolve())
    else:
        yield from _dev_server(work)


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


def test_the_server_refuses_a_client_without_the_token(request):
    # fetched here, not as a parameter: a session fixture named in the signature
    # starts before the autouse env fixture, and the server would miss WIKI_VAULT_PATH
    server_url = request.getfixturevalue("server_url")
    assert httpx.post(f"{server_url}/threads/search", json={}).status_code == 401
    ok = httpx.post(
        f"{server_url}/threads/search",
        json={},
        headers={"Authorization": "Bearer test-token"},
    )
    assert ok.status_code == 200


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


def drain(events):
    """Return (progress events, the Finished event); exactly one Finished, at the end."""
    *steps, last = list(events)
    assert isinstance(last, Finished)
    assert not any(isinstance(e, Finished) for e in steps)
    return steps, last


def states(progress):
    return {s.key: s.state for s in progress.steps}


def test_streamed_ingest_ends_where_start_ingest_ends(backend):
    steps, finished = drain(backend.stream_ingest(NOTE))
    assert states(steps[0])["load_note"] == "running"
    assert finished.result.status == "review"
    assert set(states(finished.progress).values()) == {"done"}
    stage = finished.progress.step("stage")
    assert stage.calls >= 1 and stage.detail == "facts filed: 1"
    assert backend.review(finished.result.thread_id) == finished.result
    blocking = backend.start_ingest(
        NOTE
    )  # the ledger diff carries a time: compare facts
    assert finished.result.review.facts == blocking.review.facts


def test_streamed_approve_writes_and_reports_the_commit(backend):
    _, started = drain(backend.stream_ingest(NOTE))
    _, done = drain(backend.stream_answer(started.result.thread_id, "approve"))
    assert (done.result.status, done.result.action) == ("done", "approve")
    assert NOTE in done.result.written
    assert states(done.progress)["apply"] == "done"
    commit = done.progress.step("commit")  # the vault copy is not a git repo
    assert (commit.state, commit.detail) == ("skipped", "not committed")


def test_streamed_reject_closes_the_review_and_writes_nothing(backend):
    _, started = drain(backend.stream_ingest(NOTE))
    _, done = drain(backend.stream_answer(started.result.thread_id, "reject"))
    assert done.result.action == "reject"
    assert states(done.progress) == {"review": "done"}  # no apply / commit step


def test_streaming_a_missing_note_is_refused(backend):
    with pytest.raises(NoteNotFound, match="2030-01-01"):
        list(backend.stream_ingest("00_raw/daily/2030-01-01.md"))
    assert backend.pending() == []


def test_streaming_an_answer_to_an_unknown_thread_is_refused(backend):
    with pytest.raises(UnknownThread):
        list(backend.stream_answer("ingest-nope", "approve"))


def test_streamed_ask_gives_the_same_answer_as_ask(backend):
    history = [("user", "What is Atlas?"), ("assistant", "A risk analytics project.")]
    _, finished = drain(backend.stream_ask("Is it on track?", history))
    expected = "What is Atlas? | A risk analytics project. | Is it on track?"
    assert finished.answer == expected == backend.ask("Is it on track?", history)
    assert finished.progress.text == expected
    assert states(finished.progress)["answer"] == "done"


def test_a_streamed_ask_that_searches_gives_the_same_text_as_ask(backend):
    # EchoModel: "Let me look." + an `ls` of the vault, then an answer in content blocks
    _, finished = drain(backend.stream_ask("search: Atlas?"))
    assert finished.progress.step("search").calls == 1
    assert states(finished.progress) == {"search": "done", "answer": "done"}
    assert (
        finished.answer == "search: Atlas? | Let me look."
    )  # the preamble isn't shown
    assert backend.ask("search: Atlas?") == finished.answer
