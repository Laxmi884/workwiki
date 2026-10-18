---
phase: 5
date: 2026-10-17
tags: [deepagents, middleware, context]
---

# Offload

## What it is

Keeping a result that is too big out of the model's context: the full text goes to a store, and the model gets a
short stub with a preview and a path it can page through. It's one of three answers to "too big":
- **cap** it in the tool;
- **page** it (the caller asks for a slice);
- **offload** it.

## How we used it

- No middleware of ours. Deep Agents' `FilesystemMiddleware` offloads a tool result over 20,000 tokens (`tool_token_limit_before_evict`, to `/large_tool_results/`) and a user message over 50,000 (`human_message_token_limit_before_evict`, to `/conversation_history/`). Paged tools (`read_file`, `grep`, `glob`, `ls`, ...) are skipped.
- Our `CompositeBackend` sends those paths to the in-memory default route, so nothing lands in the vault.
- `src/llm_wiki/assistant/tools.py`: `find_tasks` caps itself (`MAX_TASKS` = 60, then "... and N more; narrow the filters"), so it never needs offloading.
- Ingest is where it matters: the brief carries the whole note, and a note with a pasted log can be huge.
- Tests: `tests/unit_tests/test_offload.py`. Notebook 4.5.

## Paid-media reference

`middleware/offload.py` (`ResultOffloadMiddleware`) writes big tool results to an artifact store and returns a stub. It skips paged tools (`PAGED_TOOLS`) for the same reason Deep Agents does. Paid-media needs its own because its provider reports are typed artifacts that deterministic tools read by id; ours aren't.

## Gotchas

- An offloaded user message goes to `/conversation_history/`, not `/large_tool_results/`.
- The store holds raw text, but redaction works on the request, so text read back is cleaned like anything else.
- A capped tool beats an offloaded one when the model can narrow the question; offload fits when the whole result matters.

## In my words

