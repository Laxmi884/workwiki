---
name: wiki-filing
description: Where each kind of fact from a daily note goes in the work wiki (updates, decisions, wins, help given, person notes, learnings), section names, and how to write a fact. Read before filing anything.
---

# Filing facts

One `file_fact` call per fact and page: target, section, 1-2 sentences, the note line.
Code adds the date, the meeting and a link to the note; don't write those.

## Where facts go

| Fact | target | section |
|---|---|---|
| Project news: dates, scope, blockers, risks, owners, results, finished work | `project-…` | Updates |
| A decision (see decisions-and-wins) | `decision-log` (with `title`) | Decisions |
| …and on its project page too | `project-…` | Decisions |
| A line tagged `#win` (only those) | `impact` | Wins |
| …and on its project page too | `project-…` | Wins |
| Help the author gave (pairing, reviews, walkthroughs; keep minutes) | `person-…` | Help given |
| …and on the project page, if it was project work | `project-…` | Updates |
| About a person: goals, wishes, worries, strengths, what they own | `person-…` | Notes |
| A learning about a concept | `concept-…` | Notes |
| A one-off task that belongs to no project (ad hoc) | nothing | (it stays in the note and the task list) |

Which project a line belongs to: see people-and-projects. Code refuses a fact on an
existing project page when the line, its parent bullet and its heading don't name the
project. Don't guess: propose the project the note names, ask, or skip an ad-hoc task.

The user's rules can add sections (e.g. "## 1:1 notes", "## Customer asks"): use exactly
the heading the rule names. A fact about two things (Marta's PgBouncer work on Atlas) can
go on both pages.

If a fact changes what is true now on a project (a date moved, a status or owner
changed), also rewrite its Current state (see page-upkeep).

## Writing a fact

- Use only what the line says, plus its parent bullet or heading for context.
- Keep names, dates, numbers and reasons; never add any.
- Link a page as `[[slug]]` when you mention it; write other names plainly.
- One fact per line per page. Skip facts the page already has.
