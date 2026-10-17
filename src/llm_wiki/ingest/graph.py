"""The ingest graph (decision 0006): code steps around one deep-agent step.

    load_note ──(unchanged / broken ledger)──→ END
        ↓
    assign_ids → track_tasks → stage (ingest deep agent) → map_tasks → render → review
    review ──reject──→ END
           ├─ approve ─→ apply (approved files only) → END, or render if they changed
           ├─ revise ─→ stage (feedback + the previous filing)
           ├─ answer ─→ pick_wins (picked wins become impact edits; code only)
           │              → map_tasks → render
           └─ edit ─→ render  (fresh review)

Code nodes live in `nodes.py`, the agent in `agent.py`. In `stage` the agent reads the note and the vault
and stages edits that `staging` checks.
`map_tasks` is `track_tasks` again, now with the agent's task → project proposals and
the user's answers (decision 0007).
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

from llm_wiki.diffs import VaultReader, vault_reader
from llm_wiki.domain.changes import Edit
from llm_wiki.entity_index import load_index
from llm_wiki.ingest import nodes
from llm_wiki.ingest.agent import (
    SKILLS_DIR,
    AgentContext,
    build_brief,
    build_ingest_agent,
)
from llm_wiki.ingest.staging import (
    Filed,
    StagingContext,
    citable_lines,
    finalize,
    finalize_mappings,
    mapped_pages,
    proposed_paths,
    task_people_pages,
    with_mappings,
)
from llm_wiki.ingest.state import (
    IngestContext,
    IngestInput,
    IngestState,
    models_in_node,
)
from llm_wiki.retries import is_transient
from llm_wiki.routing import win_edits

RECURSION_LIMIT = 120  # agent steps: ~2 per tool call


class AgentIngestState(IngestState, total=False):
    """`IngestState` plus what the agent step reports.

    Attributes:
        agent_filed: Accepted facts as dicts (`Filed` fields: line, path, section,
            text), last round. Plain dicts so the checkpointer restores them.
        questions: The agent's questions for the review, last round, as dicts
            (line, text); the review shows them in their own section.
        agent_stats: Last round's numbers: seconds, tool calls, refusals (count and
            texts), tokens.
        agent_edits: The agent's checked edits, last round. `fact_edits` is these plus
            the wins the user picked in the review (`pick_wins`, code only).
    """

    agent_edits: list[Edit]
    agent_filed: list[dict[str, Any]]
    questions: list[dict[str, Any]]
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
    previous_edits = state.get("agent_edits", state.get("fact_edits", []))
    chosen = state.get("chosen_projects", {})
    task_projects = {
        **state.get("task_projects", {}),
        **{line: slug for line, slug in chosen.items() if slug},
    }
    open_tasks = {
        line: options
        for line, options in state.get("open_tasks", {}).items()
        if line not in chosen
    }
    proposals = [p for p in state.get("task_proposals", []) if p.line in open_tasks]
    brief = build_brief(
        note,
        text,
        citable,
        state.get("task_ids", {}),
        task_projects,
        state.get("done_tasks", []),
        entities,
        read,
        feedback=state.get("feedback", []) if revising else (),
        previous=[Filed(**f) for f in state.get("agent_filed", [])] if revising else (),
        dropped=[
            r.removeprefix("finalize: ")
            for r in state.get("agent_stats", {}).get("refused", [])
            if r.startswith("finalize: dropped line")
        ]
        if revising
        else (),
        open_tasks=open_tasks,
        proposals=proposals if revising else (),
    )
    skills = (runtime.context or {}).get("skills_dir")
    agent = build_ingest_agent(
        vault,
        nodes.chat_model(runtime),
        Path(skills) if skills else SKILLS_DIR,
        fallbacks=nodes.fallback_models(runtime),
    )
    context = AgentContext(
        StagingContext(
            note,
            citable,
            entities,
            read,
            task_projects=task_projects,
            feedback=tuple(state.get("feedback", [])) if revising else (),
            open_tasks=open_tasks,
        )
    )
    started = time.monotonic()
    with get_usage_metadata_callback() as usage:
        start: dict[str, Any] = {"messages": [HumanMessage(brief)]}
        if revising:  # the previous round stays staged; the agent changes it
            start |= {
                "staged": list(previous_edits),
                "filed": [Filed(**f) for f in state.get("agent_filed", [])],
                "mappings": [(p.line, p.project, p.why) for p in proposals],
            }
        result = agent.invoke(
            start,
            context=context,
            config={"recursion_limit": RECURSION_LIMIT},
        )
    tool_names = [
        c["name"]
        for m in result["messages"]
        if isinstance(m, AIMessage)
        for c in m.tool_calls
    ]
    calls = result.get("mappings", [])
    staged, filed, problems = finalize(
        result.get("staged", []),
        result.get("filed", []),
        result.get("withdrawn", []),
        ctx=with_mappings(context.staging, calls),
        keep=mapped_pages(calls)
        | task_people_pages(context.staging, result.get("staged", [])),
    )
    kept_proposals, mapping_problems = finalize_mappings(calls, staged, context.staging)
    refusals = [*result.get("refusals", []), *problems, *mapping_problems]
    stats = {
        "seconds": round(time.monotonic() - started, 1),
        "model_calls": sum(isinstance(m, AIMessage) for m in result["messages"]),
        "tool_calls": len(tool_names),
        "calls_by_tool": {n: tool_names.count(n) for n in sorted(set(tool_names))},
        "refusals": len(refusals),
        "refused": refusals,
        "tokens": {
            m: {k: u.get(k, 0) for k in ("input_tokens", "output_tokens")}
            for m, u in usage.usage_metadata.items()
        },
    }
    questions = [
        {"line": line, "text": text}
        for line, text in dict.fromkeys(map(tuple, result.get("questions", [])))
    ]
    return {
        "agent_edits": staged,
        "fact_edits": staged + _win_edits(state, staged, read),
        "agent_filed": [dataclasses.asdict(f) for f in filed],
        "task_proposals": kept_proposals,
        "questions": questions,
        "agent_stats": stats,
        "round_warnings": _for_the_user(refusals),
    }


def _for_the_user(refusals: list[str]) -> list[str]:
    """Return what the review should say about this round: only what the user can use.

    Facts code dropped at the end and refused questions are listed (the user may want
    them, e.g. via revise); refused calls the agent could still fix are only counted
    (details in `agent_stats` and the trace). The agent's questions are not warnings:
    the review has a section for them.
    """
    lost = [r.removeprefix("ask_user: ") for r in refusals if r.startswith("ask_user:")]
    dropped = [
        r.removeprefix("finalize: ")
        for r in refusals
        if r.startswith(("finalize: dropped line", "finalize: dropped task line"))
    ]
    other = len(refusals) - len(lost) - len(dropped)
    out = [f"the agent's question was refused: {r}" for r in lost]
    out += [f"not filed: {r}" for r in dropped]
    if other:
        out.append(f"code refused {other} of the agent's calls (details in the trace)")
    return out


def _win_edits(
    state: AgentIngestState, agent_edits: list[Edit], read: VaultReader
) -> list[Edit]:
    """Return edits for the done tasks the user picked as wins (no model involved)."""
    chosen = set(state.get("chosen_wins", []))
    wins = [t for t in state.get("done_tasks", []) if t.line in chosen]
    created = proposed_paths(agent_edits)
    return win_edits(state["note"], wins, lambda p: read(p) is not None or p in created)


def pick_wins(
    state: AgentIngestState, runtime: Runtime[IngestContext]
) -> dict[str, Any]:
    """After a review "answer": the agent's edits plus the picked wins (then map_tasks)."""
    vault = nodes.vault_path(runtime)
    agent_edits = state.get("agent_edits", [])
    wins = _win_edits(state, agent_edits, vault_reader(vault))
    return {"fact_edits": agent_edits + wins, "round_warnings": []}


def after_review(state: AgentIngestState) -> str:
    """Apply on approve, end on reject, ask the agent again on revise, else re-render."""
    action = state.get("review_action")
    if action == "approve" and state.get("approved"):
        return "apply"
    if action in ("approve", "reject"):
        return END
    if action == "answer":
        return "pick_wins"
    return "stage" if action == "revise" else "render"


builder = StateGraph(
    AgentIngestState, context_schema=IngestContext, input_schema=IngestInput
)
builder.add_node("load_note", models_in_node(nodes.load_note))
builder.add_node("assign_ids", models_in_node(nodes.assign_ids))
builder.add_node("track_tasks", models_in_node(nodes.track_tasks))
builder.add_node("map_tasks", models_in_node(nodes.track_tasks))
# A stage retry reruns the whole agent, after the model middleware's own retries and
# fallbacks gave up: only for failures that may pass (network, 429, 5xx), not for a bad
# key, no credits or our own bugs. The wait gives a struggling provider a moment.
builder.add_node(
    "stage",
    models_in_node(stage),
    retry_policy=RetryPolicy(
        max_attempts=2, initial_interval=10.0, retry_on=is_transient
    ),
)
builder.add_node("pick_wins", models_in_node(pick_wins))
builder.add_node("render", models_in_node(nodes.render))
# The review shows the agent's filed facts: without `input_schema` the node would get
# only its annotated `IngestState` keys, and `agent_filed` would be dropped.
builder.add_node(
    "review", models_in_node(nodes.review_changes), input_schema=AgentIngestState
)
builder.add_node("apply", models_in_node(nodes.apply))

builder.add_edge(START, "load_note")
builder.add_conditional_edges(
    "load_note", models_in_node(nodes.after_load), ["assign_ids", END]
)
builder.add_edge("assign_ids", "track_tasks")
builder.add_edge("track_tasks", "stage")
builder.add_edge("stage", "map_tasks")
builder.add_edge("map_tasks", "render")
builder.add_edge("render", "review")
builder.add_conditional_edges(
    "review",
    models_in_node(after_review),
    ["stage", "pick_wins", "render", "apply", END],
)
builder.add_edge("pick_wins", "map_tasks")
builder.add_conditional_edges(
    "apply", models_in_node(nodes.after_apply), ["render", END]
)

graph = builder.compile(name="ingest")
