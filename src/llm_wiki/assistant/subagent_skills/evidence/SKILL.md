---
name: evidence
description: How the review-writer collects evidence for a period - which pages to read in which order, what counts, and how to fill the structured draft (one sourced item per accomplishment, metrics verbatim, gaps). Read before collecting.
---

# Collecting evidence for a period

## Where to look, in order

1. The impact pages for the quarters in the period:
   `/vault/40_impact/<year>/impact-<year>-q<n>.md` (`## Wins`, `## Help given`). These
   are the user's chosen wins: always include them.
2. Project pages (`/vault/10_projects/`): `## Wins`, `## Decisions`, `## Updates` lines
   dated in the period, and `## Current state` for where each project ended up.
3. Decision logs: `/vault/30_decisions/<year>/decisions-<year>-q<n>.md`.
4. Person pages' `## Help given` for help to others.
5. `find_tasks(status="done", since=...)` only to spot projects with finished work but
   no wins; list those under gaps, not as wins.

## Filling the draft

- One `BragItem` per accomplishment. `text` in the vault's words; `date` from the
  line; `source` = the `[[...]]` link at the end of the line (the daily note), else the
  page you read it on.
- `metric`: a number or outcome the line states, copied exactly ("4s → 1.5s",
  "3 teams onboarded"). None if the line has none. Never estimate.
- Same accomplishment on two pages (impact and project): one item, the impact line's
  source.
- Group by `project` slug; items with no project go in a section with `project` None.
- `gaps`: wins without any metric, projects with updates but no wins, months in the
  period with nothing recorded.
- Lines outside the period are left out.
