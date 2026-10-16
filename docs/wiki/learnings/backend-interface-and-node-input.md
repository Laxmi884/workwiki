---
phase: 4
date: 2026-10-16
tags: [langgraph, architecture, testing]
---

# A Backend interface, and what a node actually receives

## What it is

**Ports and adapters.** Surfaces (review page, chat, CLI) talk to one small interface, `Backend`
(`start_ingest`, `pending`, `review`, `answer`, `ask`); each way of running the agents is an
adapter behind it. One contract test suite runs against every adapter, so they can't drift apart.

**Node input is filtered by the node's type hint.** LangGraph gives a node only the state keys of
the schema it is annotated with (`def review_changes(state: IngestState, ...)`), not every key on
the thread. A key that only the graph's wider state declares is silently missing (`.get()` returns
the default). `add_node(..., input_schema=WiderState)` overrides it.

## How we used it

- `src/llm_wiki/backend/base.py`: `Backend` (a `typing.Protocol`), `RunResult` (moved from
  `runner.py`), errors `NoteNotFound`, `UnknownThread`, `NoOpenReview`. A backend serves one vault;
  another vault's threads are unknown to it.
- `src/llm_wiki/backend/local.py`: `LocalBackend` wraps `IngestRunner` (SQLite) and builds the
  assistant per `ask`; the surface keeps chat history and passes it in.
- `tests/unit_tests/test_backend_contract.py`: `make(vault)` fixture parametrized by backend
  (`local` now, `server` in S2). Checked by breaking `LocalBackend` twice: each break failed a test.
- The review page used to read thread state (`note`, `agent_filed`, `task_ids`). Over the server
  those come back as JSON, so the review itself now carries them: `ReviewRequest.facts`
  (`ReviewFact`: path, section, text, line, quote), `note_date`, `task_ids_added`.
- Node input: `src/llm_wiki/ingest_agent/graph.py` adds `review` with
  `input_schema=AgentIngestState`, otherwise `agent_filed` never reached `review_changes`.

## Paid-media reference

`surfaces/runner.py` (`AgentRunner` returning a frozen `RunOutcome`, `ThreadAccessDenied` for
threads the caller doesn't own) over `runtime/local.py` and `runtime/self_hosted.py`. Same idea:
surfaces never touch the graph or the server client directly. Differs: theirs streams events
(`RunEvent`); ours returns after the run pauses (streaming is S4).

## Gotchas

- The first version of the enriched review had no facts at all, with no error: the thread had
  `agent_filed`, but the node was annotated with the narrower `IngestState`. A test that checked
  the facts by value caught it.
- `InMemorySaver.list(None)` is not globally newest-first (it walks thread by thread), so
  `pending()` sorts threads by their latest checkpoint `ts` itself.

## In my words

_(for the user)_
