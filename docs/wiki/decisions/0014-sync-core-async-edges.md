---
id: 0014
date: 2026-10-17
status: accepted
---

# 0014: Sync core, async only at the edges

## Context

The user asked (2026-10-17) whether the agents should be async and stream. Every run is a blocking call:
- `invoke()` in the runner, `ask`, `backend/local` and the ingest `stage` node;
- `runs.wait()` in `backend/server`.

The answer appears only when the run is complete. Checked the same day:
- A graph with an async-only node can't be run with `invoke()` (`TypeError: No synchronous function provided`).
- A sync node under `ainvoke()` runs in a worker thread, and the event loop kept running (9 ticks during a 0.5 s blocking sleep).
- The page routes already use `run_in_threadpool`, and `make_graph` uses `asyncio.to_thread`.

## Options

- **Convert everything to async.**
  - Pro: many runs on one thread.
  - Con: async spreads through every caller (runner, `ask`, tests, `AsyncSqliteSaver`), so sync and async paths both have to be kept; any stray blocking call freezes the loop; one user rarely runs two things at once.
- **Sync core, async at the edges.**
  - Write async code only where the framework calls it: middleware `awrap_*` versions, Starlette routes, the server's graph factory.
  - Our own nodes, tools, runner and CLI stay sync.
  - Stream with the sync APIs: `agent.stream(stream_mode="messages")`, the sync client's `runs.stream(...)`, and a Starlette streaming response fed by a sync iterator.

## Decision

Sync core, async at the edges. Streaming comes with the phase 7 surfaces and doesn't need an async conversion.

## Why

For a single-user tool, async adds a second path for everything plus a class of loop-blocking bugs, and gains
nothing: the server already runs our sync graphs without blocking. Revisit for one piece, with a test showing the
gain, when one of these happens:
- several users or surfaces run at the same time (e.g. a Telegram bot serving many chats);
- a node should fire several model calls at once;
- the Agent Server runs out of worker threads.
