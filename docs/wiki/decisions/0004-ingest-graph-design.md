---
id: 0004
date: 2026-10-12
status: accepted
---

# 0004: Ingest graph design: code around a narrow model step, edits → diffs, review loop

## Context

Phase 2 turns one daily note into proposed vault changes. The model must stay narrow (read prose only),
every write must be checkable and approvable, and the user must be able to correct missed or wrong
extractions, not only approve or reject.

## Options

- One agent with file tools reads the note and edits pages: flexible, but unpredictable steps and hard
  to review.
- Fixed graph: code parses and routes, model extracts structured facts per section, code renders exact
  diffs; review step with approve / reject / edit / revise-with-feedback: chosen.

## Decision

- Nodes: `load_note` → `assign_ids` → `track_tasks` → fan-out (`Send`, one per prose section) →
  `extract` → `route` → `render_diffs` → (phase 3) `review` (`interrupt`) → `apply`.
- Model step: `SectionFacts` (decisions, wins, person notes, help, learnings, project updates), each
  fact citing its `line`. Names may be slugs or plain names; code resolves them. Tasks are code only.
- State: `facts` is a dict keyed per section with a merge-by-key reducer (parallel-safe, a re-run
  replaces its own key); `route` rebuilds `edits` from all facts each round; `feedback` holds the
  user's revise messages.
- Writes: nodes emit small `Edit`s (`AppendId`, `AppendToSection`, `CreateFile`); `render_diffs` merges
  them per file into one `FileDiff` (before sha256, after text, unified diff, digest). Allowed folders
  are part of each edit's type.
- Review answers (phase 3): approve, approve some, reject, edit (user text replaces a diff),
  revise (feedback → re-extract with previous facts + feedback, max 3 rounds).
- Task index: code-owned `90_meta/tasks.json`. Mapping rules 1–3 in v1; rule 4 (model proposal) later.
- Every model-written line links its source note, e.g. `([[2026-10-12]])`. Facts not in the note go
  into the note, not into feedback.
- Task records keep `people` (person links) and `tags` from the task line (added 2026-10-13), so
  tasks without a project can be found by person or `#blocked`.
- People clarification (added 2026-10-13, built in 2d/2e/3): unlinked names (alias matches by code,
  unknown names from the model, ambiguous matches) become questions in the same review `interrupt`:
  this person / other existing person / new person / not a person. `00_raw/` stays untouched; the
  answer goes into `tasks.json` and facts. Confirming a name adds it as an alias on the person page
  automatically, so it is not asked again. Names resolved by alias are still listed in the review
  (shown, not asked) so a wrong match can be changed. A name that becomes an alias of two people is
  ambiguous and asked each time; a `[[link]]` in the note always wins without a question.
  Later: the model may use context (e.g. "Sam" in the Atlas sync → whoever is on Atlas) to
  pre-select an answer, never to skip the question (same rule as mapping rule 4).
- Fact context comes from code, not the model (added 2026-10-13, built in 2e): from a fact's
  `line`, code adds the note date, the meeting (`###` heading above the line), who was there
  (links in that heading) and a link to the exact line. The model's own fields keep the detail the
  note gives (names, dates, numbers, reasons; one to three sentences).

## Why

Deterministic work stays testable code, the model only does what code cannot (read prose), and every
change is an exact, hashed diff the user can approve, edit or send back with feedback.
