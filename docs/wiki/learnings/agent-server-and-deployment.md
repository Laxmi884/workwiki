---
phase: 8
date: 2026-10-16
tags: [langgraph, agent-server, deployment, langgraph-cli]
---

# The Agent Server: dev, up, build, and who owns persistence

## What it is

The LangGraph Agent Server runs your graphs behind an HTTP API (assistants, threads, runs, interrupts,
streaming, crons). The CLI starts it three ways:

| | `langgraph dev` | `langgraph up` | `langgraph build` |
|---|---|---|---|
| What | in-process dev server | local Docker stack | standalone server image |
| Persistence | "in-memory & pickled to local directory" | PostgreSQL (+ Redis for streaming) | whatever `DATABASE_URI` / `REDIS_URI` point at |
| Docker | no | yes | yes |
| Use it for | daily development, Studio, debugging | "production-like validation" | VPS / Kubernetes / LangSmith deploy |
| Account | free LangSmith key (for Studio/traces) | LangSmith key; "license key for production use" | `LANGSMITH_API_KEY` + `LANGGRAPH_CLOUD_LICENSE_KEY`, egress to `beacon.langchain.com` |

**The server owns persistence.** It copies every graph you export with its own checkpointer and store
(`langgraph_api/graph.py`: `graph_obj.copy(update={"checkpointer": ..., "store": ...})`). So exported
graphs must compile **without** a checkpointer; `langgraph dev` raises if one brings its own.

## How we used it

- `langgraph.json` exports `ingest` (`ingest_agent/graph.py:graph`) and `assistant`
  (`assistant/graph.py:make_graph`, a factory). A factory may take `ServerRuntime`, `RunnableConfig`,
  both or nothing (`langgraph_api/_factory_utils.py`); `runtime.execution_runtime.context` holds the run's
  context, and is None while the server only reads the schema.
- `http.app` mounts our review page (a Starlette app) on the same server; confirmed live ("Loaded
  custom app"). Custom routes get auth only with `http.enable_custom_route_auth`.
- `langgraph.json` keys worth knowing: `source: {"kind": "uv"}` installs from `uv.lock` (without it,
  `dependencies: ["."]` re-resolves from `pyproject.toml`); `python_version` defaults to 3.11;
  `checkpointer.ttl`, `checkpointer.serde` (allow-list custom types), `store.index` (semantic search),
  `auth.path`, `http.cors`.
- Decision 0010: surfaces reach threads through a `Backend` interface with a local and a server
  implementation, so the server stays a choice.

## Paid-media reference

`runtime/local.py` compiles the same components locally with `create_deep_agent(...,
checkpointer=InMemorySaver())`, and Managed Deep Agents compiles them in production: the same split
(graph code vs where persistence comes from) we now make explicit with two backends.

## Gotchas

- A graph that runs *another* graph with its own persistence inside a node (our old `wiki` chat graph
  calling `IngestRunner.sqlite`) creates threads the server can't see. Route in the surface instead.
- `langgraph dev` blocks synchronous I/O in async code (blockbuster: "Blocking call to os.mkdir").
  Plain `def` Starlette routes run in a worker thread; or `run_in_threadpool`. Sync graph nodes are fine.
- Our state holds custom Pydantic types; our runner allow-lists them for msgpack. Under the server,
  check `checkpointer.serde` before relying on it. Over HTTP, int dict keys come back as strings.
- `langgraph-api` 0.13.8 logs "End of Life"; upgrade before trying `up`.

## In my words

(for the user)
