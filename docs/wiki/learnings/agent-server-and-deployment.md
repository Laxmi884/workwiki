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

- `langgraph.json` exports `ingest` (`ingest/graph.py:graph`) and `assistant`
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
- Found building `ServerBackend` (S2, 2026-10-16), all against `langgraph dev`:
  - The server calls graph **factories on its event loop**, so blocking work inside one fails
    (`Path.resolve()` → "Blocking call to os.readlink"). The real `assistant` factory had this bug;
    now `async def make_graph` builds in `asyncio.to_thread` (`assistant/graph.py`).
  - A factory gets the runtime only if its parameter is **annotated `ServerRuntime`** (the server
    reads the annotation, `langgraph_api/_factory_utils.py`); with `Any` it gets the config dict.
  - `runs.wait` on a failed run **doesn't raise**: it returns `{"__error__": {"error": "<exception
    class>", "message": "An internal error occurred"}}` and the thread's status is `error`.
  - `threads.get("not-a-uuid")` is a 422, an unknown UUID a 404.
  - Graph paths in `langgraph.json` resolve from the **working directory**, not the config file's
    folder; the test server writes its config with absolute paths into a temp dir.
  - Our Pydantic state survives the dev server's checkpointer (full review → approve → write ran);
    over HTTP the values are JSON (`date` came back as `[2026, 10, 12]`).
  - A run without a thread (`runs.wait(None, ...)`) leaves no thread behind: right for `ask`.
- Found in S3 (2026-10-16): the SDK's sync client builds its transport with `retries=5`, so a stopped server makes each call hang ~7.5 s before `httpx.ConnectError` (tests pass their own `SyncLangGraphClient(httpx.Client(...))`). Every SDK error (`NotFoundError`, …) subclasses `httpx.HTTPError`; `ServerBackend` turns whatever escapes into `BackendError` (`_reachable`), so surfaces catch one error type.
- Found in S4 (2026-10-16): a compiled graph invoked inside a node is a subgraph. Its `checkpointer` decides what it stores: `None` (default) writes per-invocation checkpoints into the parent's store, `True` keeps per-thread memory, `False` stores nothing (no interrupts inside). Config (callbacks, streaming) reaches it either way without being passed. Our ingest agent is `False`.

- Found in S5 (2026-10-16), `langgraph up` / `build` (notebook 6.2):
  - The CLI writes the Dockerfile **file by file** from the project root, filtered only by `.dockerignore`
    (and `.venv`, `.git`, caches). Without one, `.env` (API keys), `.langgraph_api/`, notebooks and tests
    went into the image. Ours is an allow-list (`*`, then `!src/`, `!pyproject.toml`, ...).
  - `source: {"kind": "uv"}` installs from `uv.lock` with `uv export --frozen`, which **includes the `dev`
    group**; no CLI flag drops it, `dockerfile_lines: ["ENV UV_NO_DEV=1"]` does (the line lands right after `FROM`).
  - The image installs the project **editable** (`uv pip install -e .`), so it reads `src/` and hid that our
    wheel had no subpackages and no skills (`packages = ["llm_wiki"]`). Fixed with `packages.find` + `package-data`.
  - `checkpointer.serde.allowed_msgpack_modules` is in the CLI schema but `langgraph-api` 0.16.1's
    `Serializer` passes on only `allowed_json_modules`. Unlisted types restore with a warning by default; under
    `LANGGRAPH_STRICT_MSGPACK=true` they come back as **plain dicts** (an `AttributeError` far from the cause).
    Fixed by storing ingest state as plain JSON (`ingest/state.py`), with strict serde everywhere (0011).
  - `up` rebuilds every time (`pull_policy: build`); an extra compose file (`-d`) is merged **before** the
    generated one, so it can add env and volumes but can't remove the generated ports (API and Postgres on all
    interfaces). Two `up` stacks collide on host port 5433. So the real stack is our `compose.yml` on the
    `langgraph build` image (0011). `COMPOSE_PROJECT_NAME` keeps a test stack's `down --volumes` away from a real one.
  - Docker Desktop can't mount from `~/Documents`, `~/Desktop`, `~/Downloads` without a macOS privacy grant.
  - The vault must be mounted at the **same absolute path** (the server backend sends the client's path), and
    the review page inside the server needs `WIKI_BACKEND=server` (default `local` = its own SQLite threads).

## In my words

(for the user)
