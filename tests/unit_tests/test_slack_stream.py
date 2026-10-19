"""One streaming reply: steps tick, text streams, the result ends it."""

from llm_wiki.progress import step_plan
from llm_wiki.slack.stream import Reply

from .slack_fake import FakeSlack


def reply(slack):
    return Reply(slack, channel="D1", thread_ts="1.0", team="T1", user="U1")


def with_state(progress, **states):
    return progress.model_copy(
        update={
            "steps": tuple(
                s.model_copy(update={"state": states.get(s.key, s.state)})
                for s in progress.steps
            )
        }
    )


def test_steps_stream_then_the_result_stops_it():
    slack = FakeSlack()
    r = reply(slack)
    plan = step_plan("ingest")
    r.show(plan)
    r.show(with_state(plan, load_note="done", assign_ids="running"))
    ts = r.finish("**Review: n**", blocks=[{"type": "actions", "elements": []}])
    assert slack.methods()[0] == "chat.startStream"
    assert slack.methods()[-1] == "chat.stopStream"
    start = slack.calls[0][1]
    assert (start["channel"], start["thread_ts"]) == ("D1", "1.0")
    assert start["task_display_mode"] == "plan"
    assert {"load_note", "assign_ids"} <= {c["id"] for c in slack.chunks()}
    assert slack.calls[-1][1]["blocks"] and "**Review: n**" in slack.text()
    assert ts == "100.1"


def test_answer_text_streams_as_it_grows_and_is_not_repeated():
    slack = FakeSlack()
    r = reply(slack)
    ask = step_plan("ask")
    for text in ("Atlas", "Atlas beta", "Atlas beta moved."):
        r.show(ask.model_copy(update={"text": text}))
    r.finish("Atlas beta moved.")
    assert slack.text().count("Atlas beta moved.") == 1


def test_an_answer_that_restarts_after_a_tool_call_streams_without_repeats():
    # a preamble, the tool step resets the text to "", then the answer streams token by token
    slack = FakeSlack()
    r = reply(slack)
    ask = step_plan("ask")
    steps = [
        "Let me check.",
        "",
        "7",
        "7 tasks",
        "7 tasks:",
        "7 tasks: a",
        "7 tasks: a b",
    ]
    for text in steps:
        r.show(ask.model_copy(update={"text": text}))
    r.finish("7 tasks: a b")
    assert slack.text().count("7 tasks") == 1
    assert slack.text() == "Let me check.\n\n7 tasks: a b"


def test_an_expired_stream_finishes_with_chat_update():
    slack = FakeSlack()
    r = reply(slack)
    r.show(step_plan("ingest"))
    slack.fail_next["chat.stopStream"] = "message_not_in_streaming_state"
    r.finish("**Review: n**", blocks=[{"type": "actions", "elements": []}])
    method, args = slack.calls[-1]
    assert method == "chat.update" and args["ts"] == "100.1"
    assert args["blocks"][-1]["type"] == "actions"


def test_a_finish_without_progress_posts_one_message():
    slack = FakeSlack()
    reply(slack).finish("No review is open.")
    assert slack.methods() == ["chat.postMessage"]
    assert "No review is open." in slack.text()


def test_fail_marks_running_steps_as_errors():
    slack = FakeSlack()
    r = reply(slack)
    r.show(step_plan("ingest"))
    r.fail("Can't reach the wiki server: down")
    stop = slack.calls[-1][1]
    assert any(c.get("status") == "error" for c in stop["chunks"])
    assert "Can't reach the wiki server" in slack.text()
