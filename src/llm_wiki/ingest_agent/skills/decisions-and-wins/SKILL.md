---
name: decisions-and-wins
description: What counts as a decision, how wins are handled (only #win lines; the user picks the rest), and where help given goes. Read when the note has #decision or #win tags, or words like decided, agreed, shipped, fixed, passed, helped.
---

# Decisions and wins

## Decisions

A decision is a choice that was made: "we decided", "agreed", "reversed", "go/no-go:
GO", "we'll try X first". File it on `decision-log` with a short `title` (the choice in a
few words) and the reason in `text` ("Not given in the note." if there is none), and as a
line under Decisions on its project page.

- A date that moved *because someone chose to move it* is a decision; a date that is just
  reported is a project update.
- A reversal is a new decision: say what it reverses.

## Wins (impact)

The user decides what counts as a win, not you.

- File a win only for a line tagged `#win`: on `impact` under Wins, and on its project
  page under Wins. Code refuses Wins for untagged lines.
- Finished tasks are offered to the user as win candidates in the review by code. Don't
  file them as wins; file the finished work as an Update on its project page.
- Keep the numbers: "cut p95 from 1.8s to 1.1s", "14 min to 6 min". Never invent a metric.

## Help given

Help the author gave someone goes on that person's page under Help given, with the
minutes if the note gives them ("(1h 30m)" → 90 min); if it was project work, also as an
Update on the project page. Not on `impact`. Someone without a page: propose a person
page if the note names a human, or ask.
