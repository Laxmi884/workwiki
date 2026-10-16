---
id: 0010
date: 2026-10-16
status: accepted
---

# 0010: One Backend interface, two backends: local runner and Agent Server

## Context

The user wants to learn the LangGraph Agent Server side (dev → up → build) **and** how not to depend on
it. An audit on 2026-10-16 (docs: `docs.langchain.com/langsmith/cli`, local-dev-testing,
deploy-standalone-server, checkpointers; plus the installed `langgraph-api` source) found the graphs
server-ready but two persistence systems in play:

- Exported graphs (`ingest`, `assistant`, `wiki`) compile without a checkpointer: correct. The server
  copies each graph with its own checkpointer and store, and `langgraph dev` refuses a graph that brings
  its own ("persistence is handled automatically by the platform").
- But `runner.py` compiles ingest again with a private `SqliteSaver`, and the `wiki` chat graph's
  `command` node and the review page (`review_web/app.py`) use that runner in-process. Their ingest
  threads are invisible to the server (Studio, threads API, Postgres under `up`), and ingest threads
  the server runs are invisible to them.

Other findings (violations V4–V7 in the 2026-10-16 log): inner deep agents invoked without the node's
`config` (no nested streaming/cancel); custom Pydantic types in state (allow-listed by our runner,
unverified under the server's serde); `langgraph.json` ignores `uv.lock` and defaults to Python 3.11;
`langgraph-api` 0.13.8 is end-of-life.

## Options

- Server-native only: simplest code, but the server becomes a requirement (reverses 0005).
- Embedded runner only: no server, but we keep re-building threads/interrupts/streaming ourselves.
- Both, behind one interface (ports and adapters): two small adapters, one contract test suite.

## Decision

**Both, behind one `Backend` interface** (user chose, 2026-10-16: wants to learn the server and how
not to depend on it).

```
review page · chat box · Telegram · CLI      → only call Backend
Backend: start_ingest · pending · review · answer · ask
  LocalBackend  (our runner + SQLite; no server, no account)
  ServerBackend (langgraph-sdk over HTTP; langgraph dev now, langgraph up / build later)
same graphs (ingest, assistant), compiled without a checkpointer
```

Selected by `WIKI_BACKEND=local|server`. Independence rules:

1. Graphs never bring persistence and never start other graphs' runs from inside a node; so the
   `wiki` chat graph (nested runner) goes; "/…" routing moves into the surfaces.
2. State stays storable by either backend (JSON-safe, or allow-listed in both serdes).
3. Surfaces import only `Backend`, never `IngestRunner` or `langgraph_sdk`.
4. Server-only features get a local equivalent or a fallback: streaming → local returns after the run;
   crons → launchd calling the local backend; auth → local binds to localhost only.
5. One contract test suite runs against `LocalBackend` always and `ServerBackend` opt-in (against
   `langgraph dev`).

What stays ours either way: staging checks, digests, apply + read-back, git commit, task index, vault
I/O, model setup, the review page HTML, vault-first memory (`rules.md`). What the server gives in
server mode: threads/checkpoints (Postgres under `up`), interrupt/resume over HTTP, streaming (SSE),
background runs and queue, Studio, crons, auth.

Build steps: S1 `Backend` + `LocalBackend` + contract tests; S2 `ServerBackend` (langgraph-sdk) +
opt-in test; S3 review page + chat box on `Backend`, delete the `wiki` graph and direct runner
imports; S4 pass the node `config` into inner agents; S5 new `langgraph.json` (below), upgrade
`langgraph-api`, `langgraph up` smoke test (volumes, serde); notebook 13 runs the same review flow on
both backends side by side (one notebook, not two copies of every notebook).

Target `langgraph.json` (S5):

```json
{
  "$schema": "https://langgra.ph/schema.json",
  "python_version": "3.12",
  "image_distro": "wolfi",
  "source": { "kind": "uv" },
  "graphs": {
    "ingest": "./src/llm_wiki/ingest_agent/graph.py:graph",
    "assistant": "./src/llm_wiki/assistant/graph.py:make_graph"
  },
  "env": ".env",
  "http": { "app": "./src/llm_wiki/review_web/app.py:app" }
}
```

Add `checkpointer.serde` only if the `up` smoke test shows our types failing; add `auth` and
`http.enable_custom_route_auth` before the server is reachable beyond localhost/Tailscale.

## Why

The graphs already do the real work and are server-ready; the only coupling is how surfaces reach
threads. One interface keeps the server a choice (0005's goal) while letting the user learn and use it,
and the contract tests stop the two backends from drifting.
