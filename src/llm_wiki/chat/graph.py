"""The `wiki` graph: one chat for both agents (step 4d).

    message ──route (code)──┬─ "/…" → command: start or answer an ingest review
                            └─ else → ask: the assistant answers

Ingest runs in its own checkpointed thread through `IngestRunner` (SQLite under
`WIKI_STATE_DIR`): this chat only remembers which ingest thread has a review open.
So the review keeps all its rules (digests, ids, revise limit) and survives restarts,
and questions still work while a review waits. Routing is code: a leading "/" is a
command, never a model's guess.
"""

from __future__ import annotations

import datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.runtime import Runtime
from typing_extensions import TypedDict

from llm_wiki.assistant.agent import build_assistant
from llm_wiki.assistant.graph import run_choice
from llm_wiki.chat.commands import HELP, is_command, parse_command
from llm_wiki.chat.render import render_result
from llm_wiki.config import get_settings
from llm_wiki.model_setup import configured_fallbacks, configured_model
from llm_wiki.runner import shared_runner as ingest_runner

COMMAND_REPLY = "ingest"  # name on command replies, so the assistant skips them


class WikiContext(TypedDict, total=False):
    """Per-run settings; every key is optional.

    Attributes:
        vault_path: Vault folder, e.g. "~/WorkDev" (default `WIKI_VAULT_PATH`).
        model: "provider:model" for both agents (default `WIKI_MODEL`).
    """

    vault_path: str
    model: str


class WikiState(MessagesState, total=False):
    """The chat, plus the ingest thread whose review is open (None: none open)."""

    ingest_thread: str | None


def _text(message: BaseMessage) -> str:
    return message.content if isinstance(message.content, str) else str(message.content)


def _last_question(state: WikiState) -> str:
    humans = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    return _text(humans[-1]) if humans else ""


def route(state: WikiState) -> str:
    """Send slash commands to `command`, everything else to `ask`."""
    return "command" if is_command(_last_question(state)) else "ask"


def _reply(text: str, **update: Any) -> dict[str, Any]:
    return {"messages": [AIMessage(text, name=COMMAND_REPLY)], **update}


def command(state: WikiState, runtime: Runtime[WikiContext]) -> dict[str, Any]:
    """Run one slash command against the ingest runner."""
    parsed = parse_command(_last_question(state), datetime.date.today())
    if parsed.kind in ("help", "error"):
        return _reply(parsed.message or HELP)
    vault, model = run_choice(runtime.context)
    runner = ingest_runner(vault, model)
    open_thread = state.get("ingest_thread")
    if open_thread and runner.status(open_thread).status != "review":
        open_thread = None  # finished elsewhere (e.g. Studio): forget it

    if parsed.kind == "ingest":
        if open_thread:
            note = runner.status(open_thread).review
            return _reply(
                f"A review is open for {note.note if note else 'another note'}: "
                "`/approve` or `/reject` it first (`/review` shows it)."
            )
        assert parsed.note_path is not None
        if not (vault / parsed.note_path).is_file():
            return _reply(f"No note at {parsed.note_path} in {vault}.")
        result = runner.start(parsed.note_path)
    elif not open_thread:
        return _reply("No review is open. Start one with `/ingest [YYYY-MM-DD]`.")
    elif parsed.kind == "review":
        result = runner.status(open_thread)
    else:
        assert parsed.answer is not None
        try:
            result = runner.resume(open_thread, parsed.answer)
        except ValueError as exc:  # e.g. the review belongs to another vault
            return _reply(f"Can't answer that review here: {exc}")
    still_open = result.thread_id if result.status == "review" else None
    link = f"{get_settings().review_url.rstrip('/')}/review/{result.thread_id}"
    return _reply(render_result(result, link), ingest_thread=still_open)


def ask(state: WikiState, runtime: Runtime[WikiContext]) -> dict[str, Any]:
    """Answer with the assistant, given the conversation so far (commands left out)."""
    history = [
        m
        for m in state["messages"]
        if (isinstance(m, HumanMessage) and not is_command(_text(m)))
        or (isinstance(m, AIMessage) and m.name != COMMAND_REPLY and not m.tool_calls)
    ]
    vault, model = run_choice(runtime.context)
    agent = build_assistant(
        vault, configured_model(model), fallbacks=configured_fallbacks()
    )
    result = agent.invoke({"messages": history})
    answer = result["messages"][-1]
    if open_review := state.get("ingest_thread"):
        note = f"\n\n_(A review is still open: `/review` to see it; {open_review})_"
        return {"messages": [AIMessage(_text(answer) + note)]}
    return {"messages": [AIMessage(_text(answer))]}


builder = StateGraph(WikiState, context_schema=WikiContext)
builder.add_node("command", command)
builder.add_node("ask", ask)
builder.add_conditional_edges(START, route, ["command", "ask"])
builder.add_edge("command", END)
builder.add_edge("ask", END)

graph = builder.compile(name="wiki")
