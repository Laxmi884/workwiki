---
id: 0002
date: 2026-10-11
status: accepted
---

# 0002: Daily note format and task-to-project mapping

## Context

Ingest has to turn free-form daily notes into events reliably, and the assistant's skills need one
description of vault conventions. No standard daily-note format is designed for agent parsing
(Bullet Journal, interstitial journaling and Logseq journals solve the human side only). Too much
structure and the user stops filling it in; too little and the model guesses.

## Options

- Free-form notes, model extracts everything: least friction, least reliable.
- Strict form (required fields per entry): most reliable, abandoned within a week.
- Fixed section headings + links + a few optional tags, forgiving parser: chosen.

## Decision

**Template** (`~/Work/50_templates/daily.md`, `template: daily-v1`):

```markdown
---
type: daily
template: daily-v1
date: {{date:YYYY-MM-DD}}
---

## Carried over
## Tasks
## Meetings      (one ### per meeting: topic + [[person-…]] / [[project-…]] links)
## Log
## Helped
## Learned
```

**Section routing.** Code splits the note on `##` headings; each section goes to one extractor.

| Section | Feeds |
|---|---|
| Carried over | Task status updates (filled by rollover, see 0001) |
| Tasks | New tasks (IDs per 0001) |
| Meetings | `30_decisions/`, people pages, tasks with meeting context |
| Log | Project pages; `#win` → `40_impact/` |
| Helped | People pages, brag sheet |
| Learned | `15_concepts/` |

**Tags** (optional, detected by code, no LLM): `#win` → impact/brag sheet, `#decision` → `30_decisions/`,
`#blocked` → morning brief. Untagged text is still extracted by the model.

**Forgiving parser rules.**
- Sections optional, any order. Unknown `##` headings are treated as Log.
- A checkbox anywhere is a task; the section/heading it sits under is its context.
- `template:` in frontmatter selects the parser version; old notes keep parsing after the template changes.
- Notes without frontmatter are treated as `daily-v1` (date from filename).

**Task → project mapping.** First match wins:

1. Explicit `[[project-…]]` link on the task line.
2. Inherited context: the enclosing `###` meeting heading or parent bullet links a project.
3. Alias match: a project's frontmatter `aliases` appear in the task text. Two or more matches → step 4
   with those candidates.
4. Model proposal from active projects (name, aliases, one-line description): existing project, none,
   or new project. Requires approval; a new project is a new file, also approved.
5. None is allowed (`project = None`); the morning brief lists unassigned tasks.

The mapping is stored in the task index and the project page's generated "Open tasks" section, never
in `00_raw/`. It is sticky per task ID (no re-mapping on reword or copy). An explicit link in a later
note overrides any earlier mapping. One project per task; other project links are kept as mentions.

## Why

Headings and links let code do the routing and entity resolution, so the model only extracts meaning
within a known section. Forgiving rules and optional tags keep the note cheap to write. The same
contract drives the phase-1 parser, the phase-2 extractors and the phase-4 assistant skills.
