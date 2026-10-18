"""DM messages and buttons over a local backend: scripted models, fake Slack."""

import json

import pytest
from langchain_core.messages import AIMessage

from llm_wiki.backend import BackendError
from llm_wiki.backend import local as local_backend
from llm_wiki.backend.local import LocalBackend
from llm_wiki.ingest import nodes
from llm_wiki.slack.handlers import WikiSlack
from llm_wiki.slack.render import APPROVE, REJECT

from .scripted_model import AgentRounds, EchoModel, ToolScript, bi_tool_decision
from .slack_fake import FakeSlack

USER = "U0USER"
NOTE = "00_raw/daily/2026-10-12.md"


@pytest.fixture
def bot(vault_copy, monkeypatch):
    monkeypatch.setattr(nodes, "chat_model", AgentRounds([bi_tool_decision()]))
    monkeypatch.setattr(
        local_backend,
        "configured_model",
        lambda model: ToolScript(
            messages=iter([AIMessage("Atlas beta moved to Oct 20.")])
        ),
    )
    monkeypatch.setattr(local_backend, "configured_fallbacks", lambda: [])
    backend = LocalBackend.in_memory(vault_copy)
    wiki = WikiSlack(backend, user_id=USER, review_url="https://mac.tail.ts.net:8443")
    return wiki, backend


def dm(text, user=USER, **extra):
    return {
        "type": "message",
        "channel_type": "im",
        "channel": "D1",
        "user": user,
        "text": text,
        "ts": "1.0",
        "team": "T1",
        **extra,
    }


def test_only_the_allowed_user_in_a_dm_is_answered(bot):
    wiki, backend = bot
    for event in (
        dm("/ingest 2026-10-12", user="U0OTHER"),
        dm("/ingest 2026-10-12", channel_type="channel"),
    ):
        slack = FakeSlack()
        wiki.on_message(event, slack)
        assert slack.calls == []
    assert backend.pending() == []


@pytest.mark.parametrize(
    "extra",
    [
        {"subtype": "message_changed"},
        {"bot_id": "B1"},
        {"subtype": "channel_join"},
        {"subtype": "file_share"},
        {"text": "   "},
    ],
)
def test_events_that_are_not_the_users_text_are_ignored(bot, extra):
    wiki, backend = bot
    slack = FakeSlack()
    wiki.on_message(dm("/ingest 2026-10-12") | extra, slack)
    assert slack.calls == [] and backend.pending() == []


def test_ingest_streams_steps_then_the_review_with_buttons(bot):
    wiki, backend = bot
    slack = FakeSlack()
    wiki.on_message(dm("/ingest 2026-10-12"), slack)
    assert slack.methods()[0] == "chat.startStream"
    assert {"load_note", "stage", "review"} <= {c["id"] for c in slack.chunks()}
    (actions,) = slack.calls[-1][1]["blocks"]
    assert [e["action_id"] for e in actions["elements"]][:2] == [APPROVE, REJECT]
    (review,) = backend.pending()
    assert json.loads(actions["elements"][0]["value"])["thread"] == review.thread_id
    assert f"**Review: {NOTE}**" in slack.text()


def test_a_question_streams_its_answer(bot):
    wiki, _ = bot
    slack = FakeSlack()
    wiki.on_message(dm("When is the Atlas beta?"), slack)
    assert "Atlas beta moved to Oct 20." in slack.text()
    assert slack.calls[0][1]["thread_ts"] == "1.0"  # a reply under the user's message


def test_a_backend_failure_is_said_and_the_bot_stays_up(bot, monkeypatch):
    wiki, backend = bot

    def down(*a, **k):
        raise BackendError("the Agent Server at http://api:8000 failed: ConnectError")

    monkeypatch.setattr(backend, "pending", down)
    slack = FakeSlack()
    wiki.on_message(dm("/review"), slack)
    assert "Can't reach the wiki server: the Agent Server" in slack.text()


def test_an_unexpected_error_is_reported_without_details(bot, monkeypatch):
    wiki, backend = bot

    def boom(*a, **k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(backend, "pending", boom)
    slack = FakeSlack()
    wiki.on_message(dm("/review"), slack)
    assert "Something failed (details in the log)" in slack.text()
    assert "secret detail" not in slack.text()


def press(slack, wiki, action_id, value, user=USER, blocks=None):
    body = {
        "user": {"id": user},
        "channel": {"id": "D1"},
        "team": {"id": "T1"},
        "message": {
            "ts": "100.1",
            "thread_ts": "1.0",
            "blocks": blocks or [{"type": "actions", "elements": []}],
        },
        "actions": [{"action_id": action_id, "value": value}],
    }
    wiki.on_action(body, slack)


def opened(wiki):
    slack = FakeSlack()
    wiki.on_message(dm("/ingest 2026-10-12"), slack)
    (actions,) = slack.calls[-1][1]["blocks"]
    return actions["elements"][0]["value"]


def test_approve_writes_and_the_buttons_are_replaced(bot, vault_copy):
    wiki, backend = bot
    value = opened(wiki)
    before = (vault_copy / NOTE).read_text()
    slack = FakeSlack()
    press(slack, wiki, APPROVE, value)
    method, args = slack.calls[0]
    assert method == "chat.update" and args["text"] == "Approving…"  # no double press
    assert all(b["type"] != "actions" for b in args["blocks"])
    assert {"apply", "commit"} <= {c["id"] for c in slack.chunks()}
    assert "Applied" in slack.text()
    assert backend.pending() == [] and (vault_copy / NOTE).read_text() != before


def test_a_second_press_says_already_answered(bot):
    wiki, _ = bot
    value = opened(wiki)
    press(FakeSlack(), wiki, REJECT, value)
    slack = FakeSlack()
    press(slack, wiki, APPROVE, value)
    assert "already answered" in slack.text()
    assert "Done. Nothing was written." in slack.text()


def test_a_button_on_a_changed_review_is_refused(bot, vault_copy):
    wiki, backend = bot
    value = opened(wiki)
    before = (vault_copy / NOTE).read_text()
    stale = json.dumps(
        json.loads(value) | {"seen": "0" * 16}
    )  # e.g. revised on the page
    slack = FakeSlack()
    press(slack, wiki, APPROVE, stale)
    assert "changed since you opened it" in slack.text()
    assert len(backend.pending()) == 1 and (vault_copy / NOTE).read_text() == before


def test_a_backend_failure_restores_the_buttons(bot, monkeypatch):
    wiki, backend = bot
    value = opened(wiki)

    def down(*a, **k):
        raise BackendError("the Agent Server at http://api:8000 failed: ConnectError")

    monkeypatch.setattr(backend, "stream_answer", down)
    slack = FakeSlack()
    original = [{"type": "actions", "elements": [{"action_id": APPROVE}]}]
    press(slack, wiki, APPROVE, value, blocks=original)
    updates = [a for m, a in slack.calls if m == "chat.update"]
    assert updates[-1]["blocks"] == original
    assert "Can't reach the wiki server" in slack.text()


def test_presses_from_another_user_do_nothing(bot):
    wiki, backend = bot
    value = opened(wiki)
    slack = FakeSlack()
    press(slack, wiki, APPROVE, value, user="U0OTHER")
    assert slack.calls == [] and len(backend.pending()) == 1


def test_a_busy_review_is_said(bot):
    wiki, backend = bot
    value = opened(wiki)
    running = backend.stream_answer(json.loads(value)["thread"], "reject")
    next(running)
    slack = FakeSlack()
    press(slack, wiki, APPROVE, value)
    assert "already being answered" in slack.text()
    list(running)


def test_a_button_for_an_unknown_thread_is_said(bot):
    # e.g. the server's threads were wiped: the press must still get an answer
    wiki, _ = bot
    slack = FakeSlack()
    press(slack, wiki, APPROVE, json.dumps({"thread": "ingest-gone", "seen": "0" * 16}))
    assert "isn't open any more" in slack.text()


@pytest.mark.parametrize("typed", ["!ingest 2026-10-12", " /ingest 2026-10-12"])
def test_commands_can_be_typed_past_slacks_own_slash_commands(bot, typed):
    # Slack's client takes "/ingest" as one of its own commands and never sends it
    wiki, backend = bot
    wiki.on_message(dm(typed), FakeSlack())
    assert len(backend.pending()) == 1


def test_a_press_keeps_the_summary_and_ends_with_the_outcome(bot):
    # only the buttons go: the review text stays readable in the DM
    wiki, _ = bot
    value = opened(wiki)
    summary = {"type": "markdown", "text": "**Review: 00_raw/daily/2026-10-12.md**"}
    buttons = {"type": "actions", "elements": [{"action_id": APPROVE}]}
    slack = FakeSlack()
    press(slack, wiki, REJECT, value, blocks=[summary, buttons])
    updates = [a for m, a in slack.calls if m == "chat.update" and a["ts"] == "100.1"]
    assert updates[0]["blocks"][0] == summary and "Rejecting…" in str(
        updates[0]["blocks"]
    )
    assert updates[-1]["blocks"][0] == summary
    assert "Nothing was written" in str(updates[-1]["blocks"])
    assert all(b["type"] != "actions" for u in updates for b in u["blocks"])


@pytest.fixture
def echo_bot(vault_copy, monkeypatch):
    """The assistant replies with the turns it was sent ("q1 | a1 | q2")."""
    monkeypatch.setattr(local_backend, "configured_model", lambda model: EchoModel())
    monkeypatch.setattr(local_backend, "configured_fallbacks", lambda: [])
    backend = LocalBackend.in_memory(vault_copy)
    return WikiSlack(backend, user_id=USER, review_url=None)


def said(ts, text, user=USER):
    return {"type": "message", "user": user, "text": text, "ts": ts, "thread_ts": "1.0"}


def from_bot(ts, text):
    return {
        "type": "message",
        "user": "U0BOT",
        "bot_id": "B0BOT",
        "text": text,
        "ts": ts,
        "thread_ts": "1.0",
    }


def in_thread(text, ts="5.0"):
    return dm(text, ts=ts, thread_ts="1.0")


def test_a_reply_in_a_thread_carries_the_threads_earlier_turns(echo_bot):
    slack = FakeSlack()
    slack.replies = [
        said("1.0", "What is Atlas?"),
        from_bot("2.0", "A risk analytics project."),
        said("5.0", "Is it on track?"),  # the message being answered: not history
    ]
    echo_bot.on_message(in_thread("Is it on track?"), slack)
    (fetch,) = [a for m, a in slack.calls if m == "conversations.replies"]
    assert (fetch["channel"], fetch["ts"]) == ("D1", "1.0")
    assert (
        "What is Atlas? | A risk analytics project. | Is it on track?" in slack.text()
    )


def test_a_top_level_message_starts_a_new_conversation(echo_bot):
    slack = FakeSlack()
    echo_bot.on_message(dm("Is it on track?"), slack)
    assert "conversations.replies" not in slack.methods()
    assert "Is it on track?" in slack.text() and " | " not in slack.text()


def test_commands_reviews_and_other_users_are_not_history(echo_bot):
    slack = FakeSlack()
    slack.replies = [
        said("1.0", "!ingest 2026-10-12"),
        from_bot("2.0", "**Review: 00_raw/daily/2026-10-12.md** …"),
        said("3.0", "spam", user="U0OTHER"),
        said("3.5", "What is Atlas?"),
        from_bot("4.0", "A project."),
    ]
    echo_bot.on_message(in_thread("Who owns it?"), slack)
    assert "What is Atlas? | A project. | Who owns it?" in slack.text()
    assert "Review" not in slack.text() and "spam" not in slack.text()


def test_only_the_last_turns_are_kept(echo_bot):
    slack = FakeSlack()
    slack.replies = [
        said(f"{i}.0", f"q{i}") if i % 2 else from_bot(f"{i}.0", f"a{i}")
        for i in range(1, 31)
    ]
    echo_bot.on_message(in_thread("last", ts="99.0"), slack)
    shown = slack.text()
    assert "q11 | a12" in shown and "a10 |" not in shown  # 20 turns: 11..30


def test_a_thread_slack_wont_return_is_answered_without_history(echo_bot):
    slack = FakeSlack()
    slack.fail_next["conversations.replies"] = "ratelimited"
    echo_bot.on_message(in_thread("Is it on track?"), slack)
    assert "Is it on track?" in slack.text()


def notifier_for(wiki, backend):
    from llm_wiki.slack.notifier import Notifier

    return Notifier(backend, wiki.announced, user_id=USER, review_url=None)


def test_a_review_started_in_slack_is_not_announced_again(bot):
    wiki, backend = bot
    opened(wiki)
    (result,) = backend.pending()
    assert wiki.announced.get(result.thread_id).seen == result.review.token
    slack = FakeSlack()
    notifier_for(wiki, backend).run_once(slack)
    assert slack.calls == []


def test_a_review_answered_by_button_is_not_cleared_again(bot):
    wiki, backend = bot
    value = opened(wiki)
    (result,) = backend.pending()
    press(FakeSlack(), wiki, REJECT, value)
    assert wiki.announced.get(result.thread_id) is None
    slack = FakeSlack()
    notifier_for(wiki, backend).run_once(slack)
    assert slack.calls == []


def test_a_refused_press_records_the_reply_with_current_buttons(bot):
    wiki, backend = bot
    value = opened(wiki)
    (result,) = backend.pending()
    first = wiki.announced.get(result.thread_id)
    stale = json.dumps(json.loads(value) | {"seen": "0" * 16})
    slack = FakeSlack()
    slack._ts = 5  # its replies get other ts values than the first message's
    press(slack, wiki, APPROVE, stale)
    shown = wiki.announced.get(result.thread_id)
    assert (
        shown is not None and shown.ts != first.ts
    )  # the new reply, not the pressed one
