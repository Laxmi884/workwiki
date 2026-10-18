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
    "ask": (("search", "Searching the wiki"), ("answer", "Writing the answer")),
}

_REVIEW_AGAIN = (
    ("map_tasks", "Projects"),
    ("render", "Checks"),
    ("review", "Review ready"),
)
# A review answer runs different nodes per action (`graph.after_review`).
ANSWER_PLANS: dict[str, tuple[tuple[str, str], ...]] = {
    "approve": (("apply", "Writing files"), ("commit", "Git commit")),
    "reject": (("review", "Closing the review"),),
    "revise": (("stage", "Agent revising"), *_REVIEW_AGAIN),
    "answer": (("pick_wins", "Picked wins"), *_REVIEW_AGAIN),
    "edit": (("render", "Checks"), ("review", "Review ready")),
}
# An answer the review can't read: it explains why and asks again.
_UNREADABLE = (("review", "Checking your answer"),)


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


def step_plan(kind: Literal["ingest", "ask"]) -> Progress:
    """Return the steps of an ingest start or a question; the first one running."""
    return _plan(PLANS[kind])


def answer_plan(answer: Any) -> Progress:
    """Return the steps a review answer runs: a `ReviewAnswer`, its dict, or a string.

    "approve" writes and commits; "revise" runs the agent again; "answer" and "edit"
    only rebuild the review.
    """
    action = answer if isinstance(answer, str) else _get(answer, "action")
    action = action.strip().lower() if isinstance(action, str) else None
    return _plan(ANSWER_PLANS.get(action or "", _UNREADABLE))


def _plan(plan: Sequence[tuple[str, str]]) -> Progress:
    steps = [Step(key=k, label=label) for k, label in plan]
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
    """Mark `key` done and start the first pending step after it.

    A step still pending hasn't started, so its node's update is an earlier run of
    it: a revise resumes `review` first, while the plan's `review` is the next one.
    """
    if not progress.has(key) or progress.step(key).state == "pending":
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


def message_text(content: Any) -> str:
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
    if not progress.has(key):  # e.g. a revise reruns `stage` inside an answer run
        return progress
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
            text = message_text(_get(messages[-1], "content"))
            return progress.model_copy(update={"text": text})
    return progress


def _token(progress: Progress, data: Sequence[Any]) -> Progress:
    message, metadata = data
    if not progress.has("answer") or (metadata or {}).get("langgraph_node") != "model":
        return progress
    text = message_text(_get(message, "content"))
    if not text:
        return progress
    search = progress.step("search")
    if search.state != "done":
        progress = _with(
            progress, "search", state="done" if search.calls else "skipped"
        )
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
