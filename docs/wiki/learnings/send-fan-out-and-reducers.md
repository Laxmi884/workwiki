---
phase: 2
date: 2026-10-13
tags: [langgraph, send, reducers]
---

# Send fan-out and reducers

## What it is

A conditional edge can return a list of `Send("node", payload)` instead of a node name. LangGraph
then runs that node once per `Send`, in parallel, each with its own small input. Their outputs
meet again in the shared state through reducers, which say how two updates to the same key
combine.

## How we used it

- `src/llm_wiki/ingest/nodes.py` `fan_out`: one `Send("extract", {note, section_line, previous,
  feedback})` per prose section; a note with no prose returns `"route"` and skips the model.
- `src/llm_wiki/ingest/state.py`: `facts` uses `merge_facts` (dict merge by section key, so a
  revise round replaces its own section), `warnings` uses `operator.add`. Keys without a reducer
  (`fact_edits`, `diffs`) are overwritten, so `route` rebuilds them from scratch each round.
- `src/llm_wiki/ingest/graph.py`: `add_conditional_edges("track_tasks", fan_out, ["extract",
  "route"])`; `extract` has `RetryPolicy(max_attempts=3)`.
- Test: `tests/unit_tests/test_ingest_graph.py` runs the compiled graph with a fake model that
  answers by section heading.

## Paid-media reference

Paid-media doesn't use `Send` (checked 2026-10-13: no `Send(` in `src/`). The closest idea is
`tools/compute.py`: deterministic grouping and arithmetic in code, like our `routing.py` deciding
where each fact goes instead of the model.

## Gotchas

- Parallel branches finish in any order: a fake model that replies "in order" is flaky, so the
  test fake answers by section heading.
- A parallel step is all-or-nothing: if one branch raises, none of the step's updates apply. A
  `RetryPolicy` on `extract` retries only the failed branch.
- `input_schema=IngestInput` keeps the Studio input form to just `note_path`.

## In my words

