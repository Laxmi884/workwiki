---
id: 0007
date: 2026-10-16
status: accepted
---

# 0007: Task → project mapping by model proposal

## Context

Decision 0002 rule 4 (model proposal, approved) was the last open 0006 item. `track_tasks` maps by
explicit link, inherited context and alias; everything else stays `project: null`. All 11 tasks of the
user's 2026-10-14 note ended up unmapped.

## Options

- Proposals in `tasks.json` by default, corrected via "answer": fewer clicks; the diff still shows
  exactly what is approved.
- Opt-in per proposal, like wins: safer, but an extra answer round on every note.
- A user "none" that sticks vs. nothing stored: storing it stops carried-over tasks from being
  proposed again every day.

## Decision

- **Staging tool `map_task(line, project, why)`**, checked by code. Refused when the line is not a
  citable task line; rules 1–3 or an earlier note already mapped the task (sticky, the model never
  overrides code); `project` is not an existing project or one proposed this run with `propose_page`
  (name-or-ask still applies); or rule 3 was ambiguous and the pick is outside its candidates.
  `project="none"` takes a proposal back; a second call for the same line replaces the first.
  `why` is a short phrase shown in the review, not stored.
- **Brief** marks unmapped tasks `project ?` (ambiguous: `project ? (one of a, b)`); the
  `people-and-projects` skill says to map only when the line, its neighbours or the project page
  support it, otherwise skip.
- **Code node `map_tasks`** after `stage` and after `pick_wins` folds proposals into the `tasks.json`
  edit (`update_task_index` override argument), `mapped_by: "model"`. `track_tasks` stays before
  `stage` (the brief needs it).
- **Review** lists "Task → project" items (line, text, project, why). Proposals are **in by default**;
  approving `tasks.json` approves exactly the mappings in it. `answer` with
  `tasks: {"m1": "<project slug>" | "none"}` corrects them; a user answer is `mapped_by: "user"`.
- **User "none" sticks:** `project: null, mapped_by: "user"`, so the task is not proposed again when
  it carries over. A later explicit `[[project-…]]` link still overrides any mapping.
- **Rejecting a new project page that mappings use** (approve with `reject`) is refused with an
  `error` asking to answer those tasks first, so `tasks.json` never points at an unwritten page.
- **Added after the first real run (2026-10-16, user agreed):** a task the agent mapped counts as
  naming that project, so its facts may go on the page (`staging.with_mappings`, in `file_fact` and
  `finalize`; never over code's own mapping). Without it, lines 8, 12, 13 of the 2026-10-14 note were
  mapped to delq analysis but their facts were dropped. A user "none" later leaves such a fact on the
  page; the page diff shows it.
- **Person pages named by tasks (2026-10-16, user chose):** a proposed person page with no fact is
  kept when the note's tasks name that person (line, parent or `### 1:1 with …` heading), and
  `tasks.json` links those tasks to it (proposed pages count as known entities in `map_tasks`).
  Rejecting such a page in an approve is refused ("keep it, or revise"), as for mapped projects.
  Before, the agent proposed Joanna and Eugene, filed nothing (their lines are all tasks), and code
  dropped both as empty.
- Out of scope: the generated "Open tasks" section on project pages (phase 7, with the brief).

## Why

Same pattern as the rest of 0006: the model proposes, code checks, the review shows the exact diff.
Default-in keeps reviews short; a sticky "none" keeps carried-over tasks from being asked daily.
