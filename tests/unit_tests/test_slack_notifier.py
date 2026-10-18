"""The notifier: one DM per review opened elsewhere; buttons cleared when it's answered elsewhere."""

import threading

import pytest
from langchain_core.messages import AIMessage

from llm_wiki.backend import BackendError
from llm_wiki.backend import local as local_backend
from llm_wiki.backend.local import LocalBackend
from llm_wiki.ingest import nodes
from llm_wiki.slack.announced import Announced
from llm_wiki.slack.notifier import Notifier
from llm_wiki.slack.render import APPROVE

from .scripted_model import AgentRounds, ToolScript, bi_tool_decision
from .slack_fake import FakeSlack

USER = "U0USER"
NOTE = "00_raw/daily/2026-10-12.md"


@pytest.fixture
def setup(vault_copy, monkeypatch, tmp_path):
    monkeypatch.setattr(nodes, "chat_model", AgentRounds([bi_tool_decision()]))
    monkeypatch.setattr(
        local_backend,
        "configured_model",
        lambda model: ToolScript(messages=iter([AIMessage("ok")])),
    )
    monkeypatch.setattr(local_backend, "configured_fallbacks", lambda: [])
    backend = LocalBackend.in_memory(vault_copy)
    announced = Announced(tmp_path / "slack-announced.json")
    notifier = Notifier(
        backend, announced, user_id=USER, review_url="https://mac.tail.ts.net:8443"
    )
    return backend, announced, notifier


def posts(slack):
    return [a for m, a in slack.calls if m == "chat.postMessage"]


def updates(slack):
    return [a for m, a in slack.calls if m == "chat.update"]


def buttons(args):
    return [
        e["action_id"]
        for b in args.get("blocks") or []
        if b["type"] == "actions"
        for e in b["elements"]
    ]


def test_a_review_opened_elsewhere_is_announced_once(setup, tmp_path):
    backend, announced, notifier = setup
    result = backend.start_ingest(NOTE)
    slack = FakeSlack()
    notifier.run_once(slack)
    notifier.run_once(slack)
    (post,) = posts(slack)
    assert post["channel"] == "D1" and "thread_ts" not in post
    assert APPROVE in buttons(post)
    assert "waiting" in post["text"]
    assert announced.get(result.thread_id).seen == result.review.token
    # a restart reads the file: still one DM
    again = Notifier(
        backend,
        Announced(tmp_path / "slack-announced.json"),
        user_id=USER,
        review_url=None,
    )
    again.run_once(slack)
    assert len(posts(slack)) == 1


def test_a_review_answered_elsewhere_loses_its_buttons(setup):
    backend, announced, notifier = setup
    result = backend.start_ingest(NOTE)
    slack = FakeSlack()
    notifier.run_once(slack)
    backend.answer(result.thread_id, {"action": "reject"})
    notifier.run_once(slack)
    (update,) = updates(slack)
    assert update["channel"] == "D1" and update["ts"] == "100.1"
    assert buttons(update) == []
    assert "Answered elsewhere" in update["text"]
    assert announced.get(result.thread_id) is None
    notifier.run_once(slack)
    assert len(updates(slack)) == 1


def test_an_edit_on_the_page_updates_the_message_in_place(setup):
    backend, announced, notifier = setup
    result = backend.start_ingest(NOTE)
    slack = FakeSlack()
    notifier.run_once(slack)
    page = next(f for f in result.review.files if f.path.startswith("30_decisions/"))
    edited = backend.answer(
        result.thread_id, {"action": "edit", "file": page.id, "text": "# Edited\n"}
    )
    assert edited.status == "review" and edited.review.token != result.review.token
    notifier.run_once(slack)
    assert len(posts(slack)) == 1
    (update,) = updates(slack)
    assert APPROVE in buttons(update)
    assert edited.review.token in str(update["blocks"])
    assert announced.get(result.thread_id).seen == edited.review.token


def test_a_deleted_message_is_forgotten(setup):
    backend, announced, notifier = setup
    result = backend.start_ingest(NOTE)
    slack = FakeSlack()
    notifier.run_once(slack)
    backend.answer(result.thread_id, {"action": "reject"})
    slack.fail_next["chat.update"] = "message_not_found"
    notifier.run_once(slack)
    assert announced.get(result.thread_id) is None
    assert len(posts(slack)) == 1


def test_a_server_outage_clears_nothing(setup, monkeypatch, caplog):
    backend, announced, notifier = setup
    result = backend.start_ingest(NOTE)
    slack = FakeSlack()
    notifier.run_once(slack)

    def down():
        raise BackendError("connection refused")

    monkeypatch.setattr(backend, "pending", down)
    notifier.run_once(slack)
    assert updates(slack) == []
    assert announced.get(result.thread_id) is not None
    assert "connection refused" in caplog.text


def test_no_pass_while_a_run_is_busy(setup):
    backend, announced, notifier = setup
    backend.start_ingest(NOTE)
    slack = FakeSlack()
    with announced.busy():
        notifier.run_once(slack)
    assert slack.calls == []


def test_the_loop_survives_a_failing_pass(setup, monkeypatch):
    backend, announced, notifier = setup
    stop = threading.Event()
    calls = []

    def boom(client):
        calls.append(1)
        if len(calls) == 2:
            stop.set()
        raise RuntimeError("bad pass")

    monkeypatch.setattr(notifier, "run_once", boom)
    notifier.run_forever(FakeSlack(), stop, interval=0)
    assert len(calls) == 2


def test_an_open_review_missing_from_pending_is_kept(setup, monkeypatch):
    # pending() is capped, and a review being answered is "running", not listed
    backend, announced, notifier = setup
    result = backend.start_ingest(NOTE)
    slack = FakeSlack()
    notifier.run_once(slack)
    monkeypatch.setattr(backend, "pending", lambda: [])
    notifier.run_once(slack)
    assert updates(slack) == []
    assert announced.get(result.thread_id) is not None


def test_a_slack_error_other_than_gone_keeps_the_record_and_retries(setup):
    backend, announced, notifier = setup
    result = backend.start_ingest(NOTE)
    slack = FakeSlack()
    notifier.run_once(slack)
    backend.answer(result.thread_id, {"action": "reject"})
    slack.fail_next["chat.update"] = "ratelimited"
    notifier.run_once(slack)
    assert announced.get(result.thread_id) is not None  # still to clear
    notifier.run_once(slack)
    assert announced.get(result.thread_id) is None
    assert buttons(updates(slack)[-1]) == []


def test_a_failed_refresh_does_not_announce_the_review_again(setup):
    backend, announced, notifier = setup
    result = backend.start_ingest(NOTE)
    slack = FakeSlack()
    notifier.run_once(slack)
    page = next(f for f in result.review.files if f.path.startswith("30_decisions/"))
    edited = backend.answer(
        result.thread_id, {"action": "edit", "file": page.id, "text": "# Edited\n"}
    )
    slack.fail_next["chat.update"] = "ratelimited"
    notifier.run_once(slack)
    assert len(posts(slack)) == 1
    assert announced.get(result.thread_id).seen == result.review.token
    notifier.run_once(slack)  # the retry works
    assert announced.get(result.thread_id).seen == edited.review.token


def test_one_failing_thread_does_not_stop_the_pass(setup, monkeypatch):
    from llm_wiki.slack.announced import Shown

    backend, announced, notifier = setup
    announced.record("ingest-ghost", Shown(channel="D1", ts="9.9", seen="x"))
    real = backend.review

    def flaky(thread):
        if thread == "ingest-ghost":
            raise BackendError("500")
        return real(thread)

    monkeypatch.setattr(backend, "review", flaky)
    result = backend.start_ingest(NOTE)
    slack = FakeSlack()
    notifier.run_once(slack)
    assert len(posts(slack)) == 1  # the real review was still announced
    assert announced.get("ingest-ghost") is not None  # retried next pass
    assert announced.get(result.thread_id) is not None


def test_a_button_press_never_waits_for_a_slow_pass(setup, monkeypatch):
    backend, announced, notifier = setup
    backend.start_ingest(NOTE)
    release, in_call, pressed = threading.Event(), threading.Event(), threading.Event()

    class Slow(FakeSlack):
        def chat_postMessage(self, **kwargs):
            in_call.set()
            release.wait(5)
            return super().chat_postMessage(**kwargs)

    worker = threading.Thread(target=notifier.run_once, args=(Slow(),))
    worker.start()
    assert in_call.wait(2)

    def press():
        with announced.busy():
            pressed.set()

    threading.Thread(target=press).start()
    got_in = pressed.wait(1)
    release.set()
    worker.join(5)
    assert got_in, "a handler run waited for the notifier's slow Slack call"
