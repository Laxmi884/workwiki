"""Rollover as a one-node graph, so an Agent Server cron can run it (decision 0017).

The node calls `rollover.roll_over`; no model. State is plain JSON (decision 0011):

    input   {"date": "2026-10-19"?, "timezone": "America/Toronto"?, "commit": true?}
    output  {"result": <Rollover as JSON>}

With no `date`, the day is today in `timezone`, else `WIKI_TIMEZONE`, else the server's
local time (UTC in our image), so the cron passes its own time zone. The vault is the
run's context `vault_path`, else `WIKI_VAULT_PATH`. A `RolloverError` fails the run.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from llm_wiki import clock
from llm_wiki.config import get_settings
from llm_wiki.rollover import roll_over


class RolloverState(TypedDict, total=False):
    """Input `date`, `timezone`, `commit`; output `result`."""

    date: str | None
    timezone: str | None
    commit: bool
    result: dict[str, Any]


class RolloverContext(TypedDict, total=False):
    """Per-run settings; `vault_path` defaults to `WIKI_VAULT_PATH`."""

    vault_path: str


def run(state: RolloverState, runtime: Runtime[RolloverContext]) -> dict[str, Any]:
    """Create the day's note unless it exists; return what was done."""
    context = runtime.context or {}
    vault = (
        Path(context["vault_path"]).expanduser().resolve()
        if context.get("vault_path")
        else get_settings().vault_path
    )
    day = (
        datetime.date.fromisoformat(state["date"])
        if state.get("date")
        else clock.today(state.get("timezone"))
    )
    result = roll_over(vault, day, commit=state.get("commit", True))
    return {"result": result.model_dump(mode="json")}


graph = (
    StateGraph(RolloverState, context_schema=RolloverContext)
    .add_node("rollover", run)
    .add_edge(START, "rollover")
    .add_edge("rollover", END)
    .compile(name="rollover")
)
