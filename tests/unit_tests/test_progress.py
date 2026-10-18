"""Stream parts → steps (spec 2026-10-18): pure, on recorded part sequences.

Parts are LangGraph v2 stream parts, {"type", "ns", "data"}. The server's come as
dicts and lists, the local graph's as objects and tuples; both give one result.
"""

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from llm_wiki.domain.review import ReviewAnswer
from llm_wiki.progress import advance, answer_plan, follow, settle, step_plan


def part(kind, data, ns=()):
    return {"type": kind, "ns": ns, "data": data}


def states(progress):
    return {s.key: s.state for s in progress.steps}


def run(kind, parts):
    progress = step_plan(kind) if kind in ("ingest", "ask") else answer_plan(kind)
    for p in parts:
        progress = advance(progress, p)
    return progress


INGEST = [
    part("updates", {"load_note": {"note_path": "x"}}),
    part("updates", {"assign_ids": {}}),
    part("updates", {"track_tasks": {}}),
    part("updates", {"tools": {"messages": [ToolMessage("ok", tool_call_id="1")]}}, ns=("stage:abc",)),
    part("updates", {"tools": {"messages": [{"type": "tool"}, {"type": "tool"}]}}, ns=["stage:abc"]),
    part("custom", {"step": "stage", "detail": "facts filed: 3", "state": "done"}),
    part("updates", {"stage": {}}),
    part("updates", {"map_tasks": {}}),
    part("updates", {"render": {}}),
    part("updates", {"__interrupt__": ({"value": {}},)}),
]  # fmt: skip


def test_the_plan_starts_with_the_first_step_running():
    progress = step_plan("ingest")
    assert [s.label for s in progress.steps] == [
        "Read note", "Task IDs", "Tasks", "Agent filing facts", "Projects", "Checks",
        "Review ready",
    ]  # fmt: skip
    assert states(progress)["load_note"] == "running"
    assert set(list(states(progress).values())[1:]) == {"pending"}


def test_ingest_steps_tick_and_the_review_is_ready_once_settled():
    progress = settle(run("ingest", INGEST), "review")
    assert set(states(progress).values()) == {"done"}
    stage = progress.step("stage")
    assert (stage.calls, stage.detail) == (3, "facts filed: 3")


def test_a_finished_step_starts_the_next_one():
    progress = run("ingest", INGEST[:2])
    assert states(progress)["assign_ids"] == "done"
    assert states(progress)["track_tasks"] == "running"


def test_inner_agent_updates_only_count_tool_calls():
    progress = run("ingest", INGEST[:5])
    assert progress.step("stage").calls == 3
    assert states(progress)["stage"] == "running"


def test_an_unchanged_note_settles_as_skipped():
    progress = settle(run("ingest", INGEST[:1]), "skipped")
    assert states(progress)["load_note"] == "done"
    assert {states(progress)[k] for k in ("assign_ids", "review")} == {"skipped"}


def test_settle_never_leaves_a_step_running():
    progress = settle(answer_plan("approve"), "done")  # e.g. it failed: apply never ran
    assert set(states(progress).values()) == {"skipped"}


def test_approve_reports_apply_and_the_commit():
    progress = settle(
        run("approve", [
            part("updates", {"review": {}}),
            part("custom", {"step": "commit", "detail": "commit e9c695b", "state": "done"}),
            part("updates", {"apply": {"written": ["a.md"]}}),
        ]),
        "done",
    )  # fmt: skip
    assert states(progress) == {"apply": "done", "commit": "done"}
    assert progress.step("commit").detail == "commit e9c695b"


def token(text, node="model", ns=()):
    return part("messages", (AIMessageChunk(text), {"langgraph_node": node}), ns)


def test_answer_text_streams_from_the_top_level_model_only():
    progress = run("ask", [
        token("Atlas "),
        token("ignored", ns=("task:1",)),  # a subagent
        token("ignored", node="tools"),
        part("messages", [{"type": "AIMessageChunk", "content": "beta"}, {"langgraph_node": "model"}]),
    ])  # fmt: skip
    assert progress.text == "Atlas beta"
    assert states(progress) == {"search": "skipped", "answer": "running"}


def test_text_before_a_tool_call_is_dropped():
    progress = settle(
        run("ask", [
            token("Let me look. "),
            part("updates", {"model": {"messages": [AIMessage("Let me look. ", tool_calls=[{"name": "grep", "args": {}, "id": "1"}])]}}),
            part("updates", {"tools": {"messages": [ToolMessage("hit", tool_call_id="1")]}}),
            token("Atlas is "),
            token("on track."),
            part("updates", {"model": {"messages": [{"type": "ai", "content": "Atlas is on track.", "tool_calls": []}]}}),
        ]),
        "done",
    )  # fmt: skip
    assert progress.text == "Atlas is on track."
    assert states(progress) == {"search": "done", "answer": "done"}
    assert progress.step("search").calls == 1


def test_content_blocks_count_as_text():
    progress = run("ask", [
        part("messages", (AIMessageChunk([{"type": "text", "text": "Hi"}]), {"langgraph_node": "model"})),
    ])  # fmt: skip
    assert progress.text == "Hi"


def test_follow_yields_only_changes_and_returns_the_last():
    parts = [INGEST[0], part("updates", {"unknown_node": {}}), INGEST[1]]
    gen = follow(step_plan("ingest"), parts)
    seen = []
    try:
        while True:
            seen.append(next(gen))
    except StopIteration as stop:
        last = stop.value
    assert len(seen) == 2 and last == seen[-1]


def test_a_revise_inside_an_answer_run_changes_nothing_it_has_no_step_for():
    # a revise reruns `stage` during a review answer: the answer plan has no such step
    progress = run("approve", [INGEST[3], INGEST[6]])
    assert progress == answer_plan("approve")


@pytest.mark.parametrize(
    ("answer", "keys"),
    [
        ("approve", ["apply", "commit"]),
        ({"action": "approve", "reject": ["f2"]}, ["apply", "commit"]),
        ("reject", ["review"]),
        (ReviewAnswer(action="revise", feedback="Q2"), ["stage", "map_tasks", "render", "review"]),
        ({"action": "answer", "wins": ["w1"]}, ["pick_wins", "map_tasks", "render", "review"]),
        ({"action": "edit", "file": "f1", "text": "x"}, ["render", "review"]),
        ({"action": "nonsense"}, ["review"]),  # the review explains it and asks again
    ],
)  # fmt: skip
def test_an_answer_plan_follows_the_action(answer, keys):
    progress = answer_plan(answer)
    assert [s.key for s in progress.steps] == keys
    assert progress.steps[0].state == "running"


def test_a_revise_shows_the_agent_again_and_the_new_review_last():
    # the resumed `review` node reports first: its step must wait for the new review
    first = run("revise", [part("updates", {"review": {"feedback": ["Q2"]}})])
    assert states(first)["review"] == "pending"
    progress = settle(
        run("revise", [
            part("updates", {"review": {"feedback": ["Q2"]}}),
            INGEST[3],
            part("custom", {"step": "stage", "detail": "facts filed: 1", "state": "done"}),
            part("updates", {"stage": {}}),
            part("updates", {"map_tasks": {}}),
            part("updates", {"render": {}}),
            INGEST[-1],
        ]),
        "review",
    )  # fmt: skip
    assert set(states(progress).values()) == {"done"}
    assert progress.step("stage").calls == 1
