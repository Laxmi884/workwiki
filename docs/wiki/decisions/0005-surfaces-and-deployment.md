---
id: 0005
date: 2026-10-14
status: accepted; amended by 0010 (two backends behind one interface)
---

# 0005: Surfaces and deployment: our own runner by default, servers optional

## Context

Review needs `interrupt()`, which needs a checkpointer and a thread ID. Studio, `langgraph dev`,
LangSmith Deployment and the self-hosted Agent Server provide both, but the user wants Telegram or
Slack as interfaces, low cost, and an easy setup if the project is open-sourced.

Checked 2026-10-13/14:
- LangSmith pricing page: "Plus plans include 1 free small serverless deployment." The free
  Developer plan includes no hosted deployment; Plus is $39/seat/month plus usage.
- Self-hosted standalone Agent Server (docs "Self-host standalone servers"; what `langgraph up`
  runs): Docker image `langchain/langgraph-api` + Postgres + Redis. Docs list `LANGSMITH_API_KEY`
  and `LANGGRAPH_CLOUD_LICENSE_KEY`, and egress to `beacon.langchain.com` for licence checks.
- Tested locally (images already present: `langchain/langgraph-api:3.12`, `postgres:16`,
  `redis:7`, a past `lg-spike` image): with only a LangSmith API key from the free account the
  server logged "No enterprise license key found, running in lite mode with LangSmith API key",
  started, ran a graph and saved a checkpoint in Postgres. It checks the key online at every start.
- LangGraph itself (MIT) and `langgraph-checkpoint-sqlite` are free and need no account.

## Options

- LangSmith Deployment only: managed, but paid and needs an account.
- Self-hosted Agent Server only: free in lite mode today, but three containers, a key check at
  each start, terms set by LangChain.
- Own runner + SQLite checkpointer, servers optional: free, one process, no account; we write a
  small runner layer.

## Decision

- The graph is compiled without a checkpointer (`ingest/graph.py`), so every mode can use it.
- Default: `runner.py` compiles it with our checkpointer (SQLite file outside the vault, e.g.
  `~/.llm-wiki/state.db` via `WIKI_STATE_DB`; in-memory in tests) and offers `start(note)`,
  `resume(thread, answer)` and later `pending()`. The review screen and the answer are plain models
  (`ReviewRequest`, `ReviewAnswer`), not tied to any UI.
- Surfaces are thin adapters over the runner: CLI (phase 7), Telegram, Slack. Studio stays for
  development through `langgraph.json`.
- Optional: self-hosted Agent Server (`langgraph up` / Docker Compose, free lite mode with a
  LangSmith key) and LangSmith Deployment (Plus). Same graph, no code changes.
- Any always-on host (VPS, Docker, Fly, Modal) needs the vault there too: a git clone synced
  with the Mac (pull before ingest, push after commit). Phase 8.

- **Addendum 2026-10-16 (user asked whether the UI should wait for the Docker server): no.**
  The wiki chat (4d) and the review page (0009) are adapters over the runner. The page is a
  Starlette app mounted through `langgraph.json` `"http": {"app": ...}`, which the CLI applies to
  every server built from that file (`dev`, `up`, deploy), and it can also run standalone. Phase 8
  adds only hosting work: vault and `WIKI_STATE_DIR` as mounted volumes, auth on custom routes
  (`enable_custom_route_auth`) before the page is reachable beyond localhost/Tailscale, and the
  vault git clone synced with the Mac.

## Why

Pause/resume only needs a checkpointer and a thread ID, which we can own for free; keeping the
graph checkpointer-free lets the paid or licensed servers stay an option instead of a requirement.
