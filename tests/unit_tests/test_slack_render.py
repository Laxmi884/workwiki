"""Progress and results as Slack sees them: task chunks, text deltas, buttons."""

import json

from llm_wiki.backend import RunResult
from llm_wiki.domain.review import ReviewRequest
from llm_wiki.progress import Progress, Step, step_plan
from llm_wiki.slack.render import (
    APPROVE,
    MAX_MARKDOWN,
    REJECT,
    public_review_link,
    reply_text,
    review_buttons,
    task_chunks,
    text_delta,
)

REVIEW = ReviewRequest(note="00_raw/daily/2026-10-12.md", files=())
OPEN = RunResult(thread_id="ingest-abc", status="review", review=REVIEW)


def test_the_first_chunks_are_every_step_then_only_changes():
    plan = step_plan("ingest")
    first = task_chunks(None, plan)
    assert [c["id"] for c in first] == [s.key for s in plan.steps]
    assert first[0] == {
        "type": "task_update",
        "id": "load_note",
        "title": "Read note",
        "status": "in_progress",
    }
    assert {c["status"] for c in first[1:]} == {"pending"}
    done = plan.model_copy(
        update={
            "steps": (
                plan.steps[0].model_copy(update={"state": "done"}),
                *plan.steps[1:],
            )
        }
    )
    assert task_chunks(plan, done) == [
        {
            "type": "task_update",
            "id": "load_note",
            "title": "Read note",
            "status": "complete",
        }
    ]


def test_details_tool_calls_and_skips_show_on_the_step():
    step = Step(key="stage", label="Agent filing facts", state="running", calls=3)
    running = Progress(steps=(step,))
    assert task_chunks(None, running)[0]["details"] == "3 tool calls"
    skipped = Progress(
        steps=(step.model_copy(update={"state": "skipped", "calls": 0}),)
    )
    assert task_chunks(running, skipped)[0] == {
        "type": "task_update",
        "id": "stage",
        "title": "Agent filing facts",
        "status": "complete",
        "details": "skipped",
    }
    quiet = step.model_copy(update={"state": "done", "detail": "nothing new to file"})
    assert (
        task_chunks(running, Progress(steps=(quiet,)))[0]["details"]
        == "nothing new to file"
    )


def test_text_delta_appends_and_starts_over_after_a_reset():
    assert text_delta("", "Atlas") == "Atlas"
    assert text_delta("Atlas", "Atlas beta") == " beta"
    assert (
        text_delta("Let me look.", "The beta") == "\n\nThe beta"
    )  # Slack can't unsend
    assert text_delta("Atlas", "Atlas") == ""


def test_an_open_review_gets_buttons_carrying_its_token():
    (actions,) = review_buttons(OPEN, "https://mac.tail.ts.net:8443")
    assert [e.get("action_id") for e in actions["elements"]][:2] == [APPROVE, REJECT]
    value = json.loads(actions["elements"][0]["value"])
    assert value == {"thread": "ingest-abc", "seen": REVIEW.token}
    assert (
        actions["elements"][2]["url"]
        == "https://mac.tail.ts.net:8443/review/ingest-abc"
    )


def test_no_link_button_for_a_loopback_review_url():
    for local in (
        "http://127.0.0.1:2024",
        "http://localhost:8123",
        "http://[::1]:2024",
    ):
        assert public_review_link(local, "ingest-abc") is None
    (actions,) = review_buttons(OPEN, "http://127.0.0.1:2024")
    assert len(actions["elements"]) == 2


def test_finished_runs_get_no_buttons():
    done = RunResult(
        thread_id="t",
        status="done",
        action="approve",
        written=("a.md",),
        commit="e9c695b1",
    )
    assert review_buttons(done, None) == []
    assert reply_text(done, None).startswith("Applied 1 files")


def test_long_reviews_are_cut_to_slack_limits():
    many = ReviewRequest(
        note="n", files=(), warnings=tuple("w" * 200 for _ in range(200))
    )
    text = reply_text(RunResult(thread_id="t", status="review", review=many), None)
    assert len(text) <= MAX_MARKDOWN
    assert text.endswith("(cut: open the review page for the rest)")
    long_step = Step(key="stage", label="x" * 400, state="running", detail="d" * 400)
    chunk = task_chunks(None, Progress(steps=(long_step,)))[0]
    assert len(chunk["title"]) <= 256 and len(chunk["details"]) <= 256
