---
id: 0008
date: 2026-10-16
status: accepted
---

# 0008: The assistant deep agent (phase 4)

## Context

Ingest now keeps the wiki (0006, 0007). Phase 4 adds the second agent from CLAUDE.md: Q&A over the
vault, and review synthesis (brag sheet) for performance reviews. Deep Agents API checked against the
current docs (context7, 2026-10-16): `FilesystemPermission` rules are first-match-wins and anything
unmatched is allowed; `mode="interrupt"` pauses for approval; a subagent's `permissions` replace the
parent's; custom subagents don't inherit skills; `response_format` gives the parent JSON.

## Options

- Writes: none in phase 4, or `write_file` under `05_generated/` with `mode="interrupt"`.
- Review subagent: structured draft with sources now, or Q&A only first.
- Surface: Studio + a small `ask` script, or Studio only.

## Decision

- **Graph `assistant`** (`create_deep_agent`) replaces the template's toy `agent` graph in
  `langgraph.json`.
- **Read-only vault** mounted at `/vault/` as a `CompositeBackend` route; the default backend stays
  in-memory state (deepagents writes oversized tool results to the default backend past
  permissions, learned in 0006). Filesystem tools limited to `read_file`, `ls`, `glob`, `grep`, and
  every write denied by permission rules. **No writes in phase 4** (user chose): saving to
  `05_generated/` comes in phase 7 through the same digest → approval → apply → git chain as ingest.
- **Code tools** for deterministic data: `find_tasks` over `90_meta/tasks.json` (filters by
  project or "none", person, status open/done/cancelled/all, since; IDs and statuses from code,
  never parsed by the model).
- **Skills** in the repo: vault layout, answering rules (cite page and date; "the notes don't say";
  never invent numbers), brag-sheet rules. **Memory:** `90_meta/rules.md`, shared with ingest.
- **Subagent `review-writer`** (user chose "draft with sources"): given a period, reads impact pages,
  project pages and decision logs and returns a structured draft (`response_format`): per project,
  wins and help given with date and `[[source]]`, metrics only as written, and a list of gaps. Its
  own skills and read-only permissions.
- **Surface** (user chose): Studio via `langgraph dev`, plus `python -m llm_wiki.ask "question"`.
  The real CLI is phase 7. `langgraph.json` registers `assistant` as a graph factory
  (`make_graph(runtime: ServerRuntime)`), so a run's context can set `vault_path` and `model`; it
  replaces the template's toy `agent` graph. Model setup shared with ingest in `model_setup.py`.
- Built in steps 4a (agent, tools, skills, tests), 4b (review-writer), 4c (`ask`, `langgraph.json`);
  notebook 12 after.

- **Step 4d, one chat for both agents (user asked, 2026-10-16; chose runner-backed commands, built
  now):** graph `wiki` (`chat/graph.py`) routes by code: a leading `/` is a command (`/ingest
  [date]`, `/review`, `/approve [except f2]`, `/reject`, `/revise <text>`, `/edit f3` + text,
  `/tasks m1=…`, `/wins …`, `/help`), anything else goes to the assistant. Ingest runs in its own
  SQLite thread through `IngestRunner`; the chat stores only the open ingest thread id, so the review
  keeps its digests and rules, survives restarts, and questions work while it waits. One open review
  at a time. Reviews show as Markdown (files, diffs, tasks, wins, warnings); the review HTML stays in
  phase 7.

## Why

Same split as ingest: code owns deterministic data (tasks, paths, permissions), the model reads and
writes prose. Read-only first keeps the vault's only write path the reviewed, committed one.
