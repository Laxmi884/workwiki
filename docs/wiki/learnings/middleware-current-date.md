---
phase: 5
date: 2026-10-17
tags: [langchain, middleware, deepagents]
---

# Middleware: the current date

## What it is

Middleware is code that runs around each model call (or tool call) in an agent loop. A `wrap_model_call` hook
gets the request, can change it (`request.override(...)`) and calls `handler` to run the rest of the stack. The
smallest useful one tells the model today's date, because its own idea of "now" is its training data.

## How we used it

- `src/llm_wiki/middleware/current_date.py`: `date_line` (weekday and quarter worked out by code), `with_line`
  (append, keeping a string prompt a string), `CurrentDateMiddleware(today=datetime.date.today)`.
- `src/llm_wiki/assistant/agent.py`: one instance goes to the main agent and to `review_writer()`;
  `build_assistant(today=...)` lets tests pass a clock. The ingest agent doesn't get it: its "today" is the note's
  date, already in its brief.
- Three hooks compared (notebook 4.2): `wrap_model_call` changes one call; `@dynamic_prompt` *replaces* the
  system prompt (would drop what other middleware added); `before_model` writes to state, so the checkpointer saves
  the date and a thread resumed tomorrow carries yesterday's.
- Tests: `tests/unit_tests/test_current_date.py`.

## Paid-media reference

`middleware/current_date.py` (`CurrentDateMiddleware`): same shape, UTC date only, string or block content. We use
the local date (daily notes are named by the user's day) and add weekday and quarter.

## Gotchas

- First middleware in the list is the outermost, so its line comes first. `create_deep_agent` inserts ours after
  its core but before skills, prompt caching and memory: the date ends our prompt, then the skills middleware adds
  a second text block. So the system message the model gets is a list of blocks, not a string.
- Custom subagents get only their own `middleware`; the built-in general-purpose subagent inherits only middleware
  whose `name` replaces one of its default slots. A new name (`CurrentDate`) doesn't reach it.
- A container on UTC gives the wrong day around midnight; set `TZ`.

## In my words

