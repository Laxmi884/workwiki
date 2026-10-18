---
phase: 7
date: 2026-10-18
tags: [jobs, determinism, vault]
---

# Code-only jobs: rollover

## What it is

A job that writes into the vault without a review, allowed because no model text is involved: it copies data
code already owns (task IDs and text from `tasks.json`) into a fixed place, only when the file is absent.
Anything a model writes still goes through the review (ingest) or into `05_generated/` (decision 0001).

## How we used it

- `src/llm_wiki/rollover.py`: pure half (`caught_up_index`, `split_open`, `render_carried`, `render_note`) and an
  I/O half (`roll_over`, `main`). `open(path, "x")` makes "only if absent" hold even if Obsidian creates the
  note at the same moment; the written note is parsed back and its carried IDs compared.
- The index is behind until you ingest: changed or never-ingested notes are folded in by ID with ingest's own
  `task_tracking.update_task_index`, in memory, so status rules can't drift between ingest and rollover.
- `task_tracking._merge`: a task under `## Carried over` keeps its people (the copy lost its meeting heading).
- `vault_git.commit_paths`: `Path.samefile` instead of comparing spellings (macOS paths ignore case).
- Notebook 8.1.

## Paid-media reference

`schedules/weekly_report.py` runs the agent on a cron prompt, so whatever it proposes still waits for approval.
Rollover runs no model; that difference is what lets it skip the review.

## Gotchas

- Copying a task out of its context changes what ingest infers from it (people from the `###` heading).
- Giving an ID-less task an ID in today's note would give it a second ID when yesterday's note is ingested.
- The real run's commit failed only because the scratch path was typed in another case than git prints it.

## In my words

