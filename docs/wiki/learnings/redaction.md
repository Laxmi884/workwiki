---
phase: 5
date: 2026-10-17
tags: [langchain, middleware, deepagents, safety]
---

# Redaction

## What it is

Replacing credential-shaped text (tokens, passwords, database URLs, our own API keys) with `[REDACTED_SECRET]`
before a cloud model sees it. Daily notes are scratchpads, and every agent sends what it reads to the provider.

## How we used it

- `src/llm_wiki/middleware/redaction.py`:
  - `PATTERNS` and `find_secrets`: credential shapes; the password in a URL; `key = value` when the value looks generated (8+ characters with a digit); `secret_values()` (this process's `*_KEY`/`*_TOKEN`/`*_SECRET`/`*_PASSWORD`). Overlapping spans are merged.
  - `redact_content` handles strings and text blocks.
  - `RedactionMiddleware` cleans the request in `wrap_model_call` (system message + every message) and never the state.
- Ingest: the brief (the whole note) and page reads. Assistant: the question, pages, `find_tasks`, and both subagents.
- `src/llm_wiki/assistant/agent.py`: `general_purpose()` replaces Deep Agents' built-in subagent with a spec of the same name and prompt (`GENERAL_PURPOSE_SUBAGENT`). `shared_middleware()` gives it, and the review-writer, the date, redaction, a tool list and read-only files.
- Tests: `tests/unit_tests/test_redaction.py`. Notebook 4.4.

## Paid-media reference

`middleware/redaction.py`: the same detector idea (patterns + known values), applied to tool results in `wrap_tool_call`, plus sanitized exceptions. Ours needs the request instead, because the note itself is in the first message.

## Gotchas

- LangChain's `PIIMiddleware` rewrites the state in `before_model`. In a deep agent the rewritten copy has no message ID, and Deep Agents' reducer appends ID-less messages, so the model got the original *and* the copy (deepagents#3357). It also checks only the newest user message (langchain#40703). It works on a plain `create_agent`.
- A test that "proves" redaction must actually put the secret in front of the model: `grep`'s default output is file names only, so a subagent test using it passed with redaction off.
- Not covered: plain-word passwords; code-owned copies of the user's text (`tasks.json`, `open-tasks.md`, local only); LangSmith traces of graph state and hook inputs (`TracePolicy` is the hook-span lever, a follow-up).

## In my words

