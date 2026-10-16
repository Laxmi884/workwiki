"""The `ingest_agent` graph (decision 0006 spike): the 0004 graph with one agent step.

    load_note ──(unchanged / broken ledger)──→ END
        ↓
    assign_ids → track_tasks → stage (ingest deep agent) → render → review
    review ──approve / reject──→ END
           ├─ revise ─→ stage (feedback + the previous filing)
           └─ edit / make_page / answer ─→ render  (spike: no new edits, fresh review)

Code nodes are the 0004 ones, unchanged. `stage` replaces extract, find_names and route:
the agent reads the note and the vault and stages edits that `staging` checks.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from typing import Any

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import RetryPolicy

from llm_wiki.diffs import vault_reader
from llm_wiki.entity_index import load_index
from llm_wiki.ingest import nodes
from llm_wiki.ingest.state import IngestContext, IngestInput, IngestState
from llm_wiki.ingest_agent.agent import (
    SKILLS_DIR,
    AgentContext,
    build_brief,
    build_ingest_agent,
)
from llm_wiki.ingest_agent.staging import (
    Filed,
    StagingContext,
    citable_lines,
    finalize,
)

RECURSION_LIMIT = 120  # agent steps: ~2 per tool call


class AgentIngestState(IngestState, total=False):
    """`IngestState` plus what the agent step reports.

    Attributes:
        agent_filed: Accepted facts as dicts (`Filed` fields: line, path, section,
            text), last round. Plain dicts so the checkpointer restores them.
        questions: The agent's questions for the review, last round.
        agent_stats: Last round's numbers: seconds, tool calls, refusals, tokens.
    """

    agent_filed: list[dict[str, Any]]
    questions: list[str]
    agent_stats: dict[str, Any]


def stage(state: AgentIngestState, runtime: Runtime[IngestContext]) -> dict[str, Any]:
    """Run the ingest agent on the note; its checked, staged edits become `fact_edits`."""
    vault = nodes.vault_path(runtime)
    read = vault_reader(vault)
    note = state["note"]
    text = read(note.path)
    assert text is not None  # load_note read it
    entities = load_index(vault)
    citable = citable_lines(note, state.get("seen_lines", []))
    revising = state.get("review_action") == "revise"
    brief = build_brief(
        note,
        text,
        citable,
        state.get("task_ids", {}),
        state.get("task_projects", {}),
        state.get("done_tasks", []),
        entities,
        read,
        feedback=state.get("feedback", []) if revising else (),
        previous=[Filed(**f) for f in state.get("agent_filed", [])] if revising else (),
    )
    skills = (runtime.context or {}).get("skills_dir")
    agent = build_ingest_agent(
        vault,
        nodes.chat_model(runtime),
        Path(skills) if skills else SKILLS_DIR,
        fallbacks=nodes.fallback_models(runtime),
    )
    context = AgentContext(StagingContext(note, citable, entities, read))
    started = time.monotonic()
    with get_usage_metadata_callback() as usage:
        start: dict[str, Any] = {"messages": [HumanMessage(brief)]}
        if revising:  # the previous round stays staged; the agent changes it
            start |= {
                "staged": list(state.get("fact_edits", [])),
                "filed": [Filed(**f) for f in state.get("agent_filed", [])],
            }
        result = agent.invoke(
            start,
            context=context,
            config={"recursion_limit": RECURSION_LIMIT},
        )
    calls = [
        c["name"]
        for m in result["messages"]
        if isinstance(m, AIMessage)
        for c in m.tool_calls
    ]
    staged, filed, problems = finalize(
        result.get("staged", []), result.get("filed", []), result.get("withdrawn", [])
    )
    refusals = [*result.get("refusals", []), *problems]
    stats = {
        "seconds": round(time.monotonic() - started, 1),
        "model_calls": sum(isinstance(m, AIMessage) for m in result["messages"]),
        "tool_calls": len(calls),
        "calls_by_tool": {n: calls.count(n) for n in sorted(set(calls))},
        "refusals": len(refusals),
        "tokens": {
            m: {k: u.get(k, 0) for k in ("input_tokens", "output_tokens")}
            for m, u in usage.usage_metadata.items()
        },
    }
    questions = result.get("questions", [])
    return {
        "fact_edits": staged,
        "agent_filed": [dataclasses.asdict(f) for f in filed],
        "questions": questions,
        "agent_stats": stats,
        "warnings": [f"agent refused: {r}" for r in refusals]
        + [f"question {q}" for q in questions],
    }


def after_review(state: AgentIngestState) -> str:
    """approve/reject end; revise asks the agent again; anything else re-renders."""
    action = state.get("review_action")
    if action in ("approve", "reject"):
        return END
    return "stage" if action == "revise" else "render"


builder = StateGraph(
    AgentIngestState, context_schema=IngestContext, input_schema=IngestInput
)
builder.add_node("load_note", nodes.load_note)
builder.add_node("assign_ids", nodes.assign_ids)
builder.add_node("track_tasks", nodes.track_tasks)
builder.add_node("stage", stage, retry_policy=RetryPolicy(max_attempts=2))
builder.add_node("render", nodes.render)
builder.add_node("review", nodes.review_changes)

builder.add_edge(START, "load_note")
builder.add_conditional_edges("load_note", nodes.after_load, ["assign_ids", END])
builder.add_edge("assign_ids", "track_tasks")
builder.add_edge("track_tasks", "stage")
builder.add_edge("stage", "render")
builder.add_edge("render", "review")
builder.add_conditional_edges("review", after_review, ["stage", "render", END])

graph = builder.compile(name="ingest_agent")
