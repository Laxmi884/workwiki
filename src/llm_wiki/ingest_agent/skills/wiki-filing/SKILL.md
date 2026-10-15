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
| Project news: dates, scope, blockers, risks, owners, results | `project-…` | Updates |
| A decision (see decisions-and-wins) | `decision-log` (with `title`) | Decisions |
| …and on its project page too | `project-…` | Decisions |
| A win (see decisions-and-wins) | `impact` | Wins |
| …and on its project page too | `project-…` | Wins |
| Help the author gave (pairing, reviews, walkthroughs; keep minutes) | `impact` | Help given |
| …and on that person's page, if they have one | `person-…` | Help given |
| About a person: goals, wishes, worries, strengths, what they own | `person-…` | Notes |
| A learning about a concept | `concept-…` | Notes |

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
