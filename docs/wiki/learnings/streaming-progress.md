---
phase: 8
date: 2026-10-18
tags: [langgraph, streaming, langgraph-sdk, slack]
---

# Streaming a run as steps

## What it is

`graph.stream(..., version="v2")` yields one part per event, always `{"type", "ns", "data"}`:
- `updates`: a node finished;
- `custom`: a node's own event, written with `runtime.stream_writer` / `get_stream_writer()`;
- `messages`: LLM tokens with metadata.

`subgraphs=True` also streams graphs that run *inside* a node, tagged by namespace (`ns = ("stage:<id>",)`). We turn those parts into a frozen `Progress` (steps plus answer text) with one pure function. Every surface only draws `Progress`: "the stream is the contract" (LangGraph frontend docs).

## How we used it

- `src/llm_wiki/progress.py`: `step_plan`, `advance` (one part → new `Progress`), `follow` (yields changes, returns the last), `settle` (no step left running).
- `src/llm_wiki/ingest/nodes.py::report`: custom events `{"step", "detail", "state"}`, used by `stage` ("facts filed: n" / "nothing new to file") and `apply` ("commit abc1234" / "not committed").
- `src/llm_wiki/runner.py` (`stream_start`, `stream_resume`), `backend/local.py`, `backend/server.py`: `stream_ingest` / `stream_answer` / `stream_ask` yield `Progress`, then one `Finished` holding what the blocking call returns.
- `src/llm_wiki/chat/session.py`: `stream_reply`, which `reply()` now drains.
- `src/llm_wiki/ingest/staging.py::needs_agent`: quiet notes (rollover-only, tick-only) skip the agent, $0. A task line is code's only if its ID is in the task index with the same words; added words are news.
- Tests: `test_progress.py`, `test_quiet_notes.py`, the `stream` tests in `test_backend_contract.py` (both backends), `test_chat.py` (every test through `reply` and `stream_reply`). Notebook 8.3.

## Paid-media reference

`src/paid_media_agent/surfaces/slack/delivery.py` maps a run's events onto Slack's `chat_stream`, and `surfaces/runner.py` drives the run. They stream straight to Slack. We put a surface-neutral `Progress` in between, so Slack, the chat page and the CLI share one interpretation.

## Gotchas

- **Server names differ.** The SDK uses `stream_subgraphs=True` and `stream_mode="messages-tuple"`; the parts still arrive as type `"messages"`. Data is JSON (dicts/lists, `ns` a list), so `advance` reads message fields from objects or dicts.
- **Server errors.** A failed run arrives as an `error` part, not an exception; `_errors_raised` turns it back into `NoteNotFound` / `BackendError`.
- **Decorators on generators.** A `try` in a decorator doesn't cover a generator's body, which runs later while iterating. `_reachable_stream` wraps the `yield from` itself.
- **Text before a tool call is a preamble.** It's reset when the `tools` node runs, and the final `model` update replaces the streamed text.
- **Revise reruns a step the plan lacks.** A revise reruns `stage` inside an *answer* run, where the plan has no `stage` step: unknown steps must be ignored (found by the parametrised chat tests).
- **A review answer's steps depend on the action.** `answer_plan(answer)`: approve → apply + commit, revise → the agent again + a new review, reject → the review closes. A revise resumes the *old* `review` node first, so a step still `pending` ignores its node's update.
- **`ask()` and `stream_ask` must read text the same way.** Anthropic models answer in content blocks; `str(content)` showed a Python list. Both use `progress.message_text` (contract test: a search, then a content-block answer).
- **Stopping a stream early differs.** The server finishes the run (`on_disconnect="continue"`); locally the run stops after the current step. Surfaces drain the stream.
- **Scripted fakes don't stream tool calls.** `ToolScript` can't stream a tool-call message ("No generations found in stream"); tests never ask for `messages` on ingest, so the notebook uses a small streaming fake.
- **Docker contract run was already failing.** `WIKI_SERVER_TESTS=docker` fails on the unchanged code too (`StopIteration` in `stage`'s scripted agent). That's unrelated to streaming and still to investigate.

## In my words

