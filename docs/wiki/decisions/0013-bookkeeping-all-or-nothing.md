---
id: 0013
date: 2026-10-17
status: accepted
---

# 0013: Bookkeeping files are written all or nothing

## Context

Writing the key for notebook 5.3 (then 18) showed that leaving out `90_meta/tasks.json` in a review, while
keeping the open-tasks page or the ingest ledger, leaves the vault inconsistent; the ledger then
skips the note, so its task changes are lost until it is edited. The same holds for each of the four
files that describe one ingest:

| Left out alone | What breaks |
|---|---|
| the note (task IDs) | the index points at IDs the note doesn't have; block links break |
| `90_meta/tasks.json` | open-tasks describes another index; the note's task changes are lost |
| `05_generated/open-tasks.md` | stale until the next ingest |
| `90_meta/ingest-ledger.json` | the next ingest re-reads the note and files written facts again |

## Options

- Guard one pair (tasks.json with open-tasks.md): the other combinations stay broken.
- Bookkeeping all or nothing: no partial choice among the four is ever consistent, so don't offer one.

## Decision

- **The four bookkeeping files are always written with an approval** (`is_bookkeeping` in
  `domain/changes.py`, `ReviewFile.required`). `check_answer` refuses `approve` with one of them in
  `reject`, with the reason; the page shows them as a fixed list (no Include box); the chat marks
  them "always written".
- Wiki pages are still chosen one by one. Approving with every wiki page left out writes only the
  bookkeeping: the note is marked ingested with its tasks tracked, which is consistent.
- Not wanting task IDs in the note means rejecting the review: tracking runs on them (0001).

## Why

Every partial choice among these files breaks one of the others; refusing it in code keeps the page,
the chat and Studio under one rule.
