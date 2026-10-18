---
id: 0001
date: 2026-10-11
status: accepted
---

# 0001: Task IDs, matching, and daily-note rollover

## Context

Tasks recur across daily notes and get reworded. Matching them by text (the PDF's approach) breaks on any
edit. Each task needs a stable identity, the user must never type or remember IDs, and `00_raw/` is meant
to be human-written. The project may be open-sourced, so the format must not lock users into a plugin.

## Options

- **Obsidian block IDs** (`^t-xxxxxx`): core Obsidian, linkable, one regex to parse, hidden in Live
  Preview. No due dates or in-Obsidian queries.
- **Tasks plugin** (`🆔 xxxxxx` plus emoji fields): due dates, recurrence, completion dates, queries.
  Plugin dependency, harder to parse, more syntax for the model to get wrong.
- Who writes the ID: (1) ingest code, narrow approved exception to read-only `00_raw/`; (2) sidecar
  registry only, raw notes untouched, but identity breaks on rewording; (3) the user types IDs.

## Decision

**Format.** Block IDs: `t-` + 6 random base36 characters, e.g. `^t-7f3k2a`, at the end of the task line.
Random, not hashed (text and line positions change). Checked against the task index for collisions.
Compatible with the Tasks plugin, which accepts a trailing block ID, so users can add Tasks metadata later.

**Who writes.** The user never types IDs. Two code-only writes to `00_raw/daily/` are allowed:
1. *ID suffix:* the ingest compiler appends ` ^t-xxxxxx` to a task line. Goes through HITL approval; code
   verifies the diff adds only that suffix.
2. *Rollover (phase 7):* a morning job creates today's daily note **only if it does not exist**, from the
   daily template, with a `## Carried over` section holding open tasks copied from the index (IDs intact).
   No model text, so no approval. Never overwrites. Tasks open > 14 days move to a "Stale" list in the
   brief instead of being carried.

Model-written content (briefs, summaries, agendas) never goes into `00_raw/`; it goes to `05_generated/`,
so ingest never reads the agent's own output as human input. The assistant agent stays read-only on
`00_raw/`.

**Matching.**

| Situation | Match | LLM | Approval |
|---|---|---|---|
| New task line, no ID | Code generates ID | No | Yes (suffix write) |
| Reworded / copied / ticked, ID present | Exact ID | No | No |
| Retyped or prose mention, no ID | Code shortlists open tasks (shared links, word overlap, recent); model proposes a match | Yes | Yes. On approve, the **existing** ID is appended; "new task" gets a fresh one |

Prose without a checkbox is never edited; the index records the completion with a link to the line.

**Rules for phase 1.**
- The task index (`90_meta/task-index.json`) is a cache rebuilt from the notes; notes are the truth.
- The same ID may appear in many notes. If any occurrence is `[x]`, the task is done (done wins);
  completion date is the date of the note where it was first seen ticked.
- The ingest ledger re-processes a note whenever its hash changes, so edits to earlier days are picked up.
- All task syntax lives in one parser module (`tasks_syntax`); the domain `Task` model knows nothing about
  Markdown. Config has `task_ids: inline | sidecar`; only `inline` is built now.

## Why

Block IDs give exact, plugin-free identity that survives rewording, and rollover puts IDs in front of the
user every day, so fuzzy matching is reduced to rare, human-confirmed cases. Isolating syntax behind one
parser and one config switch keeps the door open for Tasks-plugin and no-write users if open-sourced.

## Addendum 2026-10-18: rollover as built

User's choices (2026-10-17, before building): `python -m llm_wiki.rollover` (`--vault`, `--date`, `--no-commit`).
- **Index behind the notes:** notes before the day that changed since their ingest, or were never ingested, are
  folded in by ID with ingest's `update_task_index` (in memory; `tasks.json` untouched), so a ticked task isn't
  carried. Their tasks without an ID are not carried; rollover warns "ingest it to carry them" (giving an ID here
  would give the task a second ID at that note's ingest).
- **Stale list:** printed by the CLI and returned as data until the morning brief exists.
- **Layout:** `### <project title>` per project, `### No project` last; `[/]` kept.
- **Commit:** the new note alone, by `llm-wiki`, `rollover: <date>`.
- **Ingest follow-up:** a task under `## Carried over` keeps the people it had (the copy lost its meeting
  heading); elsewhere people still follow the newest note.
