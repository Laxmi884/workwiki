"""The ingest graph (decision 0004): one daily note in, proposed diffs out.

    load_note ──(unchanged note / broken ledger)──→ END
        ↓
    assign_ids → track_tasks ─┬─ Send → extract (one per section with new prose) ─┐
                              └─ (no new prose) ─────────────────────────────────┴→ route → render

Nothing is written to the vault yet; phase 3 adds review (`interrupt`) and apply.
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
builder.add_node("route", nodes.route)
builder.add_node("render", nodes.render)

builder.add_edge(START, "load_note")
builder.add_conditional_edges("load_note", nodes.after_load, ["assign_ids", END])
builder.add_edge("assign_ids", "track_tasks")
builder.add_conditional_edges("track_tasks", nodes.fan_out, ["extract", "route"])
builder.add_edge("extract", "route")
builder.add_edge("route", "render")
builder.add_edge("render", END)

graph = builder.compile(name="ingest")
