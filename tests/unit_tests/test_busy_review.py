"""I4 (whole-project review 2026-10-18): one answer at a time per review.

The review page and the chat share one backend in one process; two answers to one
thread at once could both pass the "is a review waiting?" check and resume it twice.
"""

from types import SimpleNamespace

import httpx
import pytest
from langgraph_sdk.errors import ConflictError

from llm_wiki.backend import NoOpenReview, ReviewBusy
from llm_wiki.backend.local import LocalBackend
from llm_wiki.backend.server import ServerBackend
from llm_wiki.ingest import nodes

from .scripted_model import AgentRounds, bi_tool_decision

NOTE = "00_raw/daily/2026-10-12.md"


def test_a_second_answer_while_one_runs_is_refused(vault_copy, monkeypatch):
    monkeypatch.setattr(nodes, "chat_model", AgentRounds([bi_tool_decision()]))
    backend = LocalBackend.in_memory(vault_copy)
    thread = backend.start_ingest(NOTE).thread_id
    running = backend.stream_answer(thread, "approve")
    next(running)  # the approval has started
    with pytest.raises(ReviewBusy, match="already being answered"):
        backend.answer(thread, "reject")
    with pytest.raises(ReviewBusy):
        list(backend.stream_answer(thread, "reject"))
    *_, finished = running
    assert finished.result.action == "approve"  # the first answer won, once
    with pytest.raises(NoOpenReview):  # the lock is free again; the review is closed
        backend.answer(thread, "reject")


def test_an_abandoned_stream_frees_the_review(vault_copy, monkeypatch):
    monkeypatch.setattr(nodes, "chat_model", AgentRounds([bi_tool_decision()]))
    backend = LocalBackend.in_memory(vault_copy)
    thread = backend.start_ingest(NOTE).thread_id
    stream = backend.stream_answer(thread, "reject")
    next(stream)
    stream.close()  # e.g. the surface went away before the first step
    assert backend.answer(thread, "reject").action == "reject"


def test_the_servers_409_is_a_busy_review(tmp_path):
    def conflict(*args, **kwargs):
        request = httpx.Request("POST", "http://server/threads/t/runs/wait")
        response = httpx.Response(409, request=request)
        raise ConflictError("busy", response=response, body=None)

    thread = {"metadata": {"vault_path": str(tmp_path.resolve())}}
    state = {
        "values": {},
        "interrupts": [{"value": {"note": NOTE, "files": []}}],
        "next": ["review"],
    }
    client = SimpleNamespace(
        threads=SimpleNamespace(get=lambda t: thread, get_state=lambda t: state),
        runs=SimpleNamespace(wait=conflict, stream=conflict),
    )
    backend = ServerBackend("http://server", tmp_path, client=client)  # type: ignore[arg-type]
    with pytest.raises(ReviewBusy):
        backend.answer("t", "approve")
    with pytest.raises(ReviewBusy):
        list(backend.stream_answer("t", "approve"))


def test_surfaces_say_the_review_is_busy(vault_copy, monkeypatch):
    from starlette.testclient import TestClient

    from llm_wiki.chat.session import reply
    from llm_wiki.review_web import app as web

    monkeypatch.setattr(nodes, "chat_model", AgentRounds([bi_tool_decision()]))
    backend = LocalBackend.in_memory(vault_copy)
    monkeypatch.setattr(web, "backend", lambda: backend)
    thread = backend.start_ingest(NOTE).thread_id
    running = backend.stream_answer(thread, "approve")
    next(running)
    assert "already being answered" in reply(backend, "/reject")
    token = backend.review(thread).review.token
    res = TestClient(web.app).post(
        f"/review/{thread}/answer",
        json={"answer": {"action": "approve", "seen": token}},
    )
    assert res.status_code == 409 and "already being answered" in res.json()["error"]
    list(running)
