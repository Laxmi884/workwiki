# Slack adapter step 1: streaming progress and the agent skip — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every backend can stream a run as named steps (and an answer as streamed text), the chat can stream its reply, and a quiet daily note (rollover-only or tick-only) makes no model call.

**Architecture:** Both backends turn LangGraph's v2 stream parts (`{"type", "ns", "data"}`; locally `graph.stream(..., subgraphs=True, version="v2")`, on the server `client.runs.stream(..., stream_subgraphs=True, version="v2")`) into one frozen `Progress` value through a pure `progress.advance`. The graph says how its steps went through `runtime.stream_writer` (`custom` mode). New `stream_*` methods yield `Progress` and end with one `Finished` event carrying the value the blocking method returns. `chat.session.reply` becomes a drain of the new `stream_reply`.

**Where this differs from the spec, on purpose:**
- The live tool-call count during `stage` comes from the inner agent's own `updates` parts (its `tools` node, under the `stage:` namespace). `custom` events only carry each step's final tally (`facts filed: n`). Emitting `custom` from inside the agent's tools would touch every tool for no gain.
- `reply()` drains `stream_reply()` instead of staying a separate copy of the routing. That keeps the spec's "same routing and rules" by construction, and the parametrised chat tests prove the text is unchanged.
- The agent skip drops the spec's second condition ("no open tasks needing a project"). A rollover note carries yesterday's open tasks, so most quiet days would still pay for the agent. The agent already proposed a project for each task on the day it was new; the review keeps asking the user about tasks still without one (`review.task_questions`), and that needs no model. The skip rule is `staging.needs_agent`: run the agent when a citable line is not a known task with the same words.
- Progress for a review answer follows the action (`progress.answer_plan`): approve shows apply and commit, revise shows the agent again, answer/edit show the review being rebuilt, reject shows the review closing. The spec's table lists only the approve case. Added after the step-1 review (2026-10-18).

**Tech Stack:** Python 3.12, LangGraph 1.2.15, langgraph-sdk 0.4.7 (its `runs.stream` takes `version="v2"` and yields `{"type", "ns", "data"}` dicts), langgraph-api 0.16.1, Pydantic v2, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-10-18-slack-adapter-design.md` (build order step 1).

## Global Constraints

- Frozen Pydantic models; missing values stay `None` (CLAUDE.md).
- Code decides, the model writes: whether the agent runs is a code rule, never a model call.
- Surfaces reach graphs only through `Backend`; they never import httpx, langgraph_sdk, the runner or graphs (0010 rule 3). Surfaces catch `BackendError` only.
- Blocking methods (`start_ingest`, `answer`, `ask`) keep their behaviour; the CLI, the review page and every existing test keep passing.
- Tests use scripted models and the fixture vault (`vault_copy`), never the real vault, never a paid model.
- Sync core (0014): sync generators, no asyncio.
- Stream modes: ingest `["updates", "custom"]`; ask `["updates", "messages"]` locally, `["updates", "messages-tuple"]` on the server.
- Commits: each task ends with a walkthrough; commit only after the user says yes (memory: commit-after-walkthrough). Trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Whole suite: `uv run pytest -q` (711 passing before this plan).

## Review Focus

1. **A quiet note with a new task added** — a rollover note where the user wrote one new `- [ ] …` line must still run the agent (the new task has no ID yet). Test: `test_a_new_task_on_a_rollover_note_still_runs_the_agent` (Task 1).
2. **Revise on a quiet note** — it must be refused visibly by the review, never silently skipped. Test: `test_revise_on_a_quiet_note_is_refused_not_skipped` (Task 1).
3. **A reject or a failed apply leaves no step "running"** — no spinner forever: unfinished steps become `skipped`. Tests: `test_settle_never_leaves_a_step_running` (Task 2), `test_streamed_reject_leaves_nothing_running` (Task 3).
4. **The server stream's error part** — a missing note streamed on the server must raise `NoteNotFound` (and delete the new thread), not end quietly. Test: `test_streaming_a_missing_note_is_refused` (Task 3, both backends).
5. **Text the model writes before a tool call** — "Let me look…", a search, then the answer: the final text must be the answer only. Test: `test_text_before_a_tool_call_is_dropped` (Task 2).

---

## File Structure

```
src/llm_wiki/
  progress.py              NEW  Step, Progress, step_plan, advance, follow, settle (pure)
  ingest/staging.py        + needs_agent(note, citable)
  ingest/nodes.py          + report(runtime, step, detail, state); apply reports the commit
  ingest/graph.py          stage: quiet-note skip; reports its tally
  runner.py                + new_thread_id, stream_start, stream_resume (start/resume share checks)
  backend/base.py          + Finished, StreamEvent; Protocol gains stream_ingest/answer/ask
  backend/__init__.py      exports Finished, StreamEvent
  backend/local.py         + stream_ingest, stream_answer, stream_ask
  backend/server.py        + _reachable_stream, stream_*; shared failure handling
  chat/session.py          + ChatReply, stream_reply; reply() drains it
tests/unit_tests/
  test_quiet_notes.py      NEW  agent skip + the graph's custom events
  test_progress.py         NEW  advance/settle on recorded part sequences
  test_backend_contract.py + streaming contract tests (local always, server opt-in)
  test_chat.py             chat fixture runs every test through reply and stream_reply
notebooks/8.3_streaming.ipynb   NEW (git-ignored)
docs/wiki/                 log, progress, learnings/streaming-progress.md, index
```

---

### Task 1: Quiet notes skip the agent; the graph reports its steps

**Files:**
- Modify: `src/llm_wiki/ingest/staging.py` (add `needs_agent` after `citable_lines`, ~line 167)
- Modify: `src/llm_wiki/ingest/nodes.py` (add `report`; call it in `apply`, ~line 423)
- Modify: `src/llm_wiki/ingest/graph.py` (`stage`, ~lines 88–190)
- Test: `tests/unit_tests/test_quiet_notes.py` (new)

**Interfaces:**
- Produces: `staging.needs_agent(note: DailyNote, citable: Mapping[int, str]) -> bool`
- Produces: `nodes.report(runtime: Any, step: str, detail: str, state: str = "done") -> None` writes `{"step", "detail", "state"}` to the `custom` stream. It's a no-op when the runtime has no writer (unit tests pass a `SimpleNamespace`).
- Produces custom events:
  - `{"step": "stage", "detail": "nothing new to file", "state": "done"}`
  - `{"step": "stage", "detail": "facts filed: <n>", "state": "done"}`
  - `{"step": "commit", "detail": "commit <sha7>", "state": "done"}` or `{"step": "commit", "detail": "not committed", "state": "skipped"}`
- Produces: on a skipped round, `agent_stats["skipped"] == "nothing new to file"` and `agent_stats["model_calls"] == 0`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit_tests/test_quiet_notes.py`:

```python
"""Quiet notes (spec 2026-10-18, "Agent skip"): no model call when nothing is new.

A rollover-only note holds only carried tasks, each with an ID already; a tick-only
edit changes only task lines that have IDs. Task tracking (code) records both, and
done tasks reach the review as wins; the agent has nothing to file, so `stage`
doesn't build it.
"""

import datetime

from langgraph.types import Command

from llm_wiki.ingest import nodes
from llm_wiki.ingest.graph import builder as agent_builder
from llm_wiki.rollover import roll_over
from llm_wiki.runner import IngestRunner

from .scripted_model import AgentRounds, bi_tool_decision

NOTE = "00_raw/daily/2026-10-12.md"
DAY = datetime.date(2026, 10, 14)


def _runner(vault, monkeypatch):
    rounds = AgentRounds([bi_tool_decision()])
    monkeypatch.setattr(nodes, "chat_model", rounds)
    runner = IngestRunner.in_memory(
        context={"vault_path": str(vault)}, graph_builder=agent_builder
    )
    return runner, rounds


def _values(runner, thread_id):
    return runner.graph.get_state({"configurable": {"thread_id": thread_id}}).values


def _ingested(runner):
    started = runner.start(NOTE)
    runner.resume(started.thread_id, "approve")


def test_a_rollover_only_note_runs_no_model(vault_copy, monkeypatch):
    runner, rounds = _runner(vault_copy, monkeypatch)
    _ingested(runner)
    rolled = roll_over(vault_copy, DAY, commit=False)
    assert rolled.created and rolled.carried

    result = runner.start(rolled.path)

    assert len(rounds.briefs) == 1, "only the first note's agent round"
    assert result.status == "review"
    stats = _values(runner, result.thread_id)["agent_stats"]
    assert (stats["skipped"], stats["model_calls"]) == ("nothing new to file", 0)


def test_a_tick_only_edit_runs_no_model_and_offers_the_win(vault_copy, monkeypatch):
    runner, rounds = _runner(vault_copy, monkeypatch)
    _ingested(runner)
    path = vault_copy / NOTE
    text = path.read_text()
    ticked = text.replace("- [ ] Review Q3 budget draft", "- [x] Review Q3 budget draft")
    assert ticked != text
    path.write_text(ticked)

    result = runner.start(NOTE)

    assert len(rounds.briefs) == 1
    assert any("Review Q3 budget draft" in w.text for w in result.review.wins)


def test_a_new_task_on_a_rollover_note_still_runs_the_agent(vault_copy, monkeypatch):
    runner, rounds = _runner(vault_copy, monkeypatch)
    _ingested(runner)
    rolled = roll_over(vault_copy, DAY, commit=False)
    note = vault_copy / rolled.path
    text = note.read_text()
    assert "## Tasks\n- [ ] \n" in text  # the template's empty task line
    note.write_text(text.replace("## Tasks\n- [ ] \n", "## Tasks\n- [ ] Call Sam\n"))

    runner.start(rolled.path)

    assert len(rounds.briefs) == 2


def test_revise_on_a_quiet_note_is_refused_not_skipped(vault_copy, monkeypatch):
    runner, rounds = _runner(vault_copy, monkeypatch)
    _ingested(runner)
    rolled = roll_over(vault_copy, DAY, commit=False)
    started = runner.start(rolled.path)

    again = runner.resume(
        started.thread_id, {"action": "revise", "feedback": "look again"}
    )

    assert len(rounds.briefs) == 1
    assert again.status == "review" and again.review.error


def test_the_graph_reports_steps_on_the_custom_stream(vault_copy, monkeypatch):
    runner, _ = _runner(vault_copy, monkeypatch)
    config = {"configurable": {"thread_id": "t1"}}
    context = {"vault_path": str(vault_copy)}

    def custom(graph_input):
        return [
            p["data"]
            for p in runner.graph.stream(
                graph_input, config, context=context, stream_mode="custom", version="v2"
            )
        ]

    assert custom({"note_path": NOTE}) == [
        {"step": "stage", "detail": "facts filed: 1", "state": "done"}
    ]
    # the fixture copy is not a git repo: nothing to commit into
    assert custom(Command(resume="approve")) == [
        {"step": "commit", "detail": "not committed", "state": "skipped"}
    ]
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest tests/unit_tests/test_quiet_notes.py -v`
Expected:
- The rollover and tick tests FAIL on `len(rounds.briefs) == 1` (got 2).
- The custom-stream test FAILS (`[] != [...]`).
- The new-task test PASSES (the agent runs today).
- The revise test FAILS on `len(rounds.briefs) == 1`.

- [ ] **Step 3: Add `needs_agent`**

In `src/llm_wiki/ingest/staging.py`, right after `citable_lines` (add `Mapping` to the `collections.abc` import if it's missing):

```python
def needs_agent(note: DailyNote, citable: Mapping[int, str]) -> bool:
    """Return whether anything in the note is new for the ingest agent.

    Lines of tasks that already carry an ID (carried over by rollover, or ticked in an
    edited note) are code's: task tracking records them and their ticks, and done
    tasks reach the review as wins without a model. Any other new line (prose, a
    sub-bullet, a task without an ID yet) is news.
    """
    known = {task.line_no for task in note.tasks if task.id is not None}
    return any(line not in known for line in citable)
```

- [ ] **Step 4: Add `report` to `nodes.py`; `apply` reports the commit**

In `src/llm_wiki/ingest/nodes.py`, after `vault_path`:

```python
def report(runtime: Any, step: str, detail: str, state: str = "done") -> None:
    """Tell a streaming surface how a step went (`custom` stream mode).

    These events are the progress contract (`llm_wiki.progress`): surfaces read them,
    never node state. A no-op when nothing streams `custom`, and for the plain
    namespaces unit tests pass as a runtime.
    """
    writer = getattr(runtime, "stream_writer", None)
    if writer is not None:
        writer({"step": step, "detail": detail, "state": state})
```

In `apply`, between `commit_paths(...)` and the final `return`:

```python
    if commit:
        report(runtime, "commit", f"commit {commit[:7]}")
    else:
        report(runtime, "commit", "not committed", "skipped")
```

- [ ] **Step 5: Skip the agent in `stage`; report the tally**

In `src/llm_wiki/ingest/graph.py`, add `needs_agent` to the `staging` import. In `stage`, right after `revising = ...`:

```python
    if not revising and not needs_agent(note, citable):
        nodes.report(runtime, "stage", "nothing new to file")
        return _quiet_round(state, read)
```

Just before `stage`'s final `return`:

```python
    nodes.report(runtime, "stage", f"facts filed: {len(filed)}")
```

Below `stage`:

```python
def _quiet_round(state: AgentIngestState, read: VaultReader) -> dict[str, Any]:
    """Return `stage`'s update when the note has nothing new for the agent.

    Same keys as an agent round, all empty; picked wins still become edits (code).
    """
    return {
        "agent_edits": [],
        "fact_edits": _win_edits(state, [], read),
        "agent_filed": [],
        "task_proposals": [],
        "questions": [],
        "agent_stats": {
            "skipped": "nothing new to file",
            "seconds": 0.0,
            "model_calls": 0,
            "tool_calls": 0,
            "calls_by_tool": {},
            "refusals": 0,
            "refused": [],
            "tokens": {},
        },
        "round_warnings": [],
    }
```

In the module docstring's diagram, change `stage (ingest deep agent)` to `stage (ingest deep agent; skipped when nothing is new)`.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/unit_tests/test_quiet_notes.py -v`. Expected: all PASS.
Run: `uv run pytest -q`. Expected: all PASS.

- [ ] **Step 7: Mutation check**

Make `needs_agent` `return True`. The rollover, tick and revise tests must FAIL. Revert.
Drop `if task.id is not None` from `known`, so every task line counts as known. The new-task test must FAIL (the new task no longer wakes the agent). It's the only test that pins this guard. Revert.

- [ ] **Step 8: Walk through and commit (after the user's yes)**

```bash
git add src/llm_wiki/ingest/staging.py src/llm_wiki/ingest/nodes.py src/llm_wiki/ingest/graph.py tests/unit_tests/test_quiet_notes.py
git commit -m "Ingest: no agent run on quiet notes; graph reports steps on the custom stream"
```

---

### Task 2: `progress.py`, from stream parts to steps

**Files:**
- Create: `src/llm_wiki/progress.py`
- Test: `tests/unit_tests/test_progress.py`

**Interfaces:**
- Consumes: the custom events of Task 1.
- Produces:
  - `StepState = Literal["pending", "running", "done", "skipped"]`
  - `Step(key: str, label: str, state: StepState = "pending", detail: str | None = None, calls: int = 0)` (frozen)
  - `Progress(steps: tuple[Step, ...], text: str = "")` (frozen), with `step(key) -> Step` and `has(key) -> bool`
  - `step_plan(kind: Literal["ingest", "answer", "ask"]) -> Progress`
  - `advance(progress: Progress, part: Mapping[str, Any]) -> Progress`
  - `follow(progress: Progress, parts: Iterable[Mapping[str, Any]]) -> Generator[Progress, None, Progress]`
  - `settle(progress: Progress, status: str) -> Progress`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit_tests/test_progress.py`:

```python
"""Stream parts → steps (spec 2026-10-18): pure, on recorded part sequences.

Parts are LangGraph v2 stream parts, {"type", "ns", "data"}. The server's come as
dicts and lists, the local graph's as objects and tuples; both give one result.
"""

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from llm_wiki.progress import advance, follow, settle, step_plan


def part(kind, data, ns=()):
    return {"type": kind, "ns": ns, "data": data}


def states(progress):
    return {s.key: s.state for s in progress.steps}


def run(kind, parts):
    progress = step_plan(kind)
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
    progress = settle(step_plan("answer"), "done")  # a reject: apply never ran
    assert set(states(progress).values()) == {"skipped"}


def test_approve_reports_apply_and_the_commit():
    progress = settle(
        run("answer", [
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
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest tests/unit_tests/test_progress.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'llm_wiki.progress'`.

- [ ] **Step 3: Write `progress.py`**

Create `src/llm_wiki/progress.py`:

```python
"""A run's stream as steps a person can follow (spec 2026-10-18: the stream is the contract).

    graph ──v2 stream parts──▶ advance() ──▶ Progress ──▶ Slack / chat draw it

Parts are LangGraph's v2 stream parts, `{"type", "ns", "data"}`: from
`graph.stream(..., subgraphs=True, version="v2")` locally, or the SDK's
`runs.stream(..., stream_subgraphs=True, version="v2")` (dicts and lists instead of
objects and tuples). What counts:

    updates, root      a plan step's node finished (the next one starts);
                       for `ask`, the assistant's "tools" and "model" nodes
    updates, in stage  the inner agent's "tools" node: tool calls so far
    custom             {"step", "detail", "state"} from `nodes.report`
    messages, root     the assistant's answer tokens (node "model"; subagents left out)

Pure: no I/O, frozen values; every function returns a new `Progress`.
"""

from __future__ import annotations

from collections.abc import Generator, Iterable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

StepState = Literal["pending", "running", "done", "skipped"]

PLANS: dict[str, tuple[tuple[str, str], ...]] = {
    "ingest": (
        ("load_note", "Read note"),
        ("assign_ids", "Task IDs"),
        ("track_tasks", "Tasks"),
        ("stage", "Agent filing facts"),
        ("map_tasks", "Projects"),
        ("render", "Checks"),
        ("review", "Review ready"),
    ),
    "answer": (("apply", "Writing files"), ("commit", "Git commit")),
    "ask": (("search", "Searching the wiki"), ("answer", "Writing the answer")),
}


class Step(BaseModel):
    """One line of progress.

    Attributes:
        key: The graph node (or the `custom` event's step) it follows.
        label: What a person reads.
        state: pending → running → done; skipped when the run ended without it.
        detail: What the graph said about it (`nodes.report`), e.g. "facts filed: 3".
        calls: Tool calls made inside it so far.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    state: StepState = "pending"
    detail: str | None = None
    calls: int = 0


class Progress(BaseModel):
    """Where a run stands: its steps in order, and the answer text so far (`ask`)."""

    model_config = ConfigDict(frozen=True)

    steps: tuple[Step, ...]
    text: str = ""

    def step(self, key: str) -> Step:
        """Return the step with this key (KeyError if the plan has none)."""
        for s in self.steps:
            if s.key == key:
                return s
        raise KeyError(key)

    def has(self, key: str) -> bool:
        """Return whether the plan has a step with this key."""
        return any(s.key == key for s in self.steps)


def step_plan(kind: Literal["ingest", "answer", "ask"]) -> Progress:
    """Return the steps of an ingest start, a review answer or a question; first running."""
    steps = [Step(key=k, label=label) for k, label in PLANS[kind]]
    steps[0] = steps[0].model_copy(update={"state": "running"})
    return Progress(steps=tuple(steps))


def _with(progress: Progress, key: str, **changes: Any) -> Progress:
    if not progress.has(key):
        return progress
    steps = tuple(
        s.model_copy(update=changes) if s.key == key else s for s in progress.steps
    )
    return progress.model_copy(update={"steps": steps})


def _finish(progress: Progress, key: str) -> Progress:
    """Mark `key` done and start the first pending step after it."""
    if not progress.has(key):
        return progress
    progress = _with(progress, key, state="done")
    keys = [s.key for s in progress.steps]
    for s in progress.steps[keys.index(key) + 1 :]:
        if s.state == "pending":
            return _with(progress, s.key, state="running")
    return progress


def _get(message: Any, field: str) -> Any:
    """Read a message field from an object (local) or a dict (server)."""
    if isinstance(message, Mapping):
        return message.get(field)
    return getattr(message, field, None)


def _text(content: Any) -> str:
    """Return the text of message content: a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        return "".join(
            b.get("text", "") if isinstance(b, Mapping) else str(b)
            for b in content
            if not isinstance(b, Mapping) or b.get("type") == "text"
        )
    return ""


def _messages(update: Any) -> list[Any]:
    """Return the messages a node update adds (one update, or a list of them)."""
    updates = update if isinstance(update, list) else [update]
    out: list[Any] = []
    for u in updates:
        if isinstance(u, Mapping):
            out.extend(u.get("messages") or [])
    return out


def _tools_ran(progress: Progress, key: str, update: Any) -> Progress:
    calls = progress.step(key).calls + len(_messages(update))
    return _with(progress, key, state="running", calls=calls)


def _root_update(progress: Progress, node: str, update: Any) -> Progress:
    if not progress.has("search"):  # ingest or answer: plan steps are graph nodes
        return _finish(progress, node)
    if node == "tools":  # the assistant searched: text so far was a preamble
        progress = _tools_ran(progress, "search", update)
        progress = _with(progress, "answer", state="pending")
        return progress.model_copy(update={"text": ""})
    if node == "model":
        messages = _messages(update)
        if messages and not _get(messages[-1], "tool_calls"):  # the final answer
            progress = _with(progress, "answer", state="done")
            text = _text(_get(messages[-1], "content"))
            return progress.model_copy(update={"text": text})
    return progress


def _token(progress: Progress, data: Sequence[Any]) -> Progress:
    message, metadata = data
    if not progress.has("answer") or (metadata or {}).get("langgraph_node") != "model":
        return progress
    text = _text(_get(message, "content"))
    if not text:
        return progress
    search = progress.step("search")
    if search.state != "done":
        progress = _with(progress, "search", state="done" if search.calls else "skipped")
    progress = _with(progress, "answer", state="running")
    return progress.model_copy(update={"text": progress.text + text})


def advance(progress: Progress, part: Mapping[str, Any]) -> Progress:
    """Return `progress` after one v2 stream part (parts it doesn't know change nothing)."""
    kind, ns, data = part.get("type"), tuple(part.get("ns") or ()), part.get("data")
    if kind == "custom" and isinstance(data, Mapping) and "step" in data:
        changes: dict[str, Any] = {"detail": data.get("detail")}
        if data.get("state"):
            changes["state"] = data["state"]
        return _with(progress, str(data["step"]), **changes)
    if kind == "updates" and isinstance(data, Mapping):
        if not ns:
            for node, update in data.items():
                progress = _root_update(progress, node, update)
            return progress
        if str(ns[0]).startswith("stage:") and "tools" in data:
            return _tools_ran(progress, "stage", data["tools"])
        return progress
    if kind == "messages" and not ns and isinstance(data, Sequence) and len(data) == 2:
        return _token(progress, data)
    return progress


def follow(
    progress: Progress, parts: Iterable[Mapping[str, Any]]
) -> Generator[Progress, None, Progress]:
    """Yield `progress` each time a part changes it; return the last one.

    Backends use it as `progress = yield from follow(progress, parts)`.
    """
    for part in parts:
        new = advance(progress, part)
        if new != progress:
            progress = new
            yield progress
    return progress


def settle(progress: Progress, status: str) -> Progress:
    """Return the final progress once the run stopped with `status`.

    "review": the review is ready. Steps that never finished become skipped, so no
    surface shows a step running after the run ended (a reject skips apply).
    """
    if status == "review":
        progress = _with(progress, "review", state="done")
    steps = tuple(
        s.model_copy(update={"state": "skipped"})
        if s.state in ("pending", "running")
        else s
        for s in progress.steps
    )
    return progress.model_copy(update={"steps": steps})
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit_tests/test_progress.py -v`. Expected: all PASS.

- [ ] **Step 5: Mutation check**

Drop `not ns and` from the `messages` branch of `advance`, so subagent tokens get through. `test_answer_text_streams_from_the_top_level_model_only` must FAIL. Revert.
Drop the `"text": ""` reset in `_root_update`. `test_text_before_a_tool_call_is_dropped` must still pass (the final `model` update replaces the text), so also drop the final-text line `return progress.model_copy(update={"text": text})` (return `progress`). The test must FAIL. Revert both. These two mechanisms back each other up: live text resets on a tool call, and the final answer overwrites it.

- [ ] **Step 6: Walk through and commit (after the user's yes)**

```bash
git add src/llm_wiki/progress.py tests/unit_tests/test_progress.py
git commit -m "progress.py: LangGraph v2 stream parts to steps (pure)"
```

---

### Task 3: Streaming on the backends (local and server)

**Files:**
- Modify: `src/llm_wiki/runner.py` (`start`, `resume`; add `new_thread_id`, `stream_start`, `stream_resume`, `_resume_command`)
- Modify: `src/llm_wiki/backend/base.py` (add `Finished`, `StreamEvent`; extend `Backend`; docstring)
- Modify: `src/llm_wiki/backend/__init__.py` (export `Finished`, `StreamEvent`)
- Modify: `src/llm_wiki/backend/local.py`
- Modify: `src/llm_wiki/backend/server.py`
- Test: `tests/unit_tests/test_backend_contract.py`

**Interfaces:**
- Consumes: `step_plan`, `follow`, `settle`, `Progress` (Task 2); the custom events (Task 1).
- Produces:
  - `class Finished(BaseModel)` (frozen): `progress: Progress`, `result: RunResult | None = None`, `answer: str | None = None`
  - `StreamEvent = Progress | Finished`
  - `Backend.stream_ingest(note_path: str) -> Iterator[StreamEvent]`
  - `Backend.stream_answer(thread_id: str, answer: ReviewAnswer | dict[str, Any] | str) -> Iterator[StreamEvent]`
  - `Backend.stream_ask(question: str, history: Sequence[ChatTurn] = ()) -> Iterator[StreamEvent]`
  - What each one yields:
    - first, the plan, as a `Progress`;
    - then each change;
    - finally exactly one `Finished`.

    It raises `NoteNotFound`, `UnknownThread`, `NoOpenReview` or `BackendError` *while iterating*, in the same situations as the blocking methods.
  - `IngestRunner.new_thread_id() -> str` (static); `stream_start(note_path: str, thread_id: str) -> Iterator[dict]`; `stream_resume(thread_id: str, answer) -> Iterator[dict]`

- [ ] **Step 1: Write the failing contract tests**

Add `Finished` to the `from llm_wiki.backend import ...` line at the top of `tests/unit_tests/test_backend_contract.py`, and append these tests. They run for `local`, and for `server` when `WIKI_SERVER_TESTS` is set:

```python
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
    blocking = backend.start_ingest(NOTE)  # the ledger diff carries a time: compare facts
    assert finished.result.review.facts == blocking.review.facts


def test_streamed_approve_writes_and_reports_the_commit(backend):
    _, started = drain(backend.stream_ingest(NOTE))
    _, done = drain(backend.stream_answer(started.result.thread_id, "approve"))
    assert (done.result.status, done.result.action) == ("done", "approve")
    assert NOTE in done.result.written
    assert states(done.progress)["apply"] == "done"
    commit = done.progress.step("commit")  # the vault copy is not a git repo
    assert (commit.state, commit.detail) == ("skipped", "not committed")


def test_streamed_reject_leaves_nothing_running(backend):
    _, started = drain(backend.stream_ingest(NOTE))
    _, done = drain(backend.stream_answer(started.result.thread_id, "reject"))
    assert done.result.action == "reject"
    assert set(states(done.progress).values()) == {"skipped"}


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
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest tests/unit_tests/test_backend_contract.py -q`
Expected: collection error, `ImportError: cannot import name 'Finished'`.

- [ ] **Step 3: `Finished` and the protocol in `backend/base.py`**

Change the import to `from collections.abc import Iterator, Mapping, Sequence` and add `from llm_wiki.progress import Progress`. After `RunResult`:

```python
class Finished(BaseModel):
    """The last event of a `stream_*` call: the final progress and the call's value.

    Attributes:
        progress: Settled: no step is left running.
        result: For `stream_ingest` / `stream_answer`: what the blocking call returns.
        answer: For `stream_ask`: the assistant's answer.
    """

    model_config = ConfigDict(frozen=True)

    progress: Progress
    result: RunResult | None = None
    answer: str | None = None


StreamEvent = Progress | Finished
```

In the `Backend` protocol, after `ask`:

```python
    def stream_ingest(self, note_path: str) -> Iterator[StreamEvent]:
        """Like `start_ingest`, yielding `Progress` as steps run, then one `Finished`.

        Raises (while iterating):
            NoteNotFound: No note at `note_path` in this vault.
        """
        ...

    def stream_answer(
        self, thread_id: str, answer: ReviewAnswer | dict[str, Any] | str
    ) -> Iterator[StreamEvent]:
        """Like `answer`, yielding `Progress`, then one `Finished`.

        Raises (while iterating):
            UnknownThread: No such thread on this vault.
            NoOpenReview: Nothing is waiting for an answer on it.
        """
        ...

    def stream_ask(
        self, question: str, history: Sequence[ChatTurn] = ()
    ) -> Iterator[StreamEvent]:
        """Like `ask`, yielding `Progress` (its `text` is the answer so far), then
        one `Finished` with `answer`."""
        ...
```

In the module docstring:
- Change the Backend line to `Backend: start_ingest · pending · review · answer · ask (+ stream_ingest · stream_answer · stream_ask)`.
- Replace "Calls block until the run pauses or ends (no streaming yet; 0010 rule 4)." with: "Blocking calls return when the run pauses or ends; `stream_*` calls yield `Progress` on the way (`llm_wiki.progress`) and end with one `Finished`."

In `backend/__init__.py`, import `Finished` and `StreamEvent` from `base` and add both to `__all__`.

- [ ] **Step 4: Streams in the runner**

In `src/llm_wiki/runner.py`, add `from collections.abc import Iterator`, and below the imports:

```python
STREAM_MODES = ["updates", "custom"]  # what `llm_wiki.progress` reads from ingest
```

Inside `IngestRunner`:

```python
    @staticmethod
    def new_thread_id() -> str:
        """Return a fresh ingest thread id."""
        return f"ingest-{uuid.uuid4().hex[:12]}"

    def stream_start(self, note_path: str, thread_id: str) -> Iterator[dict[str, Any]]:
        """Ingest a note, yielding LangGraph v2 stream parts until the review pauses."""
        yield from self.graph.stream(
            {"note_path": note_path},
            self._config(thread_id),
            context=self.context,
            stream_mode=STREAM_MODES,
            subgraphs=True,
            version="v2",
        )

    def stream_resume(
        self, thread_id: str, answer: ReviewAnswer | dict[str, Any] | str
    ) -> Iterator[dict[str, Any]]:
        """Send the user's answer, yielding v2 stream parts (same checks as `resume`)."""
        command = self._resume_command(thread_id, answer)
        yield from self.graph.stream(
            command,
            self._config(thread_id),
            context=self.context,
            stream_mode=STREAM_MODES,
            subgraphs=True,
            version="v2",
        )

    def _resume_command(
        self, thread_id: str, answer: ReviewAnswer | dict[str, Any] | str
    ) -> Command:
        """Check the thread can take an answer here; return the resume command."""
        if self.status(thread_id).status != "review":
            raise ValueError(f"no review is waiting on thread {thread_id!r}")
        self._check_vault(thread_id)
        if isinstance(answer, ReviewAnswer):
            answer = answer.model_dump(mode="json")
        return Command(resume=answer)
```

In `start`, use `thread_id = thread_id or self.new_thread_id()`. Make `resume`'s body (keep its docstring):

```python
        self.graph.invoke(
            self._resume_command(thread_id, answer),
            self._config(thread_id),
            context=self.context,
        )
        return self.status(thread_id)
```

Run: `uv run pytest tests/unit_tests/test_runner.py -q`. Expected: PASS (the refactor keeps behaviour).

- [ ] **Step 5: Streams on `LocalBackend`**

In `src/llm_wiki/backend/local.py`:
- Change the import to `from collections.abc import Iterator, Sequence`.
- Add `Finished, StreamEvent` to the `backend.base` import.
- Add `from llm_wiki.progress import follow, settle, step_plan`.

Then add the module function:

```python
def _messages(question: str, history: Sequence[ChatTurn]) -> list[dict[str, str]]:
    """Return the chat as model messages: the earlier turns, then the question."""
    return [
        *({"role": r, "content": text} for r, text in history),
        {"role": "user", "content": question},
    ]
```

`ask` then uses `result = agent.invoke({"messages": _messages(question, history)})`. Add to the class:

```python
    def stream_ingest(self, note_path: str) -> Iterator[StreamEvent]:
        """Ingest `note_path`, yielding progress, until its review pauses."""
        thread_id = self._runner.new_thread_id()
        progress = step_plan("ingest")
        yield progress
        try:
            progress = yield from follow(
                progress, self._runner.stream_start(note_path, thread_id)
            )
        except FileNotFoundError as exc:  # raised by the graph's load_note
            raise NoteNotFound(f"no note at {note_path} in {self._vault}") from exc
        result = self._runner.status(thread_id)
        yield Finished(progress=settle(progress, result.status), result=result)

    def stream_answer(
        self, thread_id: str, answer: ReviewAnswer | dict[str, Any] | str
    ) -> Iterator[StreamEvent]:
        """Send the user's answer, yielding progress, until the next pause or the end."""
        if self.review(thread_id).status != "review":
            raise NoOpenReview(f"no review is waiting on thread {thread_id!r}")
        progress = step_plan("answer")
        yield progress
        progress = yield from follow(
            progress, self._runner.stream_resume(thread_id, answer)
        )
        result = self._runner.status(thread_id)
        yield Finished(progress=settle(progress, result.status), result=result)

    def stream_ask(
        self, question: str, history: Sequence[ChatTurn] = ()
    ) -> Iterator[StreamEvent]:
        """Answer, yielding progress whose `text` is the answer so far."""
        agent = build_assistant(
            self._vault, configured_model(self._model), fallbacks=configured_fallbacks()
        )
        progress = step_plan("ask")
        yield progress
        parts = agent.stream(
            {"messages": _messages(question, history)},
            stream_mode=["updates", "messages"],
            subgraphs=True,
            version="v2",
        )
        progress = yield from follow(progress, parts)
        yield Finished(progress=settle(progress, "done"), answer=progress.text)
```

Run: `uv run pytest tests/unit_tests/test_backend_contract.py -q`. Expected: the local params PASS; the server ones skip.

- [ ] **Step 6: Streams on `ServerBackend`**

In `src/llm_wiki/backend/server.py`:
- Add `Iterator` to the `collections.abc` import.
- Add `Finished, StreamEvent` to the base import.
- Add `from llm_wiki.progress import follow, settle, step_plan`.

Then add, below `MAX_PENDING`:

```python
INGEST_MODES = ["updates", "custom"]
ASK_MODES = ["updates", "messages-tuple"]  # arrives as type "messages": [message, metadata]
STREAM: dict[str, Any] = {"stream_subgraphs": True, "version": "v2"}
```

Below `_reachable`:

```python
def _reachable_stream(method: F) -> F:
    """`_reachable` for generators.

    A generator's body runs after the call has returned, so HTTP failures surface
    while iterating; they are converted there.
    """

    @functools.wraps(method)
    def wrapper(self: ServerBackend, *args: Any, **kwargs: Any) -> Any:
        try:
            yield from method(self, *args, **kwargs)
        except httpx.HTTPError as exc:
            raise BackendError(
                f"the Agent Server at {self.url} failed: {type(exc).__name__}"
            ) from exc

    return wrapper  # type: ignore[return-value]


def _errors_raised(
    parts: Iterator[Mapping[str, Any]], fail: Callable[[Mapping[str, Any]], None]
) -> Iterator[Mapping[str, Any]]:
    """Pass parts through; on the server's "error" part call `fail` (which raises)."""
    for part in parts:
        if part.get("type") == "error":
            fail(part.get("data") or {})
        yield part


def _messages(question: str, history: Sequence[ChatTurn]) -> list[dict[str, str]]:
    """Return the chat as model messages: the earlier turns, then the question."""
    return [
        *({"role": r, "content": text} for r, text in history),
        {"role": "user", "content": question},
    ]


def _ingest_error(error: Mapping[str, Any]) -> None:
    raise BackendError(f"ingest failed on the server: {error.get('error')}")


def _assistant_error(error: Mapping[str, Any]) -> None:
    raise BackendError(f"the assistant failed on the server: {error.get('error')}")
```

In the class, add these two helpers, then make the existing methods use them:

```python
    def _new_thread(self) -> str:
        return self._client.threads.create(
            metadata={"vault_path": str(self._vault)}, graph_id=INGEST
        )["thread_id"]

    def _ingest_failed(
        self, error: Mapping[str, Any], note_path: str, thread_id: str
    ) -> None:
        """Raise for a failed ingest run (a missing note deletes its new thread)."""
        if error.get("error") == "FileNotFoundError":  # from load_note
            self._client.threads.delete(thread_id)
            raise NoteNotFound(f"no note at {note_path} in {self._vault}")
        _ingest_error(error)
```

- `start_ingest`:
  - `thread_id = self._new_thread()`;
  - `if (error := _failure(output)) is not None: self._ingest_failed(error, note_path, thread_id)`.
- `answer`: `if (error := _failure(output)) is not None: _ingest_error(error)`.
- `ask`:
  - `input={"messages": _messages(question, history)}`;
  - `if (error := _failure(output)) is not None: _assistant_error(error)`.

Add the streams:

```python
    @_reachable_stream
    def stream_ingest(self, note_path: str) -> Iterator[StreamEvent]:
        """Ingest `note_path` in a new server thread, yielding progress."""
        thread_id = self._new_thread()
        progress = step_plan("ingest")
        yield progress
        parts = self._client.runs.stream(
            thread_id,
            INGEST,
            input={"note_path": note_path},
            context=self._context(),
            stream_mode=INGEST_MODES,
            **STREAM,
        )
        progress = yield from follow(
            progress,
            _errors_raised(
                parts, lambda error: self._ingest_failed(error, note_path, thread_id)
            ),
        )
        result = self._status(thread_id)
        yield Finished(progress=settle(progress, result.status), result=result)

    @_reachable_stream
    def stream_answer(
        self, thread_id: str, answer: ReviewAnswer | dict[str, Any] | str
    ) -> Iterator[StreamEvent]:
        """Resume the thread with the user's answer, yielding progress."""
        if self.review(thread_id).status != "review":
            raise NoOpenReview(f"no review is waiting on thread {thread_id!r}")
        if isinstance(answer, ReviewAnswer):
            answer = answer.model_dump(mode="json")
        progress = step_plan("answer")
        yield progress
        parts = self._client.runs.stream(
            thread_id,
            INGEST,
            command={"resume": answer},
            context=self._context(),
            stream_mode=INGEST_MODES,
            **STREAM,
        )
        progress = yield from follow(progress, _errors_raised(parts, _ingest_error))
        result = self._status(thread_id)
        yield Finished(progress=settle(progress, result.status), result=result)

    @_reachable_stream
    def stream_ask(
        self, question: str, history: Sequence[ChatTurn] = ()
    ) -> Iterator[StreamEvent]:
        """Answer from a threadless run, yielding progress with the text so far."""
        progress = step_plan("ask")
        yield progress
        parts = self._client.runs.stream(
            None,
            ASSISTANT,
            input={"messages": _messages(question, history)},
            context=self._context(),
            stream_mode=ASK_MODES,
            **STREAM,
        )
        progress = yield from follow(progress, _errors_raised(parts, _assistant_error))
        yield Finished(progress=settle(progress, "done"), answer=progress.text)
```

- [ ] **Step 7: Run the contract tests on both backends**

Run: `uv run pytest tests/unit_tests/test_backend_contract.py -q`. Expected: local PASS.
Run: `WIKI_SERVER_TESTS=1 uv run pytest tests/unit_tests/test_backend_contract.py -q`. Expected: server PASS. It starts `langgraph dev` on the scripted graphs, takes about a minute, and costs nothing.

If a server stream test fails, print what the server really sends, and fix `advance` (Task 2) or the stream modes to match it, not the test. Start `langgraph dev` the way `_dev_server` does, then:

```bash
uv run python - <<'EOF'
from langgraph_sdk import get_sync_client
c = get_sync_client(url="http://127.0.0.1:PORT")  # the port langgraph dev printed
for p in c.runs.stream(None, "assistant", input={"messages": [{"role": "user", "content": "hi"}]},
                       stream_mode=["updates", "messages-tuple"], stream_subgraphs=True, version="v2"):
    print(p["type"], p["ns"], str(p["data"])[:120])
EOF
```

Then check the production image once: `WIKI_SERVER_TESTS=docker uv run pytest tests/unit_tests/test_backend_contract.py -q`. The first build takes minutes.

- [ ] **Step 8: Whole suite, mutation check**

Run: `uv run pytest -q`. Expected: all PASS.
Mutation checks:
- In `LocalBackend.stream_ingest`, yield `Finished(progress=progress, ...)` without `settle`. `test_streamed_ingest_ends_where_start_ingest_ends` must FAIL ("review" is still running). Revert.
- In `ServerBackend.stream_ingest`, pass `parts` straight to `follow` (no `_errors_raised`). The server run of `test_streaming_a_missing_note_is_refused` must FAIL. Revert.

- [ ] **Step 9: Walk through and commit (after the user's yes)**

```bash
git add src/llm_wiki/runner.py src/llm_wiki/backend/ tests/unit_tests/test_backend_contract.py
git commit -m "Backends: stream_ingest / stream_answer / stream_ask (progress, then Finished)"
```

---

### Task 4: `chat.session.stream_reply`

**Files:**
- Modify: `src/llm_wiki/chat/session.py`
- Test: `tests/unit_tests/test_chat.py` (fixture parametrised; one new test)

**Interfaces:**
- Consumes: `Backend.stream_*`, `Finished`, `StreamEvent` (Task 3); `Progress` (Task 2).
- Produces:
  - `class ChatReply(BaseModel)` (frozen) with fields:
    - `text: str`;
    - `result: RunResult | None = None`, set when the reply shows or answered a review (a surface adds buttons);
    - `progress: Progress | None = None`.
  - `stream_reply(backend, message, history=(), *, link_base="", today=None) -> Iterator[Progress | ChatReply]` yields `Progress` events, then exactly one `ChatReply`.
  - `reply(...)`: the same signature and text as today; it now returns the last `ChatReply`'s text from `stream_reply`.

- [ ] **Step 1: Run every chat test through both entry points (failing first)**

In `tests/unit_tests/test_chat.py`, change the session import to `from llm_wiki.chat.session import ChatReply, questions_only, reply, stream_reply`. Parametrise the `chat` fixture and choose the entry point in `say`. Everything between the docstring and `def say` stays as it is:

```python
@pytest.fixture(params=["reply", "stream_reply"])
def chat(request, vault_copy, monkeypatch):
    """The chat over a local backend on a vault copy: scripted ingest, scripted answers.

    Every chat test runs twice: through `reply`, and through `stream_reply` drained to
    its last event (the same rules must give the same text).
    """
    # ... the existing setup, unchanged, down to `history: list[...] = []`

    def say(text):
        if request.param == "reply":
            answer = reply(backend, text, history, today=TODAY)
        else:
            *_, last = stream_reply(backend, text, history, today=TODAY)
            assert isinstance(last, ChatReply)
            answer = last.text
        history.extend([("user", text), ("assistant", answer)])
        return answer

    return say, ingest, backend
```

Add:

```python
def test_a_streamed_ingest_shows_steps_then_the_review_with_its_result(chat):
    _, _, backend = chat
    *steps, last = stream_reply(backend, "/ingest 2026-10-12", today=TODAY)
    assert steps and not any(isinstance(e, ChatReply) for e in steps)
    assert isinstance(last, ChatReply)
    assert last.result is not None and last.result.status == "review"
    assert last.progress.step("review").state == "done"
    assert f"**Review: {NOTE}**" in last.text  # render_result's heading
```

Run: `uv run pytest tests/unit_tests/test_chat.py -q`. Expected: FAIL with `ImportError: cannot import name 'ChatReply'`.

- [ ] **Step 2: Write `stream_reply`; make `reply` drain it**

In `src/llm_wiki/chat/session.py`:
- Change the import to `from collections.abc import Generator, Iterator, Sequence`.
- Add `from pydantic import BaseModel, ConfigDict`.
- Add `Finished, StreamEvent` to the `llm_wiki.backend` import.
- Add `from llm_wiki.progress import Progress`.

Then replace `reply` and `_command` with:

```python
class ChatReply(BaseModel):
    """The chat's last event: the reply text, and what a surface may add to it.

    Attributes:
        text: The reply, as `reply` returns it.
        result: The review it shows or answered, if any (a surface adds buttons).
        progress: The run's settled steps, if a run happened.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    result: RunResult | None = None
    progress: Progress | None = None


def _follow(events: Iterator[StreamEvent]) -> Generator[Progress, None, Finished]:
    """Pass a backend stream's progress through; return its `Finished` event."""
    for event in events:
        if isinstance(event, Finished):
            return event
        yield event
    raise RuntimeError("the backend stream ended without a Finished event")


def reply(
    backend: Backend,
    message: str,
    history: Sequence[ChatTurn] = (),
    *,
    link_base: str = "",
    today: datetime.date | None = None,
) -> str:
    """Return the chat's answer to `message`, given the conversation so far."""
    *_, last = stream_reply(backend, message, history, link_base=link_base, today=today)
    assert isinstance(last, ChatReply)
    return last.text


def stream_reply(
    backend: Backend,
    message: str,
    history: Sequence[ChatTurn] = (),
    *,
    link_base: str = "",
    today: datetime.date | None = None,
) -> Iterator[Progress | ChatReply]:
    """Like `reply`, yielding the run's progress first; ends with one `ChatReply`."""
    if not is_command(message):
        finished = yield from _follow(
            backend.stream_ask(message, questions_only(history))
        )
        answer = finished.answer or ""
        if open_reviews := backend.pending():
            note = _note(open_reviews[0])
            answer += f"\n\n_(A review is still open for {note}: `/review` shows it.)_"
        yield ChatReply(text=answer, progress=finished.progress)
        return
    yield from _command(backend, message, link_base, today or datetime.date.today())


def _command(
    backend: Backend, message: str, link_base: str, today: datetime.date
) -> Iterator[Progress | ChatReply]:
    parsed = parse_command(message, today)
    if parsed.kind in ("help", "error"):
        yield ChatReply(text=parsed.message or HELP)
        return
    open_reviews = backend.pending()

    if parsed.kind == "ingest":
        if open_reviews:
            yield ChatReply(
                text=f"A review is open for {_note(open_reviews[0])}: "
                "`/approve` or `/reject` it first (`/review` shows it)."
            )
            return
        assert parsed.note_path is not None
        try:
            finished = yield from _follow(backend.stream_ingest(parsed.note_path))
        except NoteNotFound:
            yield ChatReply(text=f"No note at {parsed.note_path} in {backend.vault}.")
            return
        yield _shown(finished.result, link_base, finished.progress)
        return

    if not open_reviews:
        yield ChatReply(text="No review is open. Start one with `/ingest [YYYY-MM-DD]`.")
        return
    if len(open_reviews) > 1:
        rows = "\n".join(
            f"- [{_note(r)}]({review_link(link_base, r.thread_id)})"
            for r in open_reviews
        )
        yield ChatReply(
            text=f"{len(open_reviews)} reviews are open; answer them on the review page:\n{rows}"
        )
        return
    (open_review,) = open_reviews
    if parsed.kind == "review":
        yield _shown(open_review, link_base)
        return
    answer = parsed.answer
    if parsed.kind == "remember":  # adds to the lists the review already has
        assert open_review.review is not None
        answer = _remember_answer(open_review.review, parsed)
    assert answer is not None
    try:
        finished = yield from _follow(
            backend.stream_answer(open_review.thread_id, answer)
        )
    except (UnknownThread, NoOpenReview) as exc:  # answered elsewhere meanwhile
        yield ChatReply(text=f"Can't answer that review here: {exc}")
        return
    yield _shown(finished.result, link_base, finished.progress)


def _shown(
    result: RunResult | None, link_base: str, progress: Progress | None = None
) -> ChatReply:
    """Return the reply that shows `result` with its review link."""
    assert result is not None
    return ChatReply(
        text=render_result(result, review_link(link_base, result.thread_id)),
        result=result,
        progress=progress,
    )
```

Add one line to the module docstring: "`stream_reply` is the same chat, yielding the run's progress first (Slack draws it); `reply` keeps only its last text."

- [ ] **Step 3: Run the chat tests and the whole suite**

Run: `uv run pytest tests/unit_tests/test_chat.py -v`. Expected: all PASS, each test twice.
Run: `uv run pytest -q`. Expected: all PASS. `test_review_web.py` covers `/chat/send`, which calls `reply`.

- [ ] **Step 4: Mutation check**

In `stream_reply`, yield the `ChatReply` before the "A review is still open" suffix is added. The `stream_reply` run of `test_review_waits_while_you_ask_questions_then_revise_and_approve` must FAIL. Revert.

- [ ] **Step 5: Walk through and commit (after the user's yes)**

```bash
git add src/llm_wiki/chat/session.py tests/unit_tests/test_chat.py
git commit -m "Chat: stream_reply yields progress then the reply; reply drains it"
```

---

### Task 5: Notebook 8.3 and the wiki

**Files:**
- Create: `notebooks/8.3_streaming.ipynb`. It's git-ignored; build it and run it without being asked (memory: teaching-approach).
- Create: `docs/wiki/learnings/streaming-progress.md` (from `learnings/_template.md`)
- Modify: `docs/wiki/index.md`, `docs/wiki/log.md`, `docs/wiki/progress.md`

**Interfaces:**
- Consumes: everything above. The notebook ends by pointing at the real files.

- [ ] **Step 1: Build the notebook (toy graph, fake model, no keys)**

Cells, in order, as in the other `8.x` notebooks (markdown, then code):
1. *What and why*: a surface wants steps, not a spinner. The stream is the contract (LangGraph frontend docs, `useStream`).
2. A toy `StateGraph` `read → think → write`. Stream it with `graph.stream(..., stream_mode="updates", version="v2")` and print `type`, `ns`, `data`.
3. Emit `{"step": "think", "detail": "2 ideas"}` from inside `think`, first with `get_stream_writer()` and then with `runtime.stream_writer`. Stream `["updates", "custom"]`. Explain why the repo uses `runtime.stream_writer`: unit tests pass a plain namespace.
4. A subagent inside `think`: `create_agent` with a `GenericFakeChatModel` that calls one tool. Stream with `subgraphs=True`, show the `ns` tuple, and filter on `ns[0].startswith("think:")` to count tool calls.
5. `stream_mode="messages"`: tokens with `metadata["langgraph_node"]`. Show why subagent tokens are dropped (their `ns` isn't empty).
6. A 20-line `advance(states, part)` over a dict of step states, driven by the parts recorded in cells 2–5.
7. The server side, read-only. `client.runs.stream(..., stream_mode=["updates", "messages-tuple"], stream_subgraphs=True, version="v2")` gives the same `{"type", "ns", "data"}`, with `ns` a list and `data` dicts. Run `advance` on a recorded list of such parts (no server needed).
8. **Exercises** (the user writes these):
   - (a) Add a `"failed"` state for a `custom` event that says so.
   - (b) Count tool calls per subgraph.
   - (c) In one sentence, explain why `settle` is needed after a reject.
9. *In this repo*:
   - repo files: `src/llm_wiki/progress.py`, `ingest/nodes.py::report`, `backend/local.py::stream_ingest`, `backend/server.py::stream_ingest`, `chat/session.py::stream_reply`;
   - paid-media: `src/paid_media_agent/surfaces/slack/delivery.py` (a run's events into `chat_stream`) and `surfaces/runner.py`.

Run: `uv run jupyter nbconvert --to notebook --execute --inplace notebooks/8.3_streaming.ipynb`. Expected: no errors. The exercise cells hold `...` bodies that don't raise.

- [ ] **Step 2: Wiki**

- `learnings/streaming-progress.md`:
  - the concepts: v2 parts, stream modes, `subgraphs`, custom events as the contract, a generator ending in one final event;
  - the repo paths from cell 9 and the paid-media reference;
  - "In my words" left empty.
- `index.md`: one line linking it.
- `log.md`, 2026-10-18 entry:
  - Slack step 1 done: quiet notes skip the agent (≈ $0.005 → $0 per quiet day), the backends stream, and the chat has `stream_reply`;
  - the commit hashes;
  - next: step 2, the Slack bot, with notebook 8.4.
- `progress.md`: tick "Slack step 1: streaming + agent skip (2026-10-18)". Current focus becomes Slack step 2.

- [ ] **Step 3: Commit the wiki (after the user's yes)**

```bash
git add docs/wiki/
git commit -m "wiki: Slack step 1 (streaming progress, quiet-note skip), learning note"
```

---

## After step 1

A real check needs the user's OK first (≈ $0.01). On `~/WorkDev`:
1. Rebuild the image: `uv run langgraph build -t llm-wiki:local`, then `WIKI_VAULT_PATH=$HOME/WorkDev docker compose up -d --wait`.
2. From a Python shell with `WIKI_BACKEND=server`, stream `/ingest` on today's rollover note through `stream_reply`. Expect "Agent filing facts: nothing new to file" and no OpenRouter charge.
3. Stream one question, and expect the text to arrive in pieces.

After that, plan step 2 (the Slack bot) from the same spec.
