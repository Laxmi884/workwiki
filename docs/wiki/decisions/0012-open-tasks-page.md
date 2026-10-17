---
id: 0012
date: 2026-10-16
status: accepted
---

# 0012: One generated page of open tasks

## Context

The first real ingest (2026-10-14 note) created person and project pages that stay blank: they exist
so `90_meta/tasks.json` can link tasks to them (0007), but pages hold facts and tasks live only in
the index. Opening Joanna's page shows nothing, though three open tasks are hers; the note writes
"Joanna", not `[[person-joanna]]`, so Obsidian's backlinks don't connect them either. The user asked
where open tasks should be visible.

## Options

- A "## Open tasks" section on every person and project page: tasks where you look, but every
  status change rewrites several pages (a noisy review), code-owned text sits inside pages the user
  edits, and ticking a task in Obsidian leaves every page stale until the next ingest.
- One generated page: one file and one diff per ingest, wiki pages stay facts only; per-page view
  through Obsidian backlinks (the page links each person and project), or later a fixed heading
  embed on each page.
- Obsidian plugin queries (Dataview/Tasks): need a plugin and links on task lines; can't read the
  JSON index.

## Decision

- **`05_generated/open-tasks.md`, written by code from the task index** (no model). Open and
  in-progress tasks, grouped **by project** (then "No project") and **by person**; each line has the
  task text, its tags (e.g. `#blocked`), "since <first_seen>", and a block link to its line
  (`[[2026-10-14#^t-odle9f|note]]`, decision 0001). Projects and people are linked by slug with
  their page title.
- **Code-owned:** the whole file is rewritten each ingest, proposed in the review with the task
  index (under Bookkeeping, not editable there), approved and committed like any other file. Its
  content depends only on the index and page titles (no clock), so an unchanged index gives no diff.
- **Later, only if backlinks aren't enough:** one fixed line per entity page,
  `![[open-tasks#Joanna]]`, embedding that section live.

## Why

One page keeps every ingest's review small and the wiki pages free of generated text, while
Obsidian's backlinks still show each person's and project's tasks on their page. It is also the
starting point for the phase-7 morning brief.
