---
id: 0009
date: 2026-10-16
status: accepted
---

# 0009: HTML review page, and "remember"

## Context

Reviews reach the user as JSON (runner) or Markdown (wiki chat, 4d). To see what changed and answer
properly the user wants a page: files with checkmarks, diffs, edits, task → project choices, wins,
revise, approve, from a browser or a phone. Telegram/Slack can stay quick "approve all / reject".
The user also wants the agents to remember preferences. Planned for phase 7; the user pulled it
forward (2026-10-16).

## Options

- When: next (after 4d), or phase 7.
- Remember: proposed in the review like any change, or written at once.
- Granularity: whole files, or per-fact toggles.

## Decision

- **Build it next** (user chose). **One review, many windows:** the pending review is the ingest
  thread in SQLite; the page, the wiki chat, Studio and later Telegram/Slack all read and answer the
  same thread. Answering anywhere ends it everywhere.
- **Served by the same server:** `langgraph.json` `"http": {"app": ...}` mounts a Starlette app
  (checked in the installed langgraph-cli schema: a custom Starlette/FastAPI app). Pages are
  rendered server-side (Jinja2); the page posts JSON with `fetch()`.
- **Every control is a `ReviewAnswer`** the runner already checks: per-file checkboxes → approve
  "except"; Edit (wiki pages) → `edit`; task dropdowns → `tasks`; win checkboxes → `wins`; revise box
  → `revise`. Choices that change files (tasks, wins, edits) are sent at once and the page reloads
  with the new diffs, so Approve always approves exactly what is on screen.
- **Whole files first** (user chose); per-fact toggles later (code withdraws the fact and
  re-renders, no model call).
- **Remember is proposed in the review** (user chose): `/remember <rule>`, a Remember box, and an
  "also remember" tick on revise add a proposed change, approved with the rest, never written
  silently. Names/aliases become page `aliases:` edits (code); other rules a line in
  `90_meta/rules.md`, which both agents read as memory.
- **A review is tied to its vault:** the ingest thread stores its vault path; resuming it with a
  runner for another vault is refused (found while designing this: threads share one SQLite file
  and the vault came only from each run's context).
- Steps: R1 page (files, diffs, checkboxes, approve, reject) + the vault check; R2 edit, tasks,
  wins, revise; R3 remember.

## Why

The review is the one place the user sees and controls every write; it deserves a real UI. Keeping
the page a thin window over the same runner keeps every guarantee (digests, apply, read-back, git
commit) in one code path.
