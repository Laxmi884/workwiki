---
phase: 3
date: 2026-10-14
tags: [langgraph, interrupt, checkpointer, hitl]
---

# interrupt() and our own runner

## What it is

`interrupt(payload)` inside a node pauses the graph and hands `payload` to whoever is running it.
The run's state is saved by a checkpointer under a thread ID; `Command(resume=answer)` on the same
thread continues it, and `interrupt()` returns `answer`. Pausing needs nothing else: any
checkpointer (in-memory, SQLite, Postgres) and any thread ID will do.

## How we used it

- `src/llm_wiki/ingest/nodes.py` `review_changes`: builds a `ReviewRequest`, calls `interrupt`, checks
  the answer with `review.check_answer`, and asks again (with `error` set) until it is valid.
- `src/llm_wiki/domain/review.py`: `ReviewRequest` / `ReviewAnswer`, plain JSON-able models any UI
  can show (decision 0005).
- `src/llm_wiki/runner.py`: `IngestRunner` compiles `ingest.graph.builder` with our checkpointer
  and offers `start`, `resume`, `status`; `in_memory()` for tests and quick tries.
- The graph module stays checkpointer-free, so `langgraph dev`/Studio and the Agent Server use it
  unchanged with their own.

## Paid-media reference

`surfaces/runner.py` plays the same role there: the one place surfaces call, building the thread
config and resuming with `Command`. It also records which caller owns a thread
(`ThreadOwnershipStore.claim`, `caller_ref`) so one user can't resume another's run; we'll need
that once Telegram/Slack exist. `docs/architecture/writes-and-approvals.md` and
`surfaces-and-presentation.md` describe their approval flow and surfaces.

## Gotchas

- A resumed node runs again from the top (checked: 3 runs for 2 answers), so nothing before
  `interrupt()` may have side effects. Several `interrupt()` calls in one node are matched to
  answers by order.
- Without a checkpointer `invoke` still stops at the interrupt and returns the state so far; it
  just can't be resumed.
- The checkpointer stores our Pydantic models and enums and warned "Deserializing unregistered
  type … will be blocked in a future version". `runner.checkpoint_serializer()` allow-lists every
  model and enum in `llm_wiki.domain` (found automatically); 9 warnings → 0.

## In my words

