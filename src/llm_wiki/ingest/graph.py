"""The ingest graph (decision 0004): one daily note in, proposed diffs out.

    load_note ──(unchanged note / broken ledger)──→ END
        ↓
    assign_ids → track_tasks ─┬─ Send → extract (one per section with new prose) ─┐
                              └─ (no new prose) ─────────────────────────────────┴→ route → render
    render → review (interrupt) ──reject──→ END
               ├─ approve ───→ apply (write approved files) → END
               │                 └─ changed since review → render (fresh review)
               ├─ revise ────→ extract (again, with the feedback) → route → render → review
               ├─ make_page ─→ route  (a decision gets its own page)
               └─ edit ──────→ render (the user's own text for one page)

Each loop ends at a fresh review, so approval is always for the final diffs. Only
`apply` writes, and only the approved digests (3d). The graph is compiled
without a checkpointer: `langgraph dev`/Studio and the Agent Server bring their own, and
`runner.py` compiles `builder` with ours (decision 0005).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from llm_wiki.ingest import nodes
from llm_wiki.ingest.state import IngestContext, IngestInput, IngestState

builder = StateGraph(
    IngestState, context_schema=IngestContext, input_schema=IngestInput
)
builder.add_node("load_note", nodes.load_note)
builder.add_node("assign_ids", nodes.assign_ids)
builder.add_node("track_tasks", nodes.track_tasks)
# A model call can fail on the network; retry it alone, not the whole parallel step.
builder.add_node("extract", nodes.extract, retry_policy=RetryPolicy(max_attempts=3))
builder.add_node(
    "find_names", nodes.find_names, retry_policy=RetryPolicy(max_attempts=3)
)
builder.add_node("route", nodes.route)
builder.add_node("render", nodes.render)
builder.add_node("review", nodes.review_changes)
builder.add_node("apply", nodes.apply)

builder.add_edge(START, "load_note")
builder.add_conditional_edges("load_note", nodes.after_load, ["assign_ids", END])
builder.add_edge("assign_ids", "track_tasks")
builder.add_conditional_edges(
    "track_tasks", nodes.fan_out, ["extract", "find_names", "route"]
)
builder.add_edge("extract", "route")
builder.add_edge("find_names", "route")
builder.add_edge("route", "render")
builder.add_edge("render", "review")
builder.add_conditional_edges(
    "review",
    nodes.after_review,
    ["extract", "find_names", "track_tasks", "route", "render", "apply", END],
)
builder.add_conditional_edges("apply", nodes.after_apply, ["render", END])

graph = builder.compile(name="ingest")
