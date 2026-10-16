---
name: vault-map
description: Where things live in the user's work vault (folders, page kinds, sections, quarterly logs, the line format with date and source). Read it before searching the vault.
---

# Vault map

| Path | What | Written by |
|---|---|---|
| `/vault/00_raw/daily/YYYY-MM-DD.md` | the user's daily notes (Tasks, Meetings, Log, Helped, Learned) | the user |
| `/vault/10_projects/project-<name>.md` | one page per project | ingest |
| `/vault/15_concepts/concept-<name>.md` | things the user learned | ingest |
| `/vault/20_people/person-<name>.md` | one page per person | ingest |
| `/vault/30_decisions/<year>/decisions-<year>-q<n>.md` | the quarter's decisions | ingest |
| `/vault/40_impact/<year>/impact-<year>-q<n>.md` | the quarter's wins and help given | ingest |
| `/vault/90_meta/tasks.json` | every tracked task: use `find_tasks`, don't read it | code |
| `/vault/90_meta/rules.md` | the user's own rules for the agents | the user's reviews |

## Pages

Entity pages have YAML frontmatter (`type`, `title`, `aliases`) and `##` sections:
- `## Current state`: the latest status in a few lines, with an `_Updated <date> from
  [[note]]_` footer. Read this first for "where is X now".
- Projects: `## Updates`, `## Decisions`, `## Wins`; people: `## Notes`, `## Help given`;
  concepts: `## Notes`. The user's rules can add sections.

Filed lines look like:

    - 2026-10-12 · Atlas weekly sync: Beta moved to Oct 20. ([[2026-10-12]])

date · meeting (if any): the fact ([[the daily note it came from]]). The date and the
link are reliable (code wrote them); cite them.

Decision log entries are `### <title>` blocks under `## <date>` with When, Rationale and
Source lines.

## Finding things

- A name: `grep` for it under `/vault/` (titles and aliases are in frontmatter).
- A period: daily notes and log headings are dated; `glob` `/vault/00_raw/daily/2026-10-*.md`.
- Something not on any page: the raw daily notes are the source of truth.
